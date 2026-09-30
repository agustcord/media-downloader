"""Configuración persistida (``config.json``) y opciones de una descarga.

Los valores por defecto son los de la decisión **D11** del plan v2:
``player_client = "web_embedded"`` (medido como el único cliente de
YouTube que funciona), ``browser`` e ``impersonate`` vacíos (R8 y P3), y una
política de códecs asimétrica por plataforma (D4′).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .platforms import PLATAFORMAS

RUTA_PROYECTO: Path = Path(__file__).resolve().parents[2]
RUTA_CONFIG: Path = RUTA_PROYECTO / "config.json"

MODOS: tuple[str, ...] = ("video", "audio")
POLITICAS_DE_CODEC: tuple[str, ...] = ("nativo", "h264")

#: D4′: YouTube e Instagram se entregan como vienen; TikTok se recodifica a
#: H.264 porque HEVC no está entre los formatos que acepta Drift.
CODEC_POLICY_POR_DEFECTO: dict[str, str] = {
    "youtube": "nativo",
    "instagram": "nativo",
    "tiktok": "h264",
}

PLANTILLA_POR_DEFECTO = "%(title).80s [%(id)s].%(ext)s"

#: D12: 30 minutos. Holgado para "mejor hasta 1080p" en uso personal (1-3
#: descargas por semana). ``None`` o ``0`` significa *sin límite*.
TIMEOUT_POR_DEFECTO: int = 1800

#: D13: el HEVC es la copia de máxima calidad y borrarlo es irreversible, así
#: que la limpieza es **opt-in**.
LIMPIAR_ORIGINAL_POR_DEFECTO: bool = False

#: D13/D12: los ``.part`` de una corrida cortada se limpian por defecto; con
#: ``false`` se conservan y el resultado los nombra.
LIMPIAR_PART_POR_DEFECTO: bool = True


@dataclass(frozen=True)
class Opciones:
    """Qué se le pide al motor para una descarga concreta."""

    modo: str = "video"  # "video" | "audio"
    max_height: int = 1080
    audio_format: str = "mp3"

    def __post_init__(self) -> None:
        if self.modo not in MODOS:
            raise ValueError(f"modo inválido: {self.modo!r}. Válidos: {', '.join(MODOS)}")
        if self.max_height <= 0:
            raise ValueError(f"max_height debe ser positivo, se recibió {self.max_height}")


@dataclass(frozen=True)
class Config:
    """Configuración persistida del descargador."""

    browser: str = ""
    profile: str = ""
    cookies_file: str = ""
    output_dir: str = "downloads"
    modo: str = "video"
    max_height: int = 1080
    audio_format: str = "mp3"
    player_client: str = "web_embedded"
    impersonate: str = ""
    codec_policy: Mapping[str, str] = field(
        default_factory=lambda: dict(CODEC_POLICY_POR_DEFECTO)
    )
    filename_template: str = PLANTILLA_POR_DEFECTO
    timeout_segundos: int | None = TIMEOUT_POR_DEFECTO
    limpiar_original_tras_recodificar: bool = LIMPIAR_ORIGINAL_POR_DEFECTO
    limpiar_part_al_cancelar: bool = LIMPIAR_PART_POR_DEFECTO

    def __post_init__(self) -> None:
        if self.modo not in MODOS:
            raise ValueError(f"modo inválido: {self.modo!r}. Válidos: {', '.join(MODOS)}")
        if self.timeout_segundos is not None and self.timeout_segundos < 0:
            raise ValueError(
                f"timeout_segundos debe ser positivo o 0 (sin límite), se recibió "
                f"{self.timeout_segundos}"
            )
        politica = dict(self.codec_policy)
        faltantes = sorted(set(PLATAFORMAS) - set(politica))
        if faltantes:
            raise ValueError(
                f"codec_policy incompleto; falta(n): {', '.join(faltantes)}"
            )
        for plataforma, valor in politica.items():
            if valor not in POLITICAS_DE_CODEC:
                raise ValueError(
                    f"codec_policy[{plataforma}] = {valor!r} no es válido. "
                    f"Válidos: {', '.join(POLITICAS_DE_CODEC)}"
                )

    def politica_codec(self, plataforma: str) -> str:
        """Política de códecs para una plataforma soportada."""
        if plataforma not in PLATAFORMAS:
            raise ValueError(f"plataforma no soportada: {plataforma!r}")
        return dict(self.codec_policy).get(plataforma, "nativo")

    def opciones(self) -> Opciones:
        """Opciones por defecto derivadas de la configuración."""
        return Opciones(modo=self.modo, max_height=self.max_height, audio_format=self.audio_format)


def cargar(ruta: Path | str = RUTA_CONFIG) -> Config:
    """Lee ``config.json``. Falla en voz alta si falta o si trae valores malos."""
    ruta = Path(ruta)
    if not ruta.exists():
        raise FileNotFoundError(f"No existe el archivo de configuración: {ruta}")
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    if not isinstance(datos, dict):
        raise ValueError(f"config.json debe ser un objeto, se leyó {type(datos).__name__}")
    campos = {
        "browser",
        "profile",
        "cookies_file",
        "output_dir",
        "modo",
        "max_height",
        "audio_format",
        "player_client",
        "impersonate",
        "codec_policy",
        "filename_template",
        "timeout_segundos",
        "limpiar_original_tras_recodificar",
        "limpiar_part_al_cancelar",
    }
    return Config(**{clave: datos[clave] for clave in campos if clave in datos})


def resolver_salida(cfg: Config) -> Path:
    """Directorio de salida absoluto (el ``.bat`` hace ``cd /d "%~dp0"``)."""
    salida = Path(cfg.output_dir)
    if not salida.is_absolute():
        salida = RUTA_PROYECTO / salida
    return salida.resolve()
