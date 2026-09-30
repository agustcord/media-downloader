"""Tests del selector de formato por plataforma (D4′).

Sin red y sin yt-dlp: se inspecciona el argv. El selector único de v1
(``bv*[vcodec^=avc1]...`` para las tres plataformas) mata Instagram y TikTok con
"Requested format is not available"; estos tests son la alarma que suena si
alguien lo reinstala.
"""

from __future__ import annotations

import pytest

from descargador.config import Opciones
from descargador.core import selector_de_formato


def _formato(argv: list[str]) -> str:
    """Valor que sigue a ``-f``."""
    assert "-f" in argv, f"el argv no lleva -f: {argv}"
    return argv[argv.index("-f") + 1]


def test_selector_de_youtube_filtra_avc1_y_1080():
    argv = selector_de_formato("youtube", Opciones())
    formato = _formato(argv)
    assert "vcodec^=avc1" in formato
    assert "height<=1080" in formato
    assert formato.endswith("/b")


def test_selector_de_instagram_no_lleva_avc1():
    formato = _formato(selector_de_formato("instagram", Opciones()))
    assert "avc1" not in formato


def test_selector_de_tiktok_no_lleva_avc1():
    formato = _formato(selector_de_formato("tiktok", Opciones()))
    assert "avc1" not in formato


def test_selector_de_instagram_lleva_yes_playlist():
    assert "--yes-playlist" in selector_de_formato("instagram", Opciones())


def test_selector_de_tiktok_no_lleva_yes_playlist():
    assert "--yes-playlist" not in selector_de_formato("tiktok", Opciones())


def test_selector_de_youtube_no_lleva_yes_playlist():
    assert "--yes-playlist" not in selector_de_formato("youtube", Opciones())


@pytest.mark.parametrize("plataforma", ["youtube", "instagram", "tiktok"])
def test_las_tres_plataformas_llevan_merge_mp4_y_windows_filenames(plataforma):
    argv = selector_de_formato(plataforma, Opciones())
    assert "--merge-output-format" in argv
    assert argv[argv.index("--merge-output-format") + 1] == "mp4"
    assert "--windows-filenames" in argv


@pytest.mark.parametrize("plataforma", ["youtube", "instagram", "tiktok"])
def test_el_selector_respeta_la_altura_pedida(plataforma):
    argv = selector_de_formato(plataforma, Opciones(max_height=720))
    assert "height<=720" in _formato(argv)
    assert "height<=1080" not in _formato(argv)


def test_el_selector_de_audio_no_pide_video():
    argv = selector_de_formato("youtube", Opciones(modo="audio", audio_format="m4a"))
    assert "--extract-audio" in argv
    assert argv[argv.index("--audio-format") + 1] == "m4a"
    assert "--merge-output-format" not in argv


def test_selector_con_plataforma_desconocida_lanza_value_error():
    with pytest.raises(ValueError):
        selector_de_formato("vimeo", Opciones())
