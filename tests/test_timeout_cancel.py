"""Ventana invisible, *timeout* y cancelación, y política de residuos.

El pedido es literal: *"la ventana de en la terminal cuando se hace
la descarga, debe ser un proceso invisible"*. Y el plan agrega que hoy no hay ni
``timeout`` ni forma de cancelar, así que un cuelgue de red deja la app sin
salida. Este archivo mide las tres cosas en **tres capas**, a propósito:

1. **Proceso espiado** (``subprocess.Popen`` sustituido, sin crear nada):
   verifica las banderas de arranque en las tres corridas. **Es el test que
   falla si alguien quita ``CREATE_NO_WINDOW``.**
2. **Proceso real** (``_ejecutar`` sobre un guion dormilón): mide que el reloj
   vence de verdad, que el proceso muere y que **el árbol** muere con él — un
   hijo que sobrevive es el modo en que fallan las implementaciones naive.
3. **Binario real** (``bin\\yt-dlp.exe`` contra un servidor HTTP local que
   responde lento): mide *timeout* y cancelación de punta a punta, con red
   nula y ``tasklist`` como testigo de que no queda nada vivo.

Lo que **no** se mide acá: el botón Cancelar de la GUI (se prueba en la capa
de interfaz). Este archivo fija la API que la GUI va a consumir.
"""

from __future__ import annotations

import http.server
import io
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from descargador import core
from descargador.config import Config, Opciones, cargar
from descargador.core import (
    HINT_TIMEOUT,
    MENSAJE_CANCELADO,
    Resultado,
    borrar_original_si_es_seguro,
    download,
    mensaje_timeout,
)
from descargador.engine import (
    FLAGS_SIN_VENTANA,
    CanceladoError,
    MotorError,
    RUTA_BINARIO,
    TimeoutDescargaError,
    YtdlpEngine,
    _ejecutar,
)
from descargador.verify import streams_de

ES_WINDOWS = sys.platform == "win32"
REQUIERE_WINDOWS = pytest.mark.skipif(
    not ES_WINDOWS, reason="las banderas de ventana oculta son de Windows"
)
REQUIERE_BINARIO = pytest.mark.skipif(
    not RUTA_BINARIO.exists(), reason=f"no existe el binario: {RUTA_BINARIO}"
)


# --------------------------------------------------------------- utilidades


class _ProcFalso:
    """Doble de ``Popen``: no crea nada, sólo entrega un stdout prefijado."""

    def __init__(self, stdout: str = "", codigo: int = 0) -> None:
        self.pid = 4242
        self._codigo = codigo
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO("")

    def poll(self) -> int:
        return self._codigo

    def wait(self, timeout: float | None = None) -> int:
        return self._codigo


class _PopenEspia:
    """Sustituye a ``subprocess.Popen``: registra las banderas y no ejecuta."""

    def __init__(self, stdouts: tuple[str, ...]) -> None:
        self._stdouts = stdouts
        self.llamadas: list[dict] = []

    def __call__(self, argv, **kwargs):
        indice = min(len(self.llamadas), len(self._stdouts) - 1)
        self.llamadas.append({"argv": list(argv), **kwargs})
        return _ProcFalso(self._stdouts[indice])


#: Guion que late en un archivo y, si se le pide, cría un hijo que también late.
#: Si el árbol no se mata, los latidos siguen creciendo y el test falla.
GUION_DORMILON = '''
import os, subprocess, sys, time

latido = os.environ["DUMMY_LATIDO"]
duracion = float(os.environ.get("DUMMY_DURACION", "30"))
hijo = None
if os.environ.get("DUMMY_CON_HIJO") == "1":
    env = dict(os.environ, DUMMY_CON_HIJO="0", DUMMY_LATIDO=os.environ["DUMMY_LATIDO_HIJO"])
    hijo = subprocess.Popen([sys.executable, __file__], env=env)

fin = time.time() + duracion
try:
    while time.time() < fin:
        with open(latido, "a", encoding="utf-8") as f:
            f.write(".")
        time.sleep(0.1)
finally:
    if hijo is not None:
        hijo.kill()
'''


def _guion(tmp_path: Path) -> Path:
    ruta = tmp_path / "dormilon.py"
    ruta.write_text(GUION_DORMILON, encoding="utf-8")
    return ruta


