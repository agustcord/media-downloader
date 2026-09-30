"""Detección y normalización de URLs de redes sociales.

Módulo puro: sin red, sin subprocess y sin estado. Es la única pieza que decide
a qué plataforma pertenece un link, y por eso es también la única que legitima
inyectar flags específicas de plataforma más abajo (``engine``, ``core``).
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

PLATAFORMAS: tuple[str, ...] = ("youtube", "instagram", "tiktok")

#: Host canónico por plataforma. Se compara por sufijo para cubrir
#: ``www.``, ``m.`` y acortadores (``youtu.be``, ``vm.tiktok.com``).
_SUFIJOS: tuple[tuple[str, str], ...] = (
    ("youtube.com", "youtube"),
    ("youtu.be", "youtube"),
    ("instagram.com", "instagram"),
    ("tiktok.com", "tiktok"),
)

#: Parámetros de rastreo que se descartan al normalizar. El resto de la query
#: se conserva: ``v`` de YouTube es parte de la identidad del video, no rastreo.
_RASTREO: frozenset[str] = frozenset({"igsh", "feature", "ig_rid"})


def _anfitrion(url: str) -> str:
    """Host de la URL en minúsculas, sin puerto ni credenciales."""
    netloc = urlparse(url).netloc
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
    if ":" in netloc:
        netloc = netloc.split(":", 1)[0]
    return netloc.strip().lower().rstrip(".")


def detectar(url: str) -> str:
    """Devuelve ``"youtube"``, ``"instagram"`` o ``"tiktok"``.

    Lanza ``ValueError`` si la URL no pertenece a ninguna plataforma soportada:
    aceptar "cualquier URL" abriría la puerta a pasarle al motor links para los
    que no tenemos selector ni política de códecs.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError(f"URL vacía o no es texto: {url!r}")
    anfitrion = _anfitrion(url)
    if not anfitrion:
        raise ValueError(f"URL sin host: {url!r}")
    for sufijo, plataforma in _SUFIJOS:
        if anfitrion == sufijo or anfitrion.endswith("." + sufijo):
            return plataforma
    raise ValueError(
        f"Plataforma no soportada: {anfitrion}. "
        f"Soportadas: {', '.join(PLATAFORMAS)}."
    )


def normalizar(url: str) -> str:
    """Quita el fragmento y los parámetros de rastreo; conserva el resto.

    ``?igsh=`` y ``&feature=`` son los que Instagram y YouTube inyectan al
    compartir; se van porque ensucian el log y no identifican al contenido.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError(f"URL vacía o no es texto: {url!r}")
    partes = urlparse(url.strip())
    if not partes.netloc:
        raise ValueError(f"URL sin host: {url!r}")
    query = urlencode(
        [(clave, valor) for clave, valor in parse_qsl(partes.query) if clave.lower() not in _RASTREO]
    )
    return urlunparse(
        (partes.scheme.lower(), partes.netloc.lower(), partes.path, partes.params, query, "")
    )
