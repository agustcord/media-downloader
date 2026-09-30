"""Tests de ``descargador.config``: valores por defecto de D11 y validación.

El último test lee el ``config.json`` real del proyecto: es el que cierra una
reactivación silenciosa de R8 (``browser`` volviendo a "brave") desde el disco.
"""

from __future__ import annotations

import json

import pytest

from descargador.config import (
    RUTA_PROYECTO,
    Config,
    Opciones,
    cargar,
    resolver_salida,
)


def test_config_por_defecto_cumple_decision_d11():
    cfg = Config()
    assert cfg.player_client == "web_embedded"
    assert cfg.browser == ""
    assert cfg.impersonate == ""
    assert cfg.cookies_file == ""
    assert cfg.max_height == 1080
    assert cfg.modo == "video"
    assert cfg.output_dir == "downloads"


def test_codec_policy_por_defecto_es_asimetrica_por_plataforma():
    cfg = Config()
    assert cfg.codec_policy == {"youtube": "nativo", "instagram": "nativo", "tiktok": "h264"}


def test_politica_codec_devuelve_la_de_cada_plataforma():
    cfg = Config()
    assert cfg.politica_codec("youtube") == "nativo"
    assert cfg.politica_codec("instagram") == "nativo"
    assert cfg.politica_codec("tiktok") == "h264"


def test_politica_codec_con_plataforma_desconocida_lanza_value_error():
    cfg = Config()
    with pytest.raises(ValueError):
        cfg.politica_codec("vimeo")


def test_cargar_desde_json_toma_los_valores_del_archivo(tmp_path):
    ruta = tmp_path / "config.json"
    ruta.write_text(
        json.dumps(
            {
                "browser": "",
                "profile": "",
                "cookies_file": "",
                "output_dir": "salida",
                "modo": "audio",
                "max_height": 720,
                "audio_format": "m4a",
                "player_client": "web_embedded",
                "impersonate": "",
                "codec_policy": {"youtube": "nativo", "instagram": "h264", "tiktok": "h264"},
                "filename_template": "%(title)s.%(ext)s",
            }
        ),
        encoding="utf-8",
    )
    cfg = cargar(ruta)
    assert cfg.output_dir == "salida"
    assert cfg.modo == "audio"
    assert cfg.max_height == 720
    assert cfg.audio_format == "m4a"
    assert cfg.politica_codec("instagram") == "h264"


def test_cargar_archivo_inexistente_lanza_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        cargar(tmp_path / "no_existe.json")


def test_cargar_con_codec_policy_incompleto_lanza_value_error(tmp_path):
    ruta = tmp_path / "config.json"
    ruta.write_text(json.dumps({"codec_policy": {"youtube": "nativo"}}), encoding="utf-8")
    with pytest.raises(ValueError):
        cargar(ruta)


def test_cargar_con_politica_de_codec_desconocida_lanza_value_error(tmp_path):
    ruta = tmp_path / "config.json"
    ruta.write_text(
        json.dumps(
            {"codec_policy": {"youtube": "vp9", "instagram": "nativo", "tiktok": "h264"}}
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        cargar(ruta)


def test_opciones_por_defecto_piden_video_hasta_1080p():
    op = Opciones()
    assert op.modo == "video"
    assert op.max_height == 1080
    assert op.audio_format == "mp3"


def test_opciones_con_modo_inexistente_lanza_value_error():
    with pytest.raises(ValueError):
        Opciones(modo="pelicula")


def test_opciones_con_altura_no_positiva_lanza_value_error():
    with pytest.raises(ValueError):
        Opciones(max_height=0)


def test_resolver_salida_absuelve_el_directorio_relativo(tmp_path):
    cfg = Config(output_dir=str(tmp_path / "downloads"))
    assert resolver_salida(cfg) == tmp_path / "downloads"


def test_resolver_salida_relativa_se_resuelve_contra_la_raiz_del_proyecto():
    cfg = Config(output_dir="downloads")
    assert resolver_salida(cfg) == (RUTA_PROYECTO / "downloads").resolve()


def test_el_config_json_del_proyecto_cumple_d11():
    cfg = cargar(RUTA_PROYECTO / "config.json")
    assert cfg.player_client == "web_embedded"
    assert cfg.browser == ""
    assert cfg.impersonate == ""
    assert cfg.cookies_file == ""
    assert cfg.codec_policy == {"youtube": "nativo", "instagram": "nativo", "tiktok": "h264"}