def _latidos(ruta: Path) -> int:
    """Cantidad de latidos escritos hasta ahora (0 si el archivo no existe)."""
    return len(ruta.read_text(encoding="utf-8")) if ruta.exists() else 0


def _pids_yt_dlp() -> list[int]:
    """PIDs vivos de ``yt-dlp.exe`` según ``tasklist`` (0 si no queda ninguno)."""
    proc = subprocess.run(
        ["tasklist", "/NH", "/FO", "CSV", "/FI", "IMAGENAME eq yt-dlp.exe"],
        capture_output=True,
        text=True,
        # tasklist escribe en la codepage de la consola (cp850 en esta máquina):
        # sin errors="replace", el mensaje local ("Información: ...") revienta
        # el hilo lector de subprocess y stdout llega None.
        errors="replace",
        **FLAGS_SIN_VENTANA,
    )
    pids: list[int] = []
    assert proc.stdout is not None, "tasklist no devolvió salida: el test sería vacuo"
    for linea in proc.stdout.splitlines():
        partes = [p.strip('"') for p in linea.split('","')]
        if len(partes) >= 2 and partes[0].lower() == "yt-dlp.exe":
            try:
                pids.append(int(partes[1]))
            except ValueError:
                continue
    return pids


class MotorDoble:
    """Doble de ``YtdlpEngine``: copia un MP4 real al destino o falla.

    Su firma es la que el núcleo llama: si el núcleo agrega un parámetro que
    este doble no acepta, el test revienta por ``TypeError``. Esa es la
    verificación de firma de la que habla el rol, no una comodidad.
    """

    def __init__(self, entregar: Path | None = None, destino: Path | None = None, error: str | None = None):
        self.entregar = entregar
        self.destino = destino
        self.error = error
        self.timeout_seg: float | None = None
        self.cancel: threading.Event | None = None
        self.llamadas = 0

    def version(self) -> str:
        return "2026.08.19"

    def inspect(self, url: str, cookies=None) -> dict:
        return {"id": "doble"}

    def download(
        self,
        url,
        plataforma,
        argv_extra=None,
        cookies=None,
        on_progress=None,
        timeout_seg=None,
        cancel=None,
    ) -> Path:
        self.llamadas += 1
        self.timeout_seg = timeout_seg
        self.cancel = cancel
        if self.error is not None:
            raise MotorError(self.error, codigo=1, stderr=self.error)
        assert self.destino is not None and self.entregar is not None
        copia = self.destino / self.entregar.name
        copia.write_bytes(self.entregar.read_bytes())
        return copia


def _cfg(tmp_path: Path, **kwargs) -> Config:
    return Config(output_dir=str(tmp_path / "downloads"), **kwargs)


# ------------------------------------------------- 1. proceso sin ventana


@REQUIERE_WINDOWS
def test_las_tres_corridas_arrancan_sin_ventana_de_consola(monkeypatch):
    """**Falla si se quita la bandera.**

    Medido sobre un ``Popen`` espiado (no se crea ningún proceso) en las tres
    corridas del motor: descarga, inspección y versión.
    """
    espia = _PopenEspia(("C:\\salida\\video.mp4\n", '{"id": "abc"}', "2026.08.19\n"))
    monkeypatch.setattr(subprocess, "Popen", espia)
    motor = YtdlpEngine(binario=Path("bin/yt-dlp.exe"))  # runner real: _ejecutar

    motor.download("https://youtu.be/abc", "youtube", argv_extra=["-o", "x"])
    motor.inspect("https://youtu.be/abc")
    motor.version()

    assert len(espia.llamadas) == 3, f"se esperaban 3 corridas, hubo {len(espia.llamadas)}"
    for indice, llamada in enumerate(espia.llamadas):
        assert llamada["creationflags"] & subprocess.CREATE_NO_WINDOW, (
            f"corrida {indice}: sin CREATE_NO_WINDOW → Windows abre una consola"
        )
        info = llamada["startupinfo"]
        assert info.dwFlags & subprocess.STARTF_USESHOWWINDOW, (
            f"corrida {indice}: STARTUPINFO sin STARTF_USESHOWWINDOW"
        )
        assert info.wShowWindow == 0, f"corrida {indice}: wShowWindow no oculta la ventana"


