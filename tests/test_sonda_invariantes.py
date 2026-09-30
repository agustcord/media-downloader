"""Sonda de invariantes I1–I8 (§7.1 del plan v2).

Veinte descargas sintéticas, intercaladas (10 éxito / 10 fallo) y mezclando las
tres plataformas. El doble se inyecta como **runner dentro de ``YtdlpEngine``**
—no reemplazando el motor entero— porque las flags de cookies y de
``web_embedded`` las construye ``engine``: un motor falso nunca las vería.

Dos avisos que esta sonda respeta:
- **I1 no es medible sobre ``core``**: el núcleo es sin estado a propósito. Se
  mide en la App, que es la capa que tiene el estado.
- **I7 e I8 se ven en el argv** que construye ``engine``, así que exigen el
  runner falso y no un motor falso.

I2 (el hilo nunca toca widgets) se mide con un espía sobre el intérprete Tcl de
cada widget: toda llamada a Tk queda registrada con el hilo que la hizo.
"""

from __future__ import annotations

import threading
import time
import tkinter as tk

import pytest

from descargador.config import Config, Opciones, resolver_salida
from descargador.core import MENSAJE_CANCELADO, download
from descargador.engine import Salida, YtdlpEngine
from descargador.gui import CON_ARCHIVO, App

URLS = {
    "youtube": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
    "instagram": "https://www.instagram.com/reel/AAAAAAAAAA/?igsh=xyz123",
    "tiktok": "https://www.tiktok.com/@alguien/video/7123456789012345678",
}
CICLO = ("youtube", "instagram", "tiktok")
CORRIDAS = [(i, CICLO[i % 3], i % 2 == 0) for i in range(20)]


def _crear_raiz(intentos: int = 3) -> tk.Tk:
    """Crea una raíz de Tk oculta, reintentando si Tcl no inicializa.

    Medido en esta máquina: ``tk.Tk()`` falla con "Can't find a usable
    init.tcl" aproximadamente **1 de cada 14 creaciones** (el archivo existe; el
    error es intermitente y ajeno al proyecto).
    """
    ultimo: Exception | None = None
    for _ in range(intentos):
        try:
            root = tk.Tk()
            root.withdraw()
            return root
        except tk.TclError as exc:  # falla ambiental, no del producto
            ultimo = exc
    raise AssertionError(f"Tcl no inicializó tras {intentos} intentos: {ultimo}")


@pytest.fixture(scope="module")
def raiz():
    """Una sola raíz de Tk por módulo: ni parpadea ventanas ni multiplica Tcl."""
    root = _crear_raiz()
    yield root
    root.destroy()


@pytest.fixture(autouse=True)
def _limpiar_raiz(raiz):
    """Deja la raíz vacía entre tests: nadie hereda widgets del anterior."""
    yield
    pendientes = raiz.tk.call("after", "info")
    if isinstance(pendientes, str):
        pendientes = pendientes.split()
    for id_after in pendientes or ():
        try:
            raiz.after_cancel(id_after)
        except tk.TclError:
            pass
    for hijo in list(raiz.winfo_children()):
        hijo.destroy()


@pytest.fixture
def app(raiz, runner, tmp_path):
    """App con motor falso y salida en ``tmp_path``."""
    cfg = Config(output_dir=str(tmp_path))
    return App(raiz, cfg, YtdlpEngine(runner=runner(stdout="2026.08.19\n"))), cfg


class EspiaTk:
    """Delega en el intérprete Tcl real y anota quién llamó."""

    def __init__(self, real, registro: list[str]) -> None:
        self._real = real
        self._registro = registro

    def call(self, *args, **kwargs):
        self._registro.append(threading.current_thread().name)
        return self._real.call(*args, **kwargs)

    def __getattr__(self, nombre):
        return getattr(self._real, nombre)


def espiar(app: App) -> list[str]:
    """Envuelve el Tcl de cada widget de la App; devuelve el registro."""
    registro: list[str] = []
    for _nombre, valor in list(vars(app).items()):
        if isinstance(valor, tk.Misc):
            valor.tk = EspiaTk(valor.tk, registro)
        elif isinstance(valor, tk.Variable):
            valor._tk = EspiaTk(valor._tk, registro)
    return registro


def texto(argv) -> str:
    return " ".join(str(a) for a in argv)


