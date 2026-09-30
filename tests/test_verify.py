"""Tests de ``descargador.verify``: el instrumento se valida contra lo conocido.

No alcanza con que ``ffprobe`` corra sin excepción: si no distingue un MP4 con
audio de uno sin audio, la red de seguridad de D9 miente y este test FALLA.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from descargador import verify
from descargador.verify import streams_de

ES_WINDOWS = sys.platform == "win32"
REQUIERE_WINDOWS = pytest.mark.skipif(
    not ES_WINDOWS, reason="las banderas de ventana oculta son de Windows"
)


def test_mp4_con_video_y_audio_da_dos_streams(fabricar_mp4):
    archivo = fabricar_mp4("con_audio.mp4", audio=True)
    n, vcodec, acodec = streams_de(archivo)
    assert n == 2
    assert vcodec == "h264"
    assert acodec == "aac"


def test_mp4_solo_video_da_un_stream(fabricar_mp4):
    archivo = fabricar_mp4("sin_audio.mp4", audio=False)
    n, vcodec, acodec = streams_de(archivo)
    assert n == 1
    assert vcodec == "h264"
    assert acodec is None


def test_el_instrumento_distingue_los_dos_casos(fabricar_mp4):
    con_audio = fabricar_mp4("a.mp4", audio=True)
    sin_audio = fabricar_mp4("b.mp4", audio=False)
    assert streams_de(con_audio)[0] != streams_de(sin_audio)[0]


def test_mp4_en_hevc_se_reporta_como_hevc(fabricar_mp4):
    archivo = fabricar_mp4("hevc.mp4", vcodec="libx265", audio=True)
    n, vcodec, acodec = streams_de(archivo)
    assert n == 2
    assert vcodec == "hevc"


def test_archivo_inexistente_lanza_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        streams_de(tmp_path / "no_existe.mp4")


def test_archivo_que_no_es_media_lanza_runtime_error(tmp_path):
    basura = tmp_path / "basura.mp4"
    basura.write_text("esto no es un video", encoding="utf-8")
    with pytest.raises(RuntimeError):
        streams_de(basura)


# ------------------------------------------- B3 en el renglón 6 (enmienda E1)


@REQUIERE_WINDOWS
def test_ffprobe_arranca_sin_ventana_de_consola(monkeypatch, tmp_path):
    """B3 aplica también a ffprobe: corre en CADA descarga (``core.py``).

    Mismo estándar que ``test_timeout_cancel.py``: ``subprocess.run`` espiado en
    el módulo bajo prueba, función real invocada con un resultado falso.
    **Falla si se quita la bandera:** quitarla abre una consola visible en
    cada descarga.
    """
    archivo = tmp_path / "falso.mp4"
    archivo.write_bytes(b"x")  # existe; el espía impide que ffprobe corra de verdad
    capturadas: list[dict] = []

    def espia(argv, **kwargs):
        capturadas.append(dict(kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout='{"streams": []}', stderr="")

    monkeypatch.setattr(verify.subprocess, "run", espia)
    streams_de(archivo)

    assert capturadas, "no se registró ninguna corrida: el espía está mal puesto"
    kwargs = capturadas[0]
    assert kwargs.get("creationflags", 0) & subprocess.CREATE_NO_WINDOW, (
        "ffprobe sin CREATE_NO_WINDOW → Windows abre una consola visible en cada descarga"
    )
    info = kwargs.get("startupinfo")
    assert info is not None, "ffprobe sin STARTUPINFO → la ventana no se oculta"
    assert info.dwFlags & subprocess.STARTF_USESHOWWINDOW, (
        "ffprobe: STARTUPINFO sin STARTF_USESHOWWINDOW"
    )
    assert info.wShowWindow == 0, "ffprobe: wShowWindow no oculta la ventana"
