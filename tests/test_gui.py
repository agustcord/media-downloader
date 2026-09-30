"""Humo de la interfaz.

Mide lo que es medible sin red y sin bloquear: que la App se construye, que al
hacerlo **no dispara ninguna descarga**, que arranca en ``idle``, que el
selector ofrece las cuatro calidades y que las declaraciones de D7′/D8 están
tanto en el fuente como en pantalla.

Lo que **no** es medible acá y queda como verificación manual del usuario: si se ve
bien, si la ventana se siente fluida, si la barra avanza en una descarga real.

Correcciones de UI que este archivo mide: el control de calidad en sus
dos estados, el espacio de estado (sin caja de registro), el contraste del
texto en curso y el botón Cancelar. El contraste se **calcula** con la función
WCAG 2.1, no se declara a ojo: un valor que no cumple es un defecto medible.
"""

from __future__ import annotations

import time
import tkinter as tk
from pathlib import Path

import pytest

import descargador.gui as gui_mod
from descargador.config import Config, Opciones
from descargador.core import MENSAJE_CANCELADO, Resultado, mensaje_timeout
from descargador.engine import YtdlpEngine
from descargador.gui import (
    AVISO_LIMITACIONES,
    CALIDADES,
    CON_ARCHIVO,
    GLIFO_MARCADO,
    GLIFO_VACIO,
    SIN_COOKIES,
    App,
    T,
)

URL_YT = "https://www.youtube.com/watch?v=aaaaaaaaaaa"