def test_las_veinte_corridas_cumplen_i1_i3_i4_i5_i6_i7_i8(app, runner, fabricar_mp4, tmp_path):
    aplicacion, cfg = app
    aplicacion._lanzar_hilo = lambda objetivo: objetivo()  # sonda determinista
    salida = resolver_salida(cfg)

    argvs: dict[int, list[str]] = {}
    resultados: dict[int, object] = {}

    for indice, plataforma, exito in CORRIDAS:
        if exito:
            archivo = fabricar_mp4(f"corrida{indice}.mp4")
            r = runner(stdout=f"{archivo}\n", lineas=("  25.0%", "  75.0%"))
        else:
            r = runner(codigo=1, stderr=f"ERROR: fallo sintetico {indice} en {plataforma}\n")

        aplicacion.engine = YtdlpEngine(runner=r)
        url = URLS[plataforma]

        # I1: la primera ocupa la App; la segunda tiene que ser rechazada.
        assert aplicacion.iniciar(url, Opciones()) is True, f"corrida {indice}: no arrancó"
        assert aplicacion.estado == "ocupado", f"corrida {indice}: no pasó a ocupado"
        assert aplicacion.iniciar(url, Opciones()) is False, (
            f"corrida {indice}: aceptó una segunda descarga simultánea (I1 violada)"
        )

        aplicacion.poll()

        # I3: tras éxito o fallo, el estado vuelve a idle.
        assert aplicacion.estado == "idle", f"corrida {indice}: no volvió a idle (I3 violada)"

        argvs[indice] = list(r.llamadas[-1])
        resultados[indice] = aplicacion.ultimo

    # --- I4 y I5: éxito ⇒ archivo real y dentro de output_dir ---------------
    for indice, plataforma, exito in CORRIDAS:
        if not exito:
            continue
        res = resultados[indice]
        assert res is not None and res.ok, f"corrida {indice}: se esperaba éxito y llegó {res}"
        assert res.archivo is not None and res.archivo.exists(), (
            f"corrida {indice}: el archivo reportado no existe (I4 violada)"
        )
        assert res.archivo.stat().st_size > 0, f"corrida {indice}: archivo vacío (I4 violada)"
        assert res.archivo.resolve().is_relative_to(salida.resolve()), (
            f"corrida {indice}: {res.archivo} quedó fuera de {salida} (I5 violada)"
        )

    # --- I6: en los 10 fallos, el error conserva el stderr del motor --------
    for indice, plataforma, exito in CORRIDAS:
        if exito:
            continue
        res = resultados[indice]
        assert res is not None and res.ok is False, f"corrida {indice}: se esperaba fallo"
        assert f"fallo sintetico {indice}" in (res.error or ""), (
            f"corrida {indice}: el log perdió el stderr del motor (I6 violada): {res.error}"
        )

    # --- I7: el filtro avc1 sólo en YouTube --------------------------------
    for indice, plataforma, _exito in CORRIDAS:
        argv = texto(argvs[indice])
        if plataforma == "youtube":
            assert "avc1" in argv, f"corrida {indice}: YouTube sin filtro avc1"
        else:
            assert "avc1" not in argv, (
                f"corrida {indice}: {plataforma} recibió el filtro avc1 (I7 violada)"
            )

    # --- I8: ninguna corrida inyecta cookies en default --------------------
    for indice, _plataforma, _exito in CORRIDAS:
        culpables = [t for t in argvs[indice] if str(t).startswith("--cookies")]
        assert not culpables, f"corrida {indice}: inyectó {culpables} (I8 violada, R8 reactivado)"

    # --- forma de la sonda: 20 corridas, 10 y 10, las tres plataformas -----
    assert sum(1 for _i, _p, e in CORRIDAS if e) == 10
    assert sum(1 for _i, _p, e in CORRIDAS if not e) == 10
    assert {p for _i, p, _e in CORRIDAS} == set(URLS)


def test_i2_el_hilo_de_trabajo_no_toca_widgets(app, fabricar_mp4, tmp_path):
    """I2 medido: toda llamada a Tk tiene que venir del hilo principal."""
    aplicacion, _cfg = app
    registro = espiar(aplicacion)
    en_hilo: list[str] = []
    archivo = fabricar_mp4("hilo.mp4")

    def runner_espia(argv, on_line, timeout_seg=None, cancel=None):
        en_hilo.append(threading.current_thread().name)
        on_line("  50.0%")
        return Salida(codigo=0, stdout=f"{archivo}\n", stderr="")

    aplicacion.engine = YtdlpEngine(runner=runner_espia)
    assert aplicacion.iniciar(URLS["youtube"], Opciones()) is True

    for _ in range(500):
        aplicacion.poll()
        if aplicacion.estado == "idle":
            break
        time.sleep(0.005)
    aplicacion._hilo.join(timeout=10)

    assert en_hilo == ["descargador-trabajo"], (
        f"la descarga no corrió en el hilo de trabajo: {en_hilo}"
    )
    assert registro, "el espía no registró ninguna llamada a Tk: el test sería vacuo"
    culpables = sorted({h for h in registro if h != "MainThread"})
    assert not culpables, f"se llamó a Tk desde los hilos {culpables} (I2 violada)"


