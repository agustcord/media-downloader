"""Verificación post-descarga con ``ffprobe`` (D9).

Ninguna descarga se reporta como exitosa sin medirla: este módulo es el
instrumento que dice cuántos streams tiene el archivo y con qué códecs. Si el
instrumento no puede leer el archivo, **falla**: preferimos un error explícito a
un "éxito" sobre un archivo que nadie verificó.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .engine import FLAGS_SIN_VENTANA


def streams_de(path: Path | str, ffprobe: str = "ffprobe") -> tuple[int, str | None, str | None]:
    """Devuelve ``(cantidad_de_streams, vcodec, acodec)`` de un archivo.

    ``vcodec``/``acodec`` son ``None`` si el archivo no tiene stream de ese tipo.
    """
    ruta = Path(path)
    if not ruta.exists():
        raise FileNotFoundError(f"No existe el archivo a verificar: {ruta}")
    argv = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,codec_name",
        "-of",
        "json",
        str(ruta),
    ]
    proc = subprocess.run(argv, capture_output=True, text=True, **FLAGS_SIN_VENTANA)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe no pudo leer {ruta}: {proc.stderr.strip()}")
    datos = json.loads(proc.stdout or "{}")
    streams = datos.get("streams") or []

    def _codec(tipo: str) -> str | None:
        for stream in streams:
            if stream.get("codec_type") == tipo:
                return stream.get("codec_name")
        return None

    return len(streams), _codec("video"), _codec("audio")
