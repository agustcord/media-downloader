"""B1 y B2 medidos contra el **binario real** de yt-dlp.

Son los únicos tests que ejecutan ``bin\\yt-dlp.exe`` de verdad, porque el
defecto que cazan vive *en el proceso del motor*, no en el argv: con stdout en
pipe, yt-dlp escribe con la codepage ANSI de Windows y la ruta del usuario
llega corrupta. Un runner falso nunca lo vería.

Cómo se hace sin red y sin depender de ninguna plataforma
---------------------------------------------------------
Un MP4 sintético (ffmpeg) se sirve por HTTP en ``127.0.0.1``: yt-dlp lo baja con
su extractor genérico, lo mueve a destino y emite ``after_move:filepath``
exactamente igual que con un link de TikTok. El **único** doble es la
resolución de plataforma de ``core.download`` (una URL local no puede
pertenecer a ninguna red social); el binario, el movimiento del archivo y el
``ffprobe`` de verificación son los reales.

El directorio de salida se llama ``salida_í``: es el caso que rompía todo. Si
alguien quita ``--encoding utf-8``, la ``í`` vuelve como ``U+FFFD`` y estos
tres tests fallan.
"""

from __future__ import annotations

import functools
import http.server
import socketserver
import threading
from pathlib import Path

import pytest

from descargador import core
from descargador.config import Config, Opciones
from descargador.engine import RUTA_BINARIO, YtdlpEngine
from descargador.verify import streams_de

#: El binario es un artefacto grande que el repositorio no versiona (``.gitignore``).
#: Sin él no se puede medir nada, y eso es ambiental: se declara, no se finge.
pytestmark = pytest.mark.skipif(
    not RUTA_BINARIO.exists(), reason=f"no existe el binario: {RUTA_BINARIO}"
)

REEMPLAZO = "\ufffd"  # U+FFFD: la marca de una decodificación que perdió bytes
ACENTO = "í"


class _HandlerSilencioso(http.server.SimpleHTTPRequestHandler):
    """Sirve archivos sin escribir cada GET en la consola de pytest."""

    def log_message(self, *args, **kwargs) -> None:
        return


@pytest.fixture
def servidor(tmp_path):
    """Sirve ``tmp_path`` por HTTP local; devuelve la URL raíz."""
    fabrica = functools.partial(_HandlerSilencioso, directory=str(tmp_path))
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), fabrica)
    httpd.daemon_threads = True
    hilo = threading.Thread(target=httpd.serve_forever, daemon=True)
    hilo.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        hilo.join(timeout=5)


@pytest.fixture
def salida_con_acento(tmp_path) -> Path:
    """Directorio de destino cuyo nombre contiene ``í``."""
    ruta = tmp_path / f"salida_{ACENTO}"
    ruta.mkdir(parents=True, exist_ok=True)
    return ruta


def test_la_ruta_con_acento_se_resuelve_en_utf8_y_existe_en_disco(
    servidor, salida_con_acento, fabricar_mp4
):
    """B1: el motor devuelve una ruta íntegra, no una con caracteres perdidos."""
    clip = fabricar_mp4("clip.mp4")

    ruta = YtdlpEngine().download(
        f"{servidor}/{clip.name}",
        "tiktok",
        argv_extra=["-o", str(salida_con_acento / "%(title)s.%(ext)s")],
    )

    assert ACENTO in str(ruta), f"la ruta perdió el acento: {ruta!r}"
    assert REEMPLAZO not in str(ruta), f"la ruta llegó corrupta (cp1252): {ruta!r}"
    assert ruta.exists(), f"el motor reportó {ruta} y ese archivo no existe en disco"
    assert ruta.stat().st_size > 0
    assert ruta.parent == salida_con_acento


def test_el_motor_real_reporta_porcentaje_y_sin_perder_la_ruta(
    servidor, salida_con_acento, fabricar_mp4
):
    """B2: vuelve el porcentaje de la barra sin perder ``after_move:filepath``."""
    clip = fabricar_mp4("clip_progreso.mp4", duracion=4.0)
    recibido: list[tuple[float | None, str]] = []

    ruta = YtdlpEngine().download(
        f"{servidor}/{clip.name}",
        "tiktok",
        argv_extra=["-o", str(salida_con_acento / "%(title)s.%(ext)s")],
        on_progress=lambda pct, linea: recibido.append((pct, linea)),
    )

    porcentajes = [pct for pct, _ in recibido if pct is not None]
    assert porcentajes, "el motor no reportó ningún porcentaje: la barra queda indeterminada"
    assert max(porcentajes) == pytest.approx(100.0)
    assert ruta.exists()
    # La ruta sigue siendo la última línea: after_move es la etapa final.
    assert recibido[-1][1].strip() == str(ruta)


def test_core_download_con_motor_real_devuelve_ok_y_archivo_medido(
    servidor, salida_con_acento, fabricar_mp4, monkeypatch
):
    """Punta a punta: ``core.download`` + motor real + ``ffprobe``."""
    clip = fabricar_mp4("clip_core.mp4")
    monkeypatch.setattr(core, "detectar", lambda _url: "tiktok")
    cfg = Config(output_dir=str(salida_con_acento))

    res = core.download(f"{servidor}/{clip.name}", Opciones(), cfg, YtdlpEngine())

    assert res.ok, f"se esperaba éxito y llegó error={res.error}"
    assert res.archivo is not None
    assert REEMPLAZO not in str(res.archivo), f"ruta corrupta: {res.archivo!r}"
    assert res.archivo.exists()
    assert res.archivo.parent == salida_con_acento
    assert res.aviso is None, f"no se esperaba aviso: {res.aviso}"
    assert streams_de(res.archivo) == (2, "h264", "aac")