MODOS_I9 = ("exito",) * 5 + ("fallo",) * 5 + ("cancelada",) * 5 + ("timeout",) * 5
CORRIDAS_I9 = [(i, CICLO[i % 3], MODOS_I9[i]) for i in range(20)]


def test_i9_corridas_canceladas_y_expiradas_nunca_reportan_exito(
    raiz, runner, fabricar_mp4, tmp_path
):
    """I9 (D12): 20 corridas — 5 éxito, 5 fallo de motor, 5 canceladas, 5 expiradas.

    Ninguna interrumpida puede producir ``ok=True`` ni un archivo, y su ``error``
    tiene que traer el mensaje literal que la GUI va a mostrar.

    **Medido a nivel de núcleo** (``core.download``) y no sobre la App porque la
    sonda arrastra el estado anterior a la capa de interfaz. La mitad de I9 que dice
    "al final ``estado == 'idle'``" **se mide en la suite de interfaz**:
    ``tests\\test_gui.py::test_cancelar_durante_la_descarga_vuelve_a_idle_con_el_mensaje_literal``
    (clic real en Cancelar → mensaje literal en pantalla → ``idle``).
    """
    salida = tmp_path / "downloads"
    salida.mkdir()
    cfg = Config(output_dir=str(salida))
    resultados: dict[int, object] = {}

    for indice, plataforma, modo in CORRIDAS_I9:
        if modo == "exito":
            archivo = fabricar_mp4(f"i9_{indice}.mp4")
            motor = YtdlpEngine(runner=runner(stdout=f"{archivo}\n", lineas=("  50.0%",)))
        elif modo == "fallo":
            motor = YtdlpEngine(runner=runner(codigo=1, stderr=f"ERROR: fallo i9 {indice}\n"))
        else:
            # Un .part previo: la corrida interrumpida no puede dejarlo tirado.
            (salida / f"residuo{indice}.mp4.part").write_bytes(b"x")
            motor = YtdlpEngine(runner=runner(stdout="nunca.mp4\n", demora=1.0))

        cancel = threading.Event()
        if modo == "cancelada":
            cancel.set()
        timeout = 0.05 if modo == "timeout" else None
        resultados[indice] = download(
            URLS[plataforma],
            Opciones(),
            cfg,
            motor,
            cancel=cancel if modo == "cancelada" else None,
            timeout_seg=timeout,
        )

    for indice, _plataforma, modo in CORRIDAS_I9:
        res = resultados[indice]
        assert res is not None
        if modo == "exito":
            assert res.ok, f"corrida {indice}: se esperaba éxito y llegó {res.error}"
            assert res.archivo is not None and res.archivo.exists()
        elif modo == "fallo":
            assert res.ok is False
            assert f"fallo i9 {indice}" in (res.error or ""), (
                f"corrida {indice}: el log perdió el stderr del motor"
            )
        else:
            assert res.ok is False, (
                f"corrida {indice}: una corrida {modo} reportó éxito (I9 violada)"
            )
            assert res.archivo is None, f"corrida {indice}: entregó un archivo pese a {modo}"
            if modo == "cancelada":
                assert res.error == MENSAJE_CANCELADO, f"corrida {indice}: {res.error}"
            else:
                assert "tiempo de espera" in (res.error or ""), f"corrida {indice}: {res.error}"
            assert not (salida / f"residuo{indice}.mp4.part").exists(), (
                f"corrida {indice}: quedó un .part huérfano (I9/D13 violada)"
            )

    # Forma de la sonda: 20 corridas, 5 de cada modo, las tres plataformas.
    assert len(CORRIDAS_I9) == 20
    for modo in ("exito", "fallo", "cancelada", "timeout"):
        assert sum(1 for _i, _p, m in CORRIDAS_I9 if m == modo) == 5, f"faltan corridas {modo}"
    assert {p for _i, p, _m in CORRIDAS_I9} == set(URLS)


def test_el_archivo_de_cookies_se_inyecta_solo_cuando_se_elige(app, runner, fabricar_mp4, tmp_path):
    """Contraparte de I8: la vía Netscape funciona, pero no está activa por defecto."""
    aplicacion, cfg = app
    aplicacion._lanzar_hilo = lambda objetivo: objetivo()
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    aplicacion.cookies_var.set(CON_ARCHIVO)
    aplicacion.ruta_cookies_var.set(str(cookies))
    r = runner(stdout=f"{fabricar_mp4('con_cookies.mp4')}\n")
    aplicacion.engine = YtdlpEngine(runner=r)

    assert aplicacion.iniciar(URLS["instagram"], Opciones()) is True
    aplicacion.poll()

    argv = r.llamadas[-1]
    assert "--cookies" in argv
    assert str(cookies) in argv