@REQUIERE_WINDOWS
@REQUIERE_BINARIO
def test_el_binario_real_tambien_arranca_sin_ventana(monkeypatch):
    """Misma medición, ahora sobre ``bin\\yt-dlp.exe`` de verdad."""
    real = subprocess.Popen
    capturadas: list[dict] = []

    def espia(argv, **kwargs):
        capturadas.append(dict(kwargs))
        return real(argv, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", espia)
    version = YtdlpEngine().version()

    assert version.strip(), "el binario real no reportó versión: el test sería vacuo"
    assert capturadas, "no se registró ningún arranque: el espía está mal puesto"
    for indice, kwargs in enumerate(capturadas):
        assert kwargs.get("creationflags", 0) & subprocess.CREATE_NO_WINDOW, (
            f"arranque {indice}: el binario real se lanzó CON ventana"
        )


# ------------------------------------------------------- 2. timeout real


def test_el_reloj_vence_y_el_proceso_dummy_muere(tmp_path, monkeypatch):
    """El *timeout* venza de verdad: proceso real, sin red, sin doble de motor."""
    guion = _guion(tmp_path)
    latido = tmp_path / "latido.txt"
    monkeypatch.setenv("DUMMY_LATIDO", str(latido))
    monkeypatch.setenv("DUMMY_DURACION", "30")

    with pytest.raises(TimeoutDescargaError) as excinfo:
        _ejecutar([sys.executable, str(guion)], lambda linea: None, timeout_seg=2.0)

    assert "tiempo de espera" in str(excinfo.value), f"mensaje no accionable: {excinfo.value}"
    assert _latidos(latido) > 0, "el dummy nunca latió: el test sería vacuo"
    marca = _latidos(latido)
    time.sleep(1.2)
    assert _latidos(latido) == marca, "el proceso sigue vivo tras vencer el reloj"


def test_sin_timeout_no_hay_limite_y_el_proceso_termina_solo(tmp_path, monkeypatch):
    """Compatibilidad: ``timeout_seg=None`` sigue siendo "sin límite"."""
    guion = _guion(tmp_path)
    monkeypatch.setenv("DUMMY_LATIDO", str(tmp_path / "latido.txt"))
    monkeypatch.setenv("DUMMY_DURACION", "0.4")

    salida = _ejecutar([sys.executable, str(guion)], lambda linea: None)

    assert salida.codigo == 0


def test_core_informa_el_timeout_con_un_mensaje_accionable(tmp_path, runner):
    """El *timeout* del motor llega a la GUI como error, no como silencio."""
    motor = YtdlpEngine(binario="yt-dlp", runner=runner(stdout="video.mp4\n", demora=5.0))
    cfg = _cfg(tmp_path, timeout_segundos=1)
    inicio = time.monotonic()

    res = download("https://www.tiktok.com/@u/video/1", Opciones(), cfg, motor)

    assert time.monotonic() - inicio < 15, "el timeout no cortó: la corrida siguió"
    assert res.ok is False
    assert res.archivo is None
    assert res.error is not None and "tiempo de espera" in res.error
    assert HINT_TIMEOUT in res.error, f"el error no dice qué hacer: {res.error}"


def test_el_timeout_de_core_sale_de_la_configuracion(tmp_path, runner):
    """Si la GUI no pasa ``timeout_seg``, manda ``config.json``."""
    motor = YtdlpEngine(binario="yt-dlp", runner=runner(stdout="video.mp4\n", demora=5.0))
    cfg = _cfg(tmp_path, timeout_segundos=1)

    res = download("https://youtu.be/abc", Opciones(), cfg, motor)

    assert res.ok is False
    assert motor is not None
    assert res.error is not None and "tiempo de espera" in res.error


def test_mensaje_timeout_nombra_los_minutos_configurados():
    """El texto que verá el usuario: 1800 s ⇒ "30 minutos", no un número crudo."""
    assert "30 minutos" in mensaje_timeout(1800)
    assert "1 segundo" in mensaje_timeout(1)
    assert "2 segundos" in mensaje_timeout(2)


# --------------------------------------------------- 3. cancelación real


def test_cancelar_mata_el_arbol_y_no_deja_hijos_latiendo(tmp_path, monkeypatch):
    """Punto que hace fallar a las implementaciones naive: **sólo el padre**.

    El dummy cría un hijo. Si la cancelación mata únicamente a ``yt-dlp.exe``,
    el hijo sigue escribiendo latidos y este test falla.
    """
    guion = _guion(tmp_path)
    latido_padre = tmp_path / "padre.txt"
    latido_hijo = tmp_path / "hijo.txt"
    monkeypatch.setenv("DUMMY_LATIDO", str(latido_padre))
    monkeypatch.setenv("DUMMY_LATIDO_HIJO", str(latido_hijo))
    monkeypatch.setenv("DUMMY_CON_HIJO", "1")
    monkeypatch.setenv("DUMMY_DURACION", "30")

    cancel = threading.Event()
    threading.Thread(target=lambda: (time.sleep(0.8), cancel.set()), daemon=True).start()

    inicio = time.monotonic()
    with pytest.raises(CanceladoError):
        _ejecutar([sys.executable, str(guion)], lambda linea: None, cancel=cancel)
    transcurrido = time.monotonic() - inicio

    assert transcurrido < 15, f"la cancelación tardó {transcurrido:.1f} s: no es una cancelación"
    assert _latidos(latido_hijo) > 0, "el hijo nunca latió: el test sería vacuo"
    marca_padre, marca_hijo = _latidos(latido_padre), _latidos(latido_hijo)
    time.sleep(1.2)
    assert _latidos(latido_padre) == marca_padre, "el padre sobrevivió a la cancelación"
    assert _latidos(latido_hijo) == marca_hijo, (
        "sobrevivió un hijo: sólo se mató al padre (árbol incompleto)"
    )


def test_cancelar_antes_de_arrancar_no_crea_el_proceso(monkeypatch):
    """Si el usuario cancela antes de que el hilo despegue, no se lanza nada."""
    espia = _PopenEspia(("x",))
    monkeypatch.setattr(subprocess, "Popen", espia)
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(CanceladoError):
        _ejecutar(["no_importa"], lambda linea: None, cancel=cancel)

    assert espia.llamadas == [], "se lanzó un proceso que ya estaba cancelado"


def test_core_devuelve_error_al_cancelar_y_no_deja_el_part_huerfano(tmp_path, runner):
    """Al cancelar: error literal, sin archivo y sin ``.part`` tirado (D12/D13)."""
    salida = tmp_path / "downloads"
    salida.mkdir()
    incompleto = salida / "clip.mp4.part"
    incompleto.write_bytes(b"basura")

    cancel = threading.Event()
    threading.Thread(target=lambda: (time.sleep(0.2), cancel.set()), daemon=True).start()
    motor = YtdlpEngine(binario="yt-dlp", runner=runner(stdout="clip.mp4\n", demora=5.0))

    res = download("https://www.tiktok.com/@u/video/1", Opciones(), _cfg(tmp_path), motor, cancel=cancel)

    assert res.ok is False, "una descarga cancelada no puede reportar éxito"
    assert res.archivo is None
    assert res.error == MENSAJE_CANCELADO
    assert incompleto.exists() is False, (
        f"quedó un .part huérfano: {[p.name for p in salida.iterdir()]}"
    )
    assert res.residuos == (incompleto,), f"el resultado no declara qué limpió: {res.residuos}"


def test_con_la_limpieza_desactivada_el_part_se_conserva_y_se_nombra(tmp_path, runner):
    """Contraparte: ``limpiar_part_al_cancelar=false`` conserva y lo declara."""
    salida = tmp_path / "downloads"
    salida.mkdir()
    incompleto = salida / "clip.mp4.part"
    incompleto.write_bytes(b"basura")

    cancel = threading.Event()
    threading.Thread(target=lambda: (time.sleep(0.2), cancel.set()), daemon=True).start()
    motor = YtdlpEngine(binario="yt-dlp", runner=runner(stdout="clip.mp4\n", demora=5.0))
    cfg = _cfg(tmp_path, limpiar_part_al_cancelar=False)

    res = download("https://youtu.be/abc", Opciones(), cfg, motor, cancel=cancel)

    assert res.ok is False
    assert incompleto.exists(), "se borró un .part que la configuración manda conservar"
    assert res.residuos == (incompleto,)
    assert res.aviso is not None and incompleto.name in res.aviso


def test_un_fallo_del_motor_no_se_disfraza_de_cancelacion(tmp_path, runner):
    """Un error del motor sigue siendo un error del motor, con su stderr."""
    motor = YtdlpEngine(binario="yt-dlp", runner=runner(codigo=1, stderr="ERROR: Unsupported URL\n"))

    res = download("https://www.tiktok.com/@u/video/1", Opciones(), _cfg(tmp_path), motor)

    assert res.ok is False
    assert "Unsupported URL" in (res.error or "")
    assert MENSAJE_CANCELADO != (res.error or "")
    assert "tiempo de espera" not in (res.error or "")


def test_una_descarga_normal_sigue_siendo_exitosa_con_timeout_y_cancel(tmp_path, fabricar_mp4, runner):
    """Caso de éxito: con reloj y cancelación disponibles, nada cambia."""
    salida = tmp_path / "downloads"
    salida.mkdir()
    entregar = fabricar_mp4("ok.mp4", audio=True)
    motor = YtdlpEngine(binario="yt-dlp", runner=runner(stdout=f"{entregar}\n", demora=0.0))

    res = download(
        "https://youtu.be/abc",
        Opciones(),
        _cfg(tmp_path),
        motor,
        timeout_seg=30,
        cancel=threading.Event(),
    )

    assert res.ok is True, f"se rompió el camino bueno: {res.error}"
    assert res.archivo is not None and res.archivo.exists()
    assert res.aviso is None


# ------------------------------------------- 4. binario real: timeout/cancel


class _HandlerLento(http.server.SimpleHTTPRequestHandler):
    """Sirve el directorio, pero dormido: el cliente se queda esperando."""

    def send_head(self):
        time.sleep(20)
        return super().send_head()

    def log_message(self, *args, **kwargs) -> None:
        return


class _ServidorLento(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address) -> None:
        return  # el cliente se fue (lo matamos): no es un error del proyecto


@pytest.fixture
def servidor_lento(tmp_path):
    """HTTP local que tarda 20 s en responder: cuelgue de red reproducible."""
    fabrica = _HandlerLento
    fabrica.directory = str(tmp_path)  # type: ignore[attr-defined]
    httpd = _ServidorLento(("127.0.0.1", 0), fabrica)
    hilo = threading.Thread(target=httpd.serve_forever, daemon=True)
    hilo.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        hilo.join(timeout=5)


@REQUIERE_WINDOWS
@REQUIERE_BINARIO
def test_timeout_punta_a_punta_con_el_binario_real(servidor_lento, tmp_path, fabricar_mp4):
    """yt-dlp real colgado contra un servidor lento: vence el reloj y muere."""
    clip = fabricar_mp4("lento.mp4", duracion=1.0)
    destino = tmp_path / "salida"
    destino.mkdir()
    motor = YtdlpEngine()

    with pytest.raises(TimeoutDescargaError):
        motor.download(
            f"{servidor_lento}/{clip.name}",
            "tiktok",
            argv_extra=["-o", str(destino / "%(title)s.%(ext)s")],
            timeout_seg=2.0,
        )

    assert _pids_yt_dlp() == [], f"quedó yt-dlp.exe vivo tras el timeout: {_pids_yt_dlp()}"


@REQUIERE_WINDOWS
@REQUIERE_BINARIO
def test_cancelacion_punta_a_punta_con_el_binario_real(
    servidor_lento, tmp_path, fabricar_mp4, monkeypatch
):
    """Lo mismo, cancelando: el resultado es un error, nunca un éxito falso."""
    monkeypatch.setattr(core, "detectar", lambda _url: "tiktok")
    clip = fabricar_mp4("lento2.mp4", duracion=1.0)
    cancel = threading.Event()
    threading.Thread(target=lambda: (time.sleep(0.8), cancel.set()), daemon=True).start()

    res = download(
        f"{servidor_lento}/{clip.name}",
        Opciones(),
        _cfg(tmp_path),
        YtdlpEngine(),
        cancel=cancel,
        timeout_seg=60,
    )

    assert res.ok is False, "una cancelación no puede reportar éxito"
    assert res.error == MENSAJE_CANCELADO
    assert res.archivo is None
    assert _pids_yt_dlp() == [], f"quedó yt-dlp.exe vivo tras cancelar: {_pids_yt_dlp()}"
    assert not list((tmp_path / "downloads").glob("*.part")), "quedó un .part huérfano"


# ------------------------------------------------------ 5. residuos (D13)


def test_por_defecto_el_original_sobrevive_a_la_recodificacion(tmp_path, fabricar_mp4):
    """D13: el HEVC es la copia de máxima calidad; no se borra sin permiso."""
    salida = tmp_path / "downloads"
    salida.mkdir()
    entregar = fabricar_mp4("tt_hevc.mp4", vcodec="libx265", audio=True)
    motor = MotorDoble(entregar=entregar, destino=salida)
    cfg = _cfg(tmp_path, codec_policy={"youtube": "nativo", "instagram": "nativo", "tiktok": "h264"})

    res = download("https://www.tiktok.com/@u/video/1", Opciones(), cfg, motor)

    assert res.ok is True
    original = salida / "tt_hevc.mp4"
    assert original.exists(), "se borró el original sin autorización (D13 violada)"
    assert res.original == original, "el resultado no declara el original conservado"
    assert res.archivo is not None and res.archivo.name == "tt_hevc_h264.mp4"
    assert res.aviso is None


def test_con_limpieza_activada_el_original_se_borra_si_el_reemplazo_es_h264(tmp_path, fabricar_mp4):
    """Con el permiso puesto y el reemplazo verificado, el original se borra."""
    salida = tmp_path / "downloads"
    salida.mkdir()
    entregar = fabricar_mp4("tt2.mp4", vcodec="libx265", audio=True)
    motor = MotorDoble(entregar=entregar, destino=salida)
    cfg = _cfg(
        tmp_path,
        codec_policy={"youtube": "nativo", "instagram": "nativo", "tiktok": "h264"},
        limpiar_original_tras_recodificar=True,
    )

    res = download("https://www.tiktok.com/@u/video/1", Opciones(), cfg, motor)

    assert res.ok is True
    assert (salida / "tt2.mp4").exists() is False, "no se borró el original pese al permiso"
    assert res.archivo is not None and res.archivo.exists()
    assert streams_de(res.archivo)[1] == "h264"
    assert res.aviso is not None and "original" in res.aviso


def test_no_se_borra_el_original_si_el_reemplazo_no_es_h264(tmp_path, fabricar_mp4):
    """Guarda: si ffprobe no confirma h264, el original se queda."""
    salida = tmp_path / "downloads"
    salida.mkdir()
    original = salida / "origen.mp4"
    original.write_bytes(b"x" * 512)
    reemplazo = salida / "origen_h264.mp4"
    reemplazo.write_bytes(b"y" * 512)

    borrado, motivo = borrar_original_si_es_seguro(
        original, reemplazo, verificador=lambda _ruta: (2, "mpeg4", "aac")
    )

    assert borrado is False
    assert original.exists(), "borró el original con un reemplazo que no es h264"
    assert "mpeg4" in motivo


def test_no_se_borra_el_original_si_el_reemplazo_no_existe(tmp_path):
    salida = tmp_path / "downloads"
    salida.mkdir()
    original = salida / "origen.mp4"
    original.write_bytes(b"x" * 512)

    borrado, motivo = borrar_original_si_es_seguro(
        original, salida / "origen_h264.mp4", verificador=lambda _ruta: (2, "h264", "aac")
    )

    assert borrado is False
    assert original.exists()
    assert motivo


def test_no_se_borra_el_original_si_el_reemplazo_esta_vacio(tmp_path):
    salida = tmp_path / "downloads"
    salida.mkdir()
    original = salida / "origen.mp4"
    original.write_bytes(b"x" * 512)
    vacio = salida / "origen_h264.mp4"
    vacio.write_bytes(b"")

    borrado, motivo = borrar_original_si_es_seguro(
        original, vacio, verificador=lambda _ruta: (2, "h264", "aac")
    )

    assert borrado is False
    assert original.exists()
    assert motivo


def test_no_se_borra_el_original_si_ffprobe_no_puede_verificar(tmp_path):
    salida = tmp_path / "downloads"
    salida.mkdir()
    original = salida / "origen.mp4"
    original.write_bytes(b"x" * 512)
    reemplazo = salida / "origen_h264.mp4"
    reemplazo.write_bytes(b"y" * 512)

    def _instrumento_roto(_ruta):
        raise RuntimeError("ffprobe no pudo leer el archivo")

    borrado, motivo = borrar_original_si_es_seguro(original, reemplazo, verificador=_instrumento_roto)

    assert borrado is False
    assert original.exists()
    assert "ffprobe" in motivo


# ------------------------------------------------------- 6. configuración


def test_el_config_json_del_proyecto_lleva_timeout_y_limpieza():
    """Los dos campos del plan existen en disco, con los valores decididos."""
    cfg = cargar(Path(__file__).resolve().parents[1] / "config.json")
    assert cfg.timeout_segundos == 1800
    assert cfg.limpiar_original_tras_recodificar is False


def test_los_valores_por_defecto_de_config_no_borran_nada():
    cfg = Config()
    assert cfg.timeout_segundos == 1800
    assert cfg.limpiar_original_tras_recodificar is False


def test_cargar_toma_los_campos_nuevos_del_json(tmp_path):
    ruta = tmp_path / "config.json"
    ruta.write_text(
        '{"timeout_segundos": 90, "limpiar_original_tras_recodificar": true,'
        ' "limpiar_part_al_cancelar": false}',
        encoding="utf-8",
    )
    cfg = cargar(ruta)
    assert cfg.timeout_segundos == 90
    assert cfg.limpiar_original_tras_recodificar is True
    assert cfg.limpiar_part_al_cancelar is False


def test_timeout_segundos_en_cero_significa_sin_limite(tmp_path, runner):
    """``0`` no puede significar "cortar de inmediato": es "sin límite"."""
    motor = YtdlpEngine(binario="yt-dlp", runner=runner(stdout="video.mp4\n", demora=0.1))
    cfg = _cfg(tmp_path, timeout_segundos=0)

    res = download("https://www.tiktok.com/@u/video/1", Opciones(), cfg, motor)

    assert "tiempo de espera" not in (res.error or ""), "un timeout de 0 cortó la corrida"


# --------------------------------------------------------- 7. centinela R8


def test_la_cancelacion_no_reactiva_r8(tmp_path, runner):
    """Ni cancelar ni expirar es excusa para volver a leer cookies (D14)."""
    cancel = threading.Event()
    cancel.set()
    motor = YtdlpEngine(binario="yt-dlp", runner=runner(stdout="video.mp4\n", demora=1.0))

    download("https://youtu.be/abc", Opciones(), _cfg(tmp_path), motor, cancel=cancel)

    culpables = [str(a) for a in motor._runner.argv if str(a).startswith("--cookies")]
    assert culpables == [], f"R8 reactivado: {culpables}"


def test_inspeccion_y_version_aceptan_la_misma_api_de_cancelacion(runner):
    """La GUI puede cancelar una inspección con el mismo evento que una descarga.

    Se declara ahora para que la capa de interfaz no se encuentre con una firma distinta.
    """
    cancel = threading.Event()
    cancel.set()
    motor = YtdlpEngine(binario="yt-dlp", runner=runner(stdout='{"id": "x"}', demora=1.0))
    with pytest.raises(CanceladoError):
        motor.inspect("https://youtu.be/abc", cancel=cancel)

    motor._runner = runner(stdout="2026.08.19\n", demora=1.0)
    with pytest.raises(CanceladoError):
        motor.version(cancel=cancel)


def test_inspeccion_tambien_vence_por_timeout(runner):
    """Inspeccionar toca la red: también puede colgarse (mismo reloj)."""
    motor = YtdlpEngine(binario="yt-dlp", runner=runner(stdout='{"id": "x"}', demora=5.0))
    with pytest.raises(TimeoutDescargaError):
        motor.inspect("https://youtu.be/abc", timeout_seg=0.2)


def test_resultado_sigue_siendo_inmutable_y_declara_residuos():
    res = Resultado(ok=False, error=MENSAJE_CANCELADO)
    assert res.residuos == ()
    assert res.original is None
    with pytest.raises(Exception):
        res.ok = True
