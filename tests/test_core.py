"""Tests de ``descargador.core``: orquestación de una descarga.

El motor es un doble: nada toca la red. Lo que se mide acá es el contrato que
consume la GUI: ``Resultado`` honesto, avisos en vez de éxitos
silenciosos, y aplicación de la política de códecs por plataforma (D4′).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from descargador import core
from descargador.config import Config, Opciones
from descargador.core import MENSAJE_CANCELADO, Resultado, download
from descargador.engine import CanceladoError, MotorError
from descargador.verify import streams_de


class MotorFalso:
    """Doble de ``YtdlpEngine``: copia un fixture al destino o falla.

    Acepta ``timeout_seg`` y ``cancel`` porque el núcleo se los pasa (D12): si
    el doble no los declarara, el ``TypeError`` avisaría del cambio de interfaz.
    Honra la cancelación para que un test futuro no crea que la ignora.
    """

    def __init__(self, entregar: Path | None = None, destino: Path | None = None, error: str | None = None):
        self.entregar = entregar
        self.destino = destino
        self.error = error
        self.argv_recibido: list[str] = []
        self.llamadas = 0
        self.timeout_seg: float | None = None
        self.cancel = None

    def version(self) -> str:
        return "2026.08.19"

    def inspect(self, url: str, cookies=None) -> dict:
        return {"id": "falso", "title": "Video falso"}

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
        self.argv_recibido = list(argv_extra or [])
        self.timeout_seg = timeout_seg
        self.cancel = cancel
        if cancel is not None and cancel.is_set():
            raise CanceladoError(MENSAJE_CANCELADO)
        if self.error is not None:
            raise MotorError(self.error, codigo=1, stderr=self.error)
        assert self.destino is not None and self.entregar is not None
        copia = self.destino / self.entregar.name
        shutil.copyfile(self.entregar, copia)
        return copia


def _cfg(tmp_path: Path, **kwargs) -> Config:
    return Config(output_dir=str(tmp_path / "downloads"), **kwargs)


def test_motor_que_falla_devuelve_resultado_no_ok_y_no_crea_archivos(tmp_path):
    salida = tmp_path / "downloads"
    motor = MotorFalso(error="ERROR: Unable to extract data")
    resultado = download(
        "https://www.tiktok.com/@u/video/1",
        Opciones(),
        _cfg(tmp_path, codec_policy={"youtube": "nativo", "instagram": "nativo", "tiktok": "h264"}),
        motor,
    )
    assert resultado.ok is False
    assert "Unable to extract data" in (resultado.error or "")
    assert resultado.archivo is None
    archivos = list(salida.iterdir()) if salida.exists() else []
    assert archivos == []


def test_url_de_otro_dominio_devuelve_resultado_no_ok(tmp_path):
    resultado = download("https://example.com", Opciones(), _cfg(tmp_path), MotorFalso(error="nunca"))
    assert resultado.ok is False
    assert resultado.error is not None
    assert "youtube" in resultado.error or "instagram" in resultado.error or "tiktok" in resultado.error


def test_descarga_exitosa_devuelve_archivo_existente_y_sin_aviso(tmp_path, fabricar_mp4):
    entregar = fabricar_mp4("ok.mp4", audio=True)
    motor = MotorFalso(entregar=entregar, destino=tmp_path / "downloads")
    resultado = download("https://youtu.be/abc", Opciones(), _cfg(tmp_path), motor)
    assert resultado.ok is True
    assert resultado.aviso is None
    assert resultado.archivo is not None
    assert resultado.archivo.exists()
    assert resultado.archivo.stat().st_size > 0


def test_politica_h264_recodifica_tiktok(tmp_path, fabricar_mp4):
    entregar = fabricar_mp4("tiktok_hevc.mp4", vcodec="libx265", audio=True)
    motor = MotorFalso(entregar=entregar, destino=tmp_path / "downloads")
    resultado = download(
        "https://www.tiktok.com/@u/video/1",
        Opciones(),
        _cfg(tmp_path, codec_policy={"youtube": "nativo", "instagram": "nativo", "tiktok": "h264"}),
        motor,
    )
    assert resultado.ok is True
    assert resultado.archivo is not None
    assert resultado.archivo.name == "tiktok_hevc_h264.mp4"
    assert streams_de(resultado.archivo)[1] == "h264"
    assert resultado.aviso is None


def test_politica_nativo_no_recodifica_aunque_el_codec_no_sea_h264(tmp_path, fabricar_mp4):
    entregar = fabricar_mp4("mpeg4.mp4", vcodec="mpeg4", audio=True)
    motor = MotorFalso(entregar=entregar, destino=tmp_path / "downloads")
    resultado = download(
        "https://www.youtube.com/watch?v=abc",
        Opciones(),
        _cfg(tmp_path, codec_policy={"youtube": "nativo", "instagram": "nativo", "tiktok": "h264"}),
        motor,
    )
    assert resultado.ok is True
    assert resultado.archivo == motor.destino / "mpeg4.mp4"
    assert streams_de(resultado.archivo)[1] == "mpeg4"


def test_descarga_sin_audio_avisa_y_es_ok(tmp_path, fabricar_mp4):
    entregar = fabricar_mp4("mudo.mp4", audio=False)
    motor = MotorFalso(entregar=entregar, destino=tmp_path / "downloads")
    resultado = download("https://youtu.be/abc", Opciones(), _cfg(tmp_path), motor)
    assert resultado.ok is True
    assert resultado.aviso is not None
    assert "SIN audio" in resultado.aviso


def test_si_la_normalizacion_falla_se_entrega_el_original_y_se_avisa(tmp_path, fabricar_mp4, monkeypatch):
    entregar = fabricar_mp4("tt.mp4", vcodec="libx265", audio=True)
    motor = MotorFalso(entregar=entregar, destino=tmp_path / "downloads")

    def _falla(*_args, **_kwargs):
        raise core.NormalizeError("ffmpeg no respondió")

    monkeypatch.setattr(core, "a_h264", _falla)
    resultado = download(
        "https://www.tiktok.com/@u/video/1",
        Opciones(),
        _cfg(tmp_path, codec_policy={"youtube": "nativo", "instagram": "nativo", "tiktok": "h264"}),
        motor,
    )
    assert resultado.ok is True
    assert resultado.archivo == motor.destino / "tt.mp4"
    assert resultado.aviso is not None
    assert "H.264" in resultado.aviso


def test_core_pasa_al_motor_el_selector_de_la_plataforma(tmp_path, fabricar_mp4):
    entregar = fabricar_mp4("ig.mp4", audio=True)
    motor = MotorFalso(entregar=entregar, destino=tmp_path / "downloads")
    download("https://www.instagram.com/reel/ABC/", Opciones(), _cfg(tmp_path), motor)
    assert "--yes-playlist" in motor.argv_recibido
    assert "-o" in motor.argv_recibido


def test_resultado_es_inmutable():
    resultado = Resultado(ok=True, archivo=None, aviso=None, error=None)
    with pytest.raises(Exception):
        resultado.ok = False
