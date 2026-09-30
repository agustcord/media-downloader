"""Tests de ``descargador.platforms``: detección y normalización de URLs.

Puros, sin red y sin subprocess. Cada caso negativo existe para que un cambio
futuro que acepte "cualquier URL" no pase inadvertido.
"""

from __future__ import annotations

import pytest

from descargador.platforms import PLATAFORMAS, detectar, normalizar


def test_detecta_youtube_en_sus_tres_hosts():
    assert detectar("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "youtube"
    assert detectar("https://youtu.be/dQw4w9WgXcQ") == "youtube"
    assert detectar("https://m.youtube.com/shorts/abc123") == "youtube"


def test_detecta_instagram_en_reel_y_post():
    assert detectar("https://www.instagram.com/reel/ABC123/?igsh=zzz") == "instagram"
    assert detectar("https://instagram.com/p/ABC123/") == "instagram"


def test_detecta_tiktok_en_sus_dos_hosts():
    assert detectar("https://www.tiktok.com/@usuario/video/7123456789") == "tiktok"
    assert detectar("https://vm.tiktok.com/ZSabcd/") == "tiktok"


def test_detecta_url_de_otro_dominio_lanza_value_error():
    with pytest.raises(ValueError):
        detectar("https://example.com")


def test_detecta_texto_vacio_lanza_value_error():
    with pytest.raises(ValueError):
        detectar("")


def test_detecta_texto_que_no_es_url_lanza_value_error():
    with pytest.raises(ValueError):
        detectar("hola mundo")


def test_plataformas_conocidas_son_las_tres_del_pedido():
    assert set(PLATAFORMAS) == {"youtube", "instagram", "tiktok"}


def test_normalizar_quita_parametro_igsh():
    url = "https://www.instagram.com/reel/ABC123/?igsh=MzRlODBiNWFlZA=="
    assert normalizar(url) == "https://www.instagram.com/reel/ABC123/"


def test_normalizar_quita_parametro_feature():
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&feature=share"
    assert normalizar(url) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_normalizar_quita_el_fragmento():
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ#t=30s"
    assert normalizar(url) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_normalizar_conserva_el_parametro_v_de_youtube():
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&igsh=abc"
    assert normalizar(url) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_normalizar_minusculiza_el_host_sin_tocar_la_ruta():
    url = "https://WWW.Instagram.com/Reel/ABC123/"
    assert normalizar(url) == "https://www.instagram.com/Reel/ABC123/"


def test_normalizar_es_idempotente():
    url = "https://www.instagram.com/reel/ABC123/?igsh=zzz#frag"
    una_vez = normalizar(url)
    assert normalizar(una_vez) == una_vez


def test_normalizar_url_vacia_lanza_value_error():
    with pytest.raises(ValueError):
        normalizar("   ")
