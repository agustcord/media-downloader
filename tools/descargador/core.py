"""Orquestación de una descarga: el contrato que consume la GUI.

Reglas que este módulo sostiene:
- **Nada se reporta como éxito sin medirlo:** tras descargar se corre ``ffprobe``
  y el número de streams decide si hay aviso de "SIN audio" (D9).
- **La política de códecs es por plataforma** (D4′): sólo se recodifica cuando
  la política lo pide y el códec medido no es H.264.
- **Nunca un éxito silencioso:** si la normalización falla, se entrega el
  original y el aviso lo dice. Si el motor falla, el ``stderr`` va en ``error``.

**Reloj y cancelación.** ``download`` acepta ``timeout_seg`` y
``cancel``. Si la corrida se interrumpe, el resultado es ``ok=False`` con un
mensaje que dice qué pasó **y** qué puede hacer el usuario: nunca un éxito sobre
un archivo a medio escribir.

**Política de residuos.** El original HEVC se conserva por
defecto y se declara en ``Resultado.original`` (la GUI lo nombra, no lo borra).
Los ``.part`` de una corrida interrumpida se limpian por defecto
(``limpiar_part_al_cancelar``); con ``false`` se conservan y se declaran.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import Config, Opciones, resolver_salida
from .cookies import CookieSpec
from .engine import (
    CanceladoError,
    MotorError,
    TimeoutDescargaError,
    YtdlpEngine,
    texto_duracion,
)
from .normalize import NormalizeError, a_h264
from .platforms import PLATAFORMAS, detectar, normalizar
from .verify import streams_de

__all__ = [
    "HINT_TIMEOUT",
    "MENSAJE_CANCELADO",
    "Opciones",
    "Resultado",
    "borrar_original_si_es_seguro",
    "download",
    "inspect_url",
    "mensaje_timeout",
    "selector_de_formato",
]

OnProgress = Callable[[float | None, str], None]

#: D12: literal, no construido: la GUI lo compara y el usuario lo lee igual siempre.
MENSAJE_CANCELADO = (
    "Cancelaste la descarga: el proceso se detuvo y no se entregó ningún archivo. "
    "Podés volver a intentarlo."
)

#: D12: la parte accionable del *timeout*. Se compone, no se esconde.
HINT_TIMEOUT = 'Si el material es muy largo, subí "timeout_segundos" en config.json.'

#: Extensiones que yt-dlp deja cuando una descarga se corta a medio camino.
SUFIJOS_RESIDUO: tuple[str, ...] = (".part", ".ytdl")


def mensaje_timeout(segundos: float) -> str:
    """Mensaje completo del *timeout*: qué pasó y qué puede hacer el usuario."""
    return (
        f"Se agotó el tiempo de espera ({texto_duracion(segundos)}) y la descarga se detuvo. "
        f"{HINT_TIMEOUT}"
    )


@dataclass(frozen=True)
class Resultado:
    """Resultado honesto de una descarga."""

    ok: bool
    archivo: Path | None = None
    aviso: str | None = None
    error: str | None = None
    #: D13: original que se conservó tras recodificar (``None`` si no aplica).
    original: Path | None = None
    #: D13: residuos (``.part``/``.ytdl``) que dejó una corrida interrumpida.
    residuos: tuple[Path, ...] = ()


def selector_de_formato(plataforma: str, op: Opciones) -> list[str]:
    """Flags de selección de formato para una plataforma (D4′).

    El filtro ``avc1`` **sólo** vale para YouTube: Instagram ofrece vp9 y TikTok
    entrega h265, y pedirles avc1 mata la descarga con "Requested format is not
    available". El ``/b`` final es la red que evita ese final ante un post raro.
    """
    if plataforma not in PLATAFORMAS:
        raise ValueError(f"plataforma no soportada: {plataforma!r}")
    if op.modo == "audio":
        return ["-f", "ba/b", "--extract-audio", "--audio-format", op.audio_format]

    altura = f"height<={op.max_height}"
    if plataforma == "youtube":
        formato = f"bv*[vcodec^=avc1][{altura}]+ba/bv*[{altura}]+ba/b"
    else:
        formato = f"bv*[{altura}]+ba/b"

    argv = ["-f", formato, "--merge-output-format", "mp4", "--windows-filenames"]
    if plataforma == "instagram":
        argv.append("--yes-playlist")
    return argv


def inspect_url(url: str, cfg: Config, engine: YtdlpEngine) -> dict:
    """Metadatos del link, con la plataforma ya resuelta."""
    limpia = normalizar(url)
    plataforma = detectar(limpia)
    datos = engine.inspect(limpia, CookieSpec.de_config(cfg))
    return {**datos, "plataforma": plataforma, "url": limpia}


def residuos_de(salida: Path, desde: float) -> list[Path]:
    """Archivos incompletos que el motor pudo dejar en ``salida`` desde ``desde``.

    ``desde`` es un ``time.time()`` tomado antes de arrancar: el filtro evita
    tocar residuos de corridas anteriores (hoy no hay concurrentes por I1, pero
    la guarda es barata y hace la limpieza auditable).
    """
    if not salida.exists():
        return []
    return sorted(
        p
        for p in salida.iterdir()
        if p.is_file() and p.name.endswith(SUFIJOS_RESIDUO) and p.stat().st_mtime >= desde - 1.0
    )


def _resultado_interrumpido(
    salida: Path, desde: float, cfg: Config, error: str
) -> Resultado:
    """Resultado de una corrida cortada por cancelación o por *timeout* (D12/D13)."""
    encontrados = residuos_de(salida, desde)
    borrados: list[Path] = []
    conservados: list[Path] = []
    if cfg.limpiar_part_al_cancelar:
        for ruta in encontrados:
            try:
                ruta.unlink()
            except OSError:
                conservados.append(ruta)
            else:
                borrados.append(ruta)
    else:
        conservados = list(encontrados)

    aviso: str | None = None
    if borrados:
        aviso = (
            f"Se eliminaron {len(borrados)} archivo(s) incompleto(s) de la descarga detenida: "
            + ", ".join(p.name for p in borrados)
        )
    elif conservados:
        aviso = (
            "Quedaron archivos incompletos en la carpeta de salida "
            "(podés borrarlos a mano): " + ", ".join(p.name for p in conservados)
        )
    return Resultado(
        ok=False,
        error=error,
        aviso=aviso,
        residuos=tuple(borrados + conservados),
    )


def borrar_original_si_es_seguro(
    original: Path | str,
    reemplazo: Path | str,
    verificador: Callable[[Path | str], tuple[int, str | None, str | None]] = streams_de,
) -> tuple[bool, str]:
    """Borra ``original`` **sólo si** el reemplazo está verificado (D13).

    Nunca se borra contenido descargado sin comprobar que su reemplazo existe,
    pesa más de cero y ``ffprobe`` lo reporta H.264. Devuelve
    ``(se_borró, motivo)``: el motivo es lo que la GUI muestra cuando no se pudo.
    """
    original = Path(original)
    reemplazo = Path(reemplazo)
    if not reemplazo.exists():
        return False, f"no existe el reemplazo {reemplazo.name}"
    if reemplazo.stat().st_size == 0:
        return False, f"el reemplazo {reemplazo.name} quedó vacío"
    try:
        _streams, vcodec, _acodec = verificador(reemplazo)
    except (FileNotFoundError, RuntimeError) as exc:
        return False, f"ffprobe no pudo verificar el reemplazo: {exc}"
    if vcodec != "h264":
        return False, f"el reemplazo no es H.264 (ffprobe reporta {vcodec})"
    try:
        original.unlink()
    except OSError as exc:
        return False, f"no se pudo borrar el original: {exc}"
    return True, ""


def download(
    url: str,
    op: Opciones,
    cfg: Config,
    engine: YtdlpEngine,
    on_progress: OnProgress | None = None,
    timeout_seg: float | None = None,
    cancel: threading.Event | None = None,
) -> Resultado:
    """Descarga ``url`` aplicando la política de códecs y verificando el resultado.

    API de cancelación para la GUI (**no cambiar**)::

        cancel = threading.Event()            # antes de lanzar el hilo
        res = download(url, op, cfg, engine, avance,
                       timeout_seg=cfg.timeout_segundos, cancel=cancel)
        cancel.set()                          # desde el botón Cancelar

    Si se cancela o vence el reloj: ``ok=False``, ``archivo=None`` y un ``error``
    con el mensaje literal (``MENSAJE_CANCELADO`` / ``mensaje_timeout``).
    """
    try:
        limpia = normalizar(url)
        plataforma = detectar(limpia)
    except ValueError as exc:
        return Resultado(
            ok=False,
            error=f"URL no reconocida: {exc} Plataformas soportadas: {', '.join(PLATAFORMAS)}.",
        )

    reloj = cfg.timeout_segundos if timeout_seg is None else timeout_seg
    salida = resolver_salida(cfg)
    salida.mkdir(parents=True, exist_ok=True)
    argv = selector_de_formato(plataforma, op) + [
        "-o",
        str(salida / cfg.filename_template),
    ]
    desde = time.time()
    try:
        archivo = engine.download(
            limpia,
            plataforma,
            argv,
            CookieSpec.de_config(cfg),
            on_progress,
            timeout_seg=reloj,
            cancel=cancel,
        )
    except MotorError as exc:
        return Resultado(ok=False, error=str(exc))
    except CanceladoError:
        return _resultado_interrumpido(salida, desde, cfg, MENSAJE_CANCELADO)
    except TimeoutDescargaError:
        return _resultado_interrumpido(salida, desde, cfg, mensaje_timeout(reloj or 0))

    try:
        streams, vcodec, _acodec = streams_de(archivo)
    except (FileNotFoundError, RuntimeError) as exc:
        return Resultado(
            ok=False,
            error=f"El motor reportó {archivo} pero no se pudo verificar: {exc}",
        )

    avisos: list[str] = []
    original: Path | None = None
    if op.modo == "video" and streams < 2:
        avisos.append(
            f"El archivo se descargó SIN audio (ffprobe reporta {streams} stream)."
        )
    if op.modo == "video" and cfg.politica_codec(plataforma) == "h264" and vcodec != "h264":
        try:
            recodificado = a_h264(archivo)
        except NormalizeError as exc:
            avisos.append(
                f"No se pudo recodificar a H.264 ({exc}); "
                f"se entrega el original en {vcodec}."
            )
        else:
            original = archivo
            archivo = recodificado
            if cfg.limpiar_original_tras_recodificar:
                borrado, motivo = borrar_original_si_es_seguro(original, recodificado)
                if borrado:
                    avisos.append(
                        f"Se eliminó el original {original.name} tras recodificarlo a H.264."
                    )
                else:
                    avisos.append(f"No se eliminó el original {original.name}: {motivo}")
    return Resultado(
        ok=True, archivo=archivo, aviso=" ".join(avisos) or None, original=original
    )
