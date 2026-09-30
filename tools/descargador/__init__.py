"""Paquete del descargador de contenido de redes sociales.

Núcleo: ``platforms`` (detección y normalización de URLs),
``config`` (config.json y opciones), ``cookies`` (R8: sin cookies por defecto),
``engine`` (único módulo que ejecuta ``bin\\yt-dlp.exe``), ``core`` (descarga,
selector por plataforma y política de códecs), ``verify`` (ffprobe) y
``normalize`` (HEVC → H.264). La interfaz gráfica vive en ``gui``.

Ningún módulo del núcleo importa tkinter: sólo ``gui`` y ``__main__`` lo hacen.
"""

__version__ = "0.1.0"
