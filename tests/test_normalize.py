"""Tests de ``descargador.normalize``: HEVC → H.264 con ffmpeg (D10).

Caso real que lo motiva: TikTok entrega HEVC y Drift no lo acepta. El fixture
se genera con ``libx265`` y se verifica que de verdad salió HEVC antes de
pedirle al módulo que lo recodifique; si el encoder no estuviera, el test
falla en la precondición y no en la aserción final.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from descargador import normalize
from descargador.normalize import NormalizeError, a_h264
from descargador.verify import streams_de

ES_WINDOWS = sys.platform == "win32"
REQUIERE_WINDOWS = pytest.mark.skipif(
    not ES_WINDOWS, reason="las banderas de ventana oculta son de Windows"
)


def test_el_fixture_de_entrada_es_realmente_hevc(fabricar_mp4):
    archivo = fabricar_mp4("entrada_hevc.mp4", vcodec="libx265", audio=True)
    n, vcodec, acodec = streams_de(archivo)
    assert n == 2
    assert vcodec == "hevc", "el fixture no salió en HEVC: el test no mediría nada"


def test_a_h264_recodifica_a_h264(fabricar_mp4):
    archivo = fabricar_mp4("entrada_hevc.mp4", vcodec="libx265", audio=True)
    salida = a_h264(archivo)
    assert salida.exists()
    assert salida.stat().st_size > 0
    assert streams_de(salida)[1] == "h264"


def test_a_h264_no_pisa_el_original_y_nombra_con_sufijo(fabricar_mp4):
    archivo = fabricar_mp4("original.mp4", vcodec="libx265", audio=True)
    salida = a_h264(archivo)
    assert salida == archivo.with_name("original_h264.mp4")
    assert archivo.exists()
    assert streams_de(archivo)[1] == "hevc"


def test_a_h264_conserva_el_audio(fabricar_mp4):
    archivo = fabricar_mp4("con_audio.mp4", vcodec="libx265", audio=True)
    salida = a_h264(archivo)
    n, _vcodec, acodec = streams_de(salida)
    assert n == 2
    assert acodec == "aac"


def test_a_h264_con_entrada_inexistente_lanza_normalize_error(tmp_path):
    with pytest.raises(NormalizeError):
        a_h264(tmp_path / "no_existe.mp4")


def test_a_h264_con_archivo_que_no_es_media_lanza_normalize_error(tmp_path):
    basura = tmp_path / "basura.mp4"
    basura.write_text("esto no es un video", encoding="utf-8")
    with pytest.raises(NormalizeError):
        a_h264(basura)


def test_medicion_de_costo_segundos_por_minuto_de_video(fabricar_mp4, capsys):
    """Criterio 6: cronometra la recodificación e imprime s/min.

    El fixture es chico a propósito (el test tiene que correr en segundos); el
    número extrapolado es una referencia de orden de magnitud, no el costo de
    un video real de 1080p, que se mide aparte.
    """
    duracion = 20.0
    archivo = fabricar_mp4("medicion.mp4", vcodec="libx265", audio=True, duracion=duracion)
    inicio = time.perf_counter()
    salida = a_h264(archivo)
    transcurrido = time.perf_counter() - inicio
    segundos_por_minuto = transcurrido / (duracion / 60.0)

    assert salida.exists()
    with capsys.disabled():
        print(
            f"\n[MEDICION] a_h264: {transcurrido:.2f} s para {duracion:.0f} s de video "
            f"-> {segundos_por_minuto:.2f} s por minuto de video (320x240, preset veryfast)"
        )
    assert transcurrido > 0


# ------------------------------------------- B3 en el renglón 6 (enmienda E1)


@REQUIERE_WINDOWS
def test_ffmpeg_arranca_sin_ventana_de_consola(monkeypatch, tmp_path):
    """B3 aplica también a ffmpeg: corre en cada TikTok y puede durar minutos.

    Mismo estándar que ``test_timeout_cancel.py``: ``subprocess.run`` espiado en
    el módulo bajo prueba, función real invocada con un resultado falso.
    **Falla si se quita la bandera:** quitarla abre una consola visible en
    cada recodificación.
    """
    entrada = tmp_path / "entrada.mp4"
    entrada.write_bytes(b"x")  # existe; el espía impide que ffmpeg corra de verdad
    capturadas: list[dict] = []

    def espia(argv, **kwargs):
        capturadas.append(dict(kwargs))
        Path(argv[-1]).write_bytes(b"salida falsa")  # honra el contrato de a_h264
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(normalize.subprocess, "run", espia)
    a_h264(entrada)

    assert capturadas, "no se registró ninguna corrida: el espía está mal puesto"
    kwargs = capturadas[0]
    assert kwargs.get("creationflags", 0) & subprocess.CREATE_NO_WINDOW, (
        "ffmpeg sin CREATE_NO_WINDOW → Windows abre una consola visible en cada recodificación"
    )
    info = kwargs.get("startupinfo")
    assert info is not None, "ffmpeg sin STARTUPINFO → la ventana no se oculta"
    assert info.dwFlags & subprocess.STARTF_USESHOWWINDOW, (
        "ffmpeg: STARTUPINFO sin STARTF_USESHOWWINDOW"
    )
    assert info.wShowWindow == 0, "ffmpeg: wShowWindow no oculta la ventana"
