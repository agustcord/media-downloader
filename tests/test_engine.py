"""Tests de ``descargador.engine`` y ``descargador.cookies`` sobre el argv.

Usan un runner falso: no hay red, no hay yt-dlp, no hay cookies reales.
El grupo de cookies es la alarma de R8: mientras ``CookieSpec()`` sea el
default, el argv no puede contener ni ``--cookies`` ni ``--cookies-from-browser``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from descargador.cookies import CookieSpec
from descargador.engine import MotorError, YtdlpEngine


def _motor(runner_falso) -> YtdlpEngine:
    return YtdlpEngine(binario=Path("bin/yt-dlp.exe"), runner=runner_falso)


def _descargar(runner_falso, plataforma: str, cookies: CookieSpec | None = None):
    motor = _motor(runner_falso)
    motor.download(
        "https://example.com/video",
        plataforma,
        argv_extra=["-o", "salida.mp4"],
        cookies=cookies,
    )
    return runner_falso


def test_player_client_web_embedded_se_inyecta_en_youtube(runner):
    falso = _descargar(runner(stdout="video.mp4\n"), "youtube")
    assert "--extractor-args" in falso.argv
    assert falso.valor_de("--extractor-args") == "youtube:player_client=web_embedded"


@pytest.mark.parametrize("plataforma", ["instagram", "tiktok"])
def test_player_client_no_se_inyecta_fuera_de_youtube(runner, plataforma):
    falso = _descargar(runner(stdout="video.mp4\n"), plataforma)
    assert "--extractor-args" not in falso.argv
    assert "web_embedded" not in " ".join(falso.argv)


def test_inspeccion_de_youtube_usa_el_mismo_cliente_que_la_descarga(runner):
    motor = _motor(runner(stdout='{"id": "abc"}'))
    motor.inspect("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert motor._runner.valor_de("--extractor-args") == "youtube:player_client=web_embedded"


def test_inspeccion_de_instagram_no_inyecta_extractor_args(runner):
    motor = _motor(runner(stdout='{"id": "abc"}'))
    motor.inspect("https://www.instagram.com/reel/ABC/")
    assert "--extractor-args" not in motor._runner.argv


def test_cookiespec_por_defecto_no_inyecta_cookies(runner):
    falso = _descargar(runner(stdout="video.mp4\n"), "youtube", cookies=CookieSpec())
    assert "--cookies" not in falso.argv
    assert "--cookies-from-browser" not in falso.argv


def test_sin_especificar_cookies_tampoco_se_inyecta_nada(runner):
    falso = _descargar(runner(stdout="video.mp4\n"), "youtube", cookies=None)
    assert "--cookies" not in falso.argv
    assert "--cookies-from-browser" not in falso.argv


def test_cookiespec_por_defecto_no_produce_argumentos():
    assert CookieSpec().to_args() == []


def test_cookiespec_con_archivo_netscape_inyecta_cookies(runner):
    falso = _descargar(runner(stdout="video.mp4\n"), "tiktok", cookies=CookieSpec(archivo="cookies.txt"))
    assert falso.valor_de("--cookies") == "cookies.txt"


def test_cookiespec_con_navegador_inyecta_cookies_from_browser(runner):
    falso = _descargar(runner(stdout="video.mp4\n"), "tiktok", cookies=CookieSpec(browser="brave"))
    assert falso.valor_de("--cookies-from-browser") == "brave"


def test_cookiespec_con_navegador_y_perfil_compone_el_argumento():
    assert CookieSpec(browser="chrome", profile="Profile 4").to_args() == [
        "--cookies-from-browser",
        "chrome:Profile 4",
    ]


def test_el_archivo_de_cookies_tiene_precedencia_sobre_el_navegador():
    assert CookieSpec(browser="brave", archivo="c.txt").to_args() == ["--cookies", "c.txt"]


def test_version_devuelve_el_stdout_del_binario(runner):
    motor = _motor(runner(stdout="2026.08.19\n"))
    assert motor.version() == "2026.08.19"
    assert motor._runner.argv[-1] == "--version"


def test_encoding_utf8_se_inyecta_en_las_tres_corridas(runner):
    """B1 (P0): sin ``--encoding utf-8`` el motor imprime la ruta en cp1252.

    Medido sobre el binario: la ``í`` de una ruta de usuario con acento sale
    como ``0xED``; decodificada como UTF-8 llega ``U+FFFD`` y el ``Path`` no
    existe. La flag es del proceso, así que va en las tres corridas.
    """
    falso = _descargar(runner(stdout="video.mp4\n"), "tiktok")
    assert falso.valor_de("--encoding") == "utf-8"

    motor = _motor(runner(stdout='{"id": "abc"}'))
    motor.inspect("https://youtu.be/abc")
    assert motor._runner.valor_de("--encoding") == "utf-8"

    motor._runner = runner(stdout="2026.08.19\n")
    motor.version()
    assert motor._runner.valor_de("--encoding") == "utf-8"


def test_download_pide_progreso_aunque_print_traiga_quiet(runner):
    """B2: ``--print`` implica ``--quiet``; ``--progress`` devuelve la barra."""
    falso = _descargar(runner(stdout="video.mp4\n"), "youtube")
    assert "--progress" in falso.argv


def test_inspect_devuelve_el_json_del_motor(runner):
    datos = {"id": "abc", "title": "Un video"}
    motor = _motor(runner(stdout=json.dumps(datos)))
    assert motor.inspect("https://youtu.be/abc") == datos


def test_download_devuelve_la_ruta_impresa_por_el_motor(runner):
    motor = _motor(runner(stdout="C:\\salida\\video.mp4\n"))
    archivo = motor.download("https://youtu.be/abc", "youtube", argv_extra=["-o", "x"])
    assert archivo == Path("C:\\salida\\video.mp4")


def test_download_pasa_el_argv_extra_y_la_url_al_final(runner):
    falso = _descargar(runner(stdout="video.mp4\n"), "youtube")
    assert falso.argv[-1] == "https://example.com/video"
    assert "-o" in falso.argv


def test_download_con_codigo_no_cero_lanza_motor_error_con_el_stderr(runner):
    motor = _motor(runner(codigo=1, stderr="ERROR: Unsupported URL"))
    with pytest.raises(MotorError) as excinfo:
        motor.download("https://example.com/video", "tiktok", argv_extra=[])
    assert "Unsupported URL" in str(excinfo.value)
    assert excinfo.value.codigo == 1
    assert excinfo.value.stderr == "ERROR: Unsupported URL"


def test_download_reporta_el_porcentaje_de_progreso(runner):
    recibido: list[tuple[float | None, str]] = []
    motor = _motor(runner(stdout="video.mp4\n", lineas=(" 42.0%", " 100.0%", " mezclando")))
    motor.download(
        "https://youtu.be/abc",
        "youtube",
        argv_extra=[],
        on_progress=lambda pct, texto: recibido.append((pct, texto)),
    )
    assert (42.0, " 42.0%") in recibido
    assert (100.0, " 100.0%") in recibido


def test_download_reporta_none_cuando_el_porcentaje_no_es_numero(runner):
    recibido: list[tuple[float | None, str]] = []
    motor = _motor(runner(stdout="video.mp4\n", lineas=("NA%",)))
    motor.download(
        "https://youtu.be/abc",
        "youtube",
        argv_extra=[],
        on_progress=lambda pct, texto: recibido.append((pct, texto)),
    )
    assert recibido[-1] == (None, "NA%")


def test_download_sin_ruta_impresa_lanza_motor_error(runner):
    motor = _motor(runner(stdout=""))
    with pytest.raises(MotorError):
        motor.download("https://youtu.be/abc", "youtube", argv_extra=[])
