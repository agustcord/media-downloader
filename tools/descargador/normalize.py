"""Normalización de códecs con ffmpeg (D10).

Motivo: TikTok entrega HEVC y Drift no lo acepta. Este módulo recodifica a
H.264 con ``libx264``. Es el único lugar del proyecto que llama a ffmpeg, y
falla en voz alta: ``core`` atrapa ``NormalizeError`` y entrega el original
avisando, nunca un éxito silencioso.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .engine import FLAGS_SIN_VENTANA


class NormalizeError(RuntimeError):
    """La recodificación no se pudo completar."""


def a_h264(
    path: Path | str,
    ffmpeg: str = "ffmpeg",
    crf: int = 18,
    preset: str = "veryfast",
) -> Path:
    """Recodifica ``path`` a H.264 + AAC y devuelve la ruta del nuevo archivo.

    La salida se escribe junto al original como ``<nombre>_h264.mp4``: el
    archivo descargado nunca se pisa, así un fallo de ffmpeg no destruye el
    material que ya bajamos.
    """
    entrada = Path(path)
    if not entrada.exists():
        raise NormalizeError(f"No existe el archivo a normalizar: {entrada}")
    salida = entrada.with_name(f"{entrada.stem}_h264.mp4")
    argv = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(entrada),
        "-c:v",
        "libx264",
        "-crf",
        str(crf),
        "-preset",
        preset,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(salida),
    ]
    proc = subprocess.run(argv, capture_output=True, text=True, **FLAGS_SIN_VENTANA)
    if proc.returncode != 0:
        raise NormalizeError(f"ffmpeg salió con código {proc.returncode}: {proc.stderr.strip()}")
    if not salida.exists() or salida.stat().st_size == 0:
        raise NormalizeError(f"ffmpeg no produjo un archivo válido: {salida}")
    return salida