def _luminancia(color: str) -> float:
    """Luminancia relativa WCAG 2.1 de un ``#rrggbb``."""
    hexa = str(color).lstrip("#")
    r, g, b = (int(hexa[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def lineal(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = lineal(r), lineal(g), lineal(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contraste(frente: str, fondo: str) -> float:
    """Ratio de contraste WCAG 2.1 entre dos colores ``#rrggbb``."""
    a, b = _luminancia(frente), _luminancia(fondo)
    alto, bajo = max(a, b), min(a, b)
    return (alto + 0.05) / (bajo + 0.05)


def _buscar_cajas(widget) -> list[tk.Text]:
    """Todo widget de texto multilínea bajo ``widget`` (la caja que se quitó)."""
    halladas: list[tk.Text] = []
    for hijo in widget.winfo_children():
        if isinstance(hijo, tk.Text):
            halladas.append(hijo)
        halladas.extend(_buscar_cajas(hijo))
    return halladas


def _crear_raiz(intentos: int = 3) -> tk.Tk:
    """Crea una raíz de Tk oculta, reintentando si Tcl no inicializa.

    Medido en esta máquina: ``tk.Tk()`` falla con "Can't find a usable
    init.tcl" aproximadamente **1 de cada 14 creaciones** (el archivo existe y
    es legible; el error es intermitente y ajeno al proyecto). Con una sola raíz
    por módulo y reintento, la suite deja de ser intermitente.
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
    """App con motor falso. El runner registra cada argv que se le pida.

    ``ruta_log`` va al temporal: el registro de la corrida es un diagnóstico y
    no tiene que ensuciar el proyecto cuando corren los tests.
    """
    r = runner(stdout="2026.08.19\n")
    cfg = Config(output_dir=str(tmp_path))
    aplicacion = App(raiz, cfg, YtdlpEngine(runner=r), ruta_log=tmp_path / "registro.log")
    return aplicacion, r, cfg


def test_la_app_se_construye_sin_tocar_la_red(app):
    """Criterio 1: construir y destruir, sin descargar y sin bloquear."""
    aplicacion, r, _cfg = app
    # El único subprocess pedido es --version. Cualquier otra llamada sería
    # trabajo real (una inspección o una descarga) disparado al construir.
    assert len(r.llamadas) == 1
    assert r.llamadas[0][-1] == "--version"
    assert aplicacion._version == "2026.08.19"


def test_arranca_en_idle_y_con_los_botones_habilitados(app):
    aplicacion, _r, _cfg = app
    assert aplicacion.estado == "idle"
    assert str(aplicacion.boton_descargar["state"]) == "normal"
    assert str(aplicacion.boton_inspeccionar["state"]) == "normal"


def test_el_selector_ofrece_las_cuatro_calidades(app):
    aplicacion, _r, _cfg = app
    etiquetas = [c.etiqueta for c in CALIDADES]
    assert etiquetas == [
        "Mejor hasta 1080p",
        "Hasta 720p",
        "Solo audio MP3",
        "Solo audio M4A",
    ]
    assert aplicacion.calidad_var.get() == etiquetas[0]
    aplicacion.calidad_var.set("Solo audio M4A")
    op = aplicacion._opciones_actuales()
    assert op == Opciones(modo="audio", audio_format="m4a")


def test_cookies_por_defecto_son_sin_cookies(app):
    aplicacion, _r, cfg = app
    assert aplicacion.cookies_var.get() == SIN_COOKIES
    assert aplicacion.ruta_cookies_var.get() == ""
    # Sin cookies elegidas, la Config efectiva es la original: sin flags.
    assert aplicacion._cfg_efectivo() is cfg


def test_elegir_archivo_de_cookies_sin_ruta_avisa_y_no_promete(app):
    aplicacion, _r, cfg = app
    aplicacion.cookies_var.set(CON_ARCHIVO)
    aplicacion.ruta_cookies_var.set("")
    assert aplicacion._cfg_efectivo() is cfg
    pendientes = [dato for tipo, dato in _vaciar(aplicacion.cola) if tipo == "log"]
    assert any("sin cookies" in texto.lower() for texto, _tag in pendientes)


def test_elegir_archivo_inexistente_avisa_y_no_promete(app, tmp_path):
    aplicacion, _r, cfg = app
    aplicacion.cookies_var.set(CON_ARCHIVO)
    aplicacion.ruta_cookies_var.set(str(tmp_path / "no-existe.txt"))
    assert aplicacion._cfg_efectivo() is cfg


def test_el_fuente_no_ofrece_cookies_de_navegador():
    """Criterio 7: la GUI no promete Brave/Chrome/Edge."""
    fuente = Path(gui_mod.__file__).read_text(encoding="utf-8")
    assert "cookies-from-browser" not in fuente
    for navegador in ("brave", "chrome", "edge"):
        assert navegador not in fuente.lower()


def test_el_fuente_declara_lo_que_no_soporta():
    """Criterio 6: historias no soportadas y carruseles no verificados."""
    fuente = Path(gui_mod.__file__).read_text(encoding="utf-8")
    assert "no soportadas" in fuente
    assert "no verificado" in fuente


def test_las_limitaciones_son_visibles_en_pantalla(raiz, runner, tmp_path):
    """El texto tiene que estar en un widget, no sólo en el fuente."""
    aplicacion = App(raiz, Config(output_dir=str(tmp_path)), YtdlpEngine(runner=runner(stdout="v\n")))
    textos = _textos_de_labels(raiz)
    assert any("no soportadas" in t for t in textos)
    assert any("no verificado" in t for t in textos)
    assert AVISO_LIMITACIONES in textos


def test_rechaza_link_vacio_y_link_de_otra_plataforma(app):
    aplicacion, r, _cfg = app
    assert aplicacion.iniciar("", Opciones()) is False
    assert aplicacion.iniciar("https://example.com/video", Opciones()) is False
    assert aplicacion.estado == "idle"
    assert len(r.llamadas) == 1  # sólo --version: no se pidió nada al motor


def test_tras_un_fallo_el_estado_vuelve_a_idle_y_el_boton_se_rehabilita(app, runner):
    """I3 en chico: el ciclo ocupado → fin deja la App usable."""
    aplicacion, _r, _cfg = app
    aplicacion.engine = YtdlpEngine(runner=runner(codigo=1, stderr="ERROR: boom\n"))
    aplicacion._lanzar_hilo = lambda objetivo: objetivo()  # sonda sincronica

    assert aplicacion.iniciar(URL_YT, Opciones()) is True
    assert aplicacion.estado == "ocupado"
    aplicacion.poll()
    assert aplicacion.estado == "idle"
    assert str(aplicacion.boton_descargar["state"]) == "normal"
    assert aplicacion.ultimo is not None and aplicacion.ultimo.ok is False


def test_mide_el_codec_de_un_archivo_real(app, fabricar_mp4):
    aplicacion, _r, _cfg = app
    completo = fabricar_mp4("completo.mp4")
    texto, tag = aplicacion._medir_codec(completo)
    assert "h264" in texto and "aac" in texto
    assert tag == "ok"


def test_un_archivo_sin_audio_se_reporta_ambar_y_nunca_verde(app, fabricar_mp4):
    aplicacion, _r, _cfg = app
    sin_audio = fabricar_mp4("sin_audio.mp4", audio=False)
    texto, tag = aplicacion._medir_codec(sin_audio)
    assert "SIN audio" in texto
    assert tag == "aviso"


def test_cuando_no_hay_archivo_no_se_inventa_un_codec(app):
    aplicacion, _r, _cfg = app
    texto, tag = aplicacion._medir_codec(None)
    assert "no se midió" in texto.lower()
    assert tag == "muted"


# ---------------------------------------------------------------------------
# Correcciones de UI — lo que el docstring de gui.py promete y acá se
# mide. Nada de esto se declara "a ojo": estados sobre el widget, contraste con
# la función WCAG 2.1 y teclado sobre el atributo que Tk realmente consulta.
# ---------------------------------------------------------------------------


def test_cancelar_deshabilitado_en_idle_y_normal_durante_la_descarga(app, runner):
    """``state`` medido **sobre el widget**, no en el fuente."""
    aplicacion, _r, _cfg = app
    assert str(aplicacion.boton_cancelar["state"]) == "disabled"

    aplicacion.engine = YtdlpEngine(runner=runner(stdout="lento.mp4\n", demora=0.3))
    assert aplicacion.iniciar(URL_YT, Opciones()) is True
    aplicacion.poll()  # aplica el evento "ocupado": Cancelar pasa a normal
    assert str(aplicacion.boton_cancelar["state"]) == "normal"
    # Corrección 3: el texto en curso va en blanco (titular), nunca gris.
    assert aplicacion.estado_label.cget("fg") == T["titular"]

    # Cierre del ciclo (I3): la corrida termina sola y la App queda usable. Se
    # espera de verdad — dejar la barra en indeterminado dejaría un ``after``
    # del ttk vivo y ensuciaría la raíz compartida del módulo.
    for _ in range(1000):
        aplicacion.poll()
        if aplicacion.estado == "idle":
            break
        time.sleep(0.01)
    assert aplicacion.estado == "idle"
    assert str(aplicacion.boton_descargar["state"]) == "normal"


def test_cancelar_durante_la_descarga_vuelve_a_idle_con_el_mensaje_literal(app, runner):
    """Botón Cancelar medido sobre el widget:

    El clic en Cancelar prende el evento; el doble del motor honra el contrato,
    ``core`` devuelve el mensaje literal y la App vuelve a ``idle`` con ese
    mensaje **en pantalla**, sin archivo y con Cancelar otra vez apagado.
    """
    aplicacion, _r, _cfg = app
    aplicacion.engine = YtdlpEngine(runner=runner(stdout="nunca.mp4\n", demora=5.0))
    assert aplicacion.iniciar(URL_YT, Opciones()) is True
    aplicacion.poll()
    assert aplicacion.estado == "ocupado"

    aplicacion._al_cancelar()
    aplicacion.poll()  # aplica "cancelando": el clic se ve de inmediato
    assert "Cancelando" in aplicacion.estado_var.get()

    for _ in range(1000):
        aplicacion.poll()
        if aplicacion.estado == "idle":
            break
        time.sleep(0.01)
    assert aplicacion._hilo is not None
    aplicacion._hilo.join(timeout=10)
    aplicacion.poll()

    assert aplicacion.estado == "idle", "I9: una corrida cancelada no volvió a idle"
    assert aplicacion.ultimo is not None and aplicacion.ultimo.ok is False
    assert aplicacion.ultimo.archivo is None
    assert aplicacion.ultimo.error == MENSAJE_CANCELADO
    assert aplicacion.estado_var.get() == MENSAJE_CANCELADO
    assert str(aplicacion.boton_cancelar["state"]) == "disabled"


def test_timeout_muestra_el_tiempo_y_como_subirlo(app, tmp_path):
    """El mensaje del *timeout* nombra los minutos configurados

    ``cfg.timeout_segundos`` es 1800 en el proyecto, pero el ``Config`` del
    *fixture* usa sus propios valores: el test usa el de la ``cfg`` real de la
    App, así que mide lo que la GUI va a mostrar, no un número suelto.
    """
    aplicacion, _r, cfg = app
    mensaje = mensaje_timeout(cfg.timeout_segundos)
    aplicacion._al_terminar(Resultado(ok=False, error=mensaje))

    assert aplicacion.estado == "idle"
    assert "tiempo de espera" in aplicacion.estado_var.get()
    assert "minuto" in aplicacion.estado_var.get() or "minuto" in aplicacion.detalle_var.get()
    # La salida es accionable, no un muro: el detalle dice dónde se cambia.
    assert "timeout_segundos" in aplicacion.detalle_var.get()
    # Semántica de color: un timeout es aviso (ámbar), no error rojo.
    assert aplicacion.estado_label.cget("fg") == T["aviso"]


def test_tras_recodificar_se_nombra_el_original_conservado(app, fabricar_mp4, tmp_path):
    """Criterio 4 / D13: informar en vez de borrar — el original se nombra."""
    aplicacion, _r, _cfg = app
    archivo = fabricar_mp4("reemplazo.mp4")
    original = tmp_path / "original.mp4"
    aplicacion._al_terminar(Resultado(ok=True, archivo=archivo, original=original))

    assert "Descarga finalizada" in aplicacion.estado_var.get()
    detalle = aplicacion.detalle_var.get()
    assert "Original conservado" in detalle
    assert original.name in detalle


def test_no_hay_caja_de_registro_y_el_estado_dice_que_paso(raiz, runner, tmp_path):
    """Corrección 2: ni un solo ``tk.Text`` en toda la ventana; queda la línea de estado."""
    aplicacion = App(
        raiz,
        Config(output_dir=str(tmp_path)),
        YtdlpEngine(runner=runner(stdout="v\n")),
        ruta_log=tmp_path / "registro.log",
    )
    assert _buscar_cajas(raiz) == []
    archivo = fabricar_mp4_si_hay_ffmpeg(tmp_path)
    if archivo is not None:  # con ffmpeg disponible se verifica la palabra exacta
        aplicacion._al_terminar(Resultado(ok=True, archivo=archivo))
        assert "finalizada" in aplicacion.estado_var.get().lower()


def fabricar_mp4_si_hay_ffmpeg(tmp_path):
    """MP4 sintético si ffmpeg está; ``None`` si no (la caja es lo que se mide)."""
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None:
        return None
    ruta = tmp_path / "caja.mp4"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=64x64:rate=5", "-t", "0.5", str(ruta)],
        capture_output=True, text=True,
    )
    return ruta if proc.returncode == 0 and ruta.exists() else None


def test_el_control_de_calidad_en_sus_dos_estados(app):
    """Corrección 1: seleccionado = cian lleno + ``●`` + negrita; el resto, bloque oscuro."""
    aplicacion, _r, _cfg = app
    radios = aplicacion._radios_calidad
    assert len(radios) == 4

    marcado, _calidad = radios[0]
    assert marcado.cget("bg") == T["cian"]
    assert str(marcado.cget("text")).startswith(GLIFO_MARCADO)
    assert "bold" in str(marcado.cget("font"))
    for boton, _v in radios[1:]:
        assert boton.cget("bg") == T["elevado"]
        assert str(boton.cget("text")).startswith(GLIFO_VACIO)
        assert "bold" not in str(boton.cget("font"))

    # Cambiar la selección por cualquier vía (mouse o teclado llega por la misma
    # ``trace_add``) re-repinta el grupo: el estado sigue siendo inequívoco.
    aplicacion.calidad_var.set(radios[2][1].etiqueta)
    assert radios[2][0].cget("bg") == T["cian"]
    assert str(radios[2][0].cget("text")).startswith(GLIFO_MARCADO)
    assert radios[0][0].cget("bg") == T["elevado"]
    assert str(radios[0][0].cget("text")).startswith(GLIFO_VACIO)


def test_el_selector_de_cookies_tambien_marca_su_estado(app):
    """El mismo patrón de bloques vale para cookies: ninguna opción ambigua."""
    aplicacion, _r, _cfg = app
    radios = aplicacion._radios_cookies
    assert len(radios) == 2
    assert radios[0][0].cget("bg") == T["cian"]
    aplicacion.cookies_var.set(CON_ARCHIVO)
    assert radios[1][0].cget("bg") == T["cian"]
    assert radios[0][0].cget("bg") == T["elevado"]


def test_contraste_del_control_de_calidad_en_sus_dos_estados(app):
    """Criterio 4a: el nuevo control mide ≥ 4.5:1 en seleccionado y en reposo."""
    aplicacion, _r, _cfg = app
    marcado = aplicacion._radios_calidad[0][0]
    ratio_sel = contraste(marcado.cget("fg"), marcado.cget("bg"))
    reposo = aplicacion._radios_calidad[1][0]
    ratio_reposo = contraste(reposo.cget("fg"), reposo.cget("bg"))
    assert ratio_sel >= 4.5, f"seleccionado: {ratio_sel:.2f}:1"
    assert ratio_reposo >= 4.5, f"reposo: {ratio_reposo:.2f}:1"


def test_contraste_del_texto_de_estado_y_del_boton_inactivo(app):
    """Criterio 4b + corrección 3: el gris ya no convive con el cian.

    El defecto original medido: ``#9ca3af`` sobre ``#06b6d4`` daba 1.05:1. La
    regla nueva: el gris vive sólo sobre superficie oscura, y el texto en curso
    va en blanco sobre el fondo de la app.
    """
    en_curso = contraste(T["titular"], T["app"])
    gris_sobre_oscuro = contraste(T["inactivo"], T["elevado"])
    gris_sobre_cian = contraste(T["inactivo"], T["cian"])
    assert en_curso >= 4.5, f"texto en curso: {en_curso:.2f}:1"
    assert gris_sobre_oscuro >= 4.5, f"gris sobre elevado: {gris_sobre_oscuro:.2f}:1"
    # Documentado como prohibido: el combos defectuoso queda bajo AA a propósito.
    assert gris_sobre_cian < 4.5, (
        "si esto subiera de 4.5:1 la prohibición quedaría obsoleta; hoy mide "
        f"{gris_sobre_cian:.2f}:1 y por eso no se usa"
    )


def test_contraste_de_los_colores_semanticos_del_estado(app):
    """Todo color que puede tomar la línea de estado mide AA sobre su fondo."""
    for token in ("ok", "error", "aviso", "muted", "texto"):
        ratio = contraste(T[token], T["app"])
        assert ratio >= 4.5, f"{token} sobre app: {ratio:.2f}:1"
    # El único texto claro sobre cian es el del bloque seleccionado (sobre_cian).
    assert contraste(T["sobre_cian"], T["cian"]) >= 4.5


def test_selector_botes_y_radios_son_operables_con_teclado(app):
    """Criterio 6: todo interactivo entra en el recorrido de tabulación de Tk."""
    aplicacion, _r, _cfg = app
    interactivos = (
        [r for r, _v in aplicacion._radios_calidad]
        + [r for r, _v in aplicacion._radios_cookies]
        + [
            aplicacion.boton_descargar,
            aplicacion.boton_cancelar,
            aplicacion.boton_inspeccionar,
        ]
    )
    assert interactivos, "el test sería vacuo: no encontró widgets interactivos"
    for widget in interactivos:
        assert str(widget.cget("takefocus")) == "1", (
            f"{widget.cget('text')!r} queda fuera del teclado"
        )


def _textos_de_labels(widget) -> list[str]:
    """Textos de todos los Labels bajo ``widget`` (incluye los de variables)."""
    textos: list[str] = []
    for hijo in widget.winfo_children():
        if isinstance(hijo, tk.Label):
            valor = hijo.cget("text")
            if not valor:
                variable = str(hijo.cget("textvariable"))
                if variable:
                    valor = hijo.getvar(variable)
            textos.append(str(valor))
        textos.extend(_textos_de_labels(hijo))
    return textos


def _vaciar(cola) -> list[tuple]:
    salida = []
    while True:
        try:
            salida.append(cola.get_nowait())
        except Exception:
            return salida
