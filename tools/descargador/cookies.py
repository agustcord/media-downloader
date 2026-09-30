"""Especificación de cookies para el motor.

Estado real: Chrome 154 / Edge 153-154 /
Brave 154 de esta máquina cifran las cookies con App-Bound Encryption (v20) y
yt-dlp falla en duro al intentar leerlas. Por eso ``CookieSpec()`` —el valor por
defecto— **no produce ninguna flag**: las descargas son anónimas y no exponen la
sesión del usuario.

La vía a futuro (``--cookies <archivo Netscape>`` exportado a mano) queda
implementada y desactivada: se activa poniendo una ruta en ``config.json``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # sólo para tipar; evita el ciclo config <-> cookies
    from .config import Config


@dataclass(frozen=True)
class CookieSpec:
    """Cómo (o si) hay que pasar cookies al motor."""

    browser: str = ""  # "" | "brave" | "chrome" | "edge" ... → --cookies-from-browser
    profile: str = ""  # perfil dentro del navegador, si hiciera falta
    archivo: str = ""  # Netscape .txt exportado a mano → --cookies

    def to_args(self) -> list[str]:
        """Flags de cookies para yt-dlp. ``[]`` cuando no se usan (default)."""
        if self.archivo:
            return ["--cookies", self.archivo]
        if self.browser:
            objetivo = f"{self.browser}:{self.profile}" if self.profile else self.browser
            return ["--cookies-from-browser", objetivo]
        return []

    @classmethod
    def de_config(cls, cfg: "Config") -> "CookieSpec":
        """Construye la especificación desde la configuración persistida."""
        return cls(
            browser=getattr(cfg, "browser", ""),
            profile=getattr(cfg, "profile", ""),
            archivo=getattr(cfg, "cookies_file", ""),
        )
