"""Chequeo de deriva (YouTube, Instagram, TikTok) + centinela de cookies.

Estos tests tocan la red: corren ``bin\\yt-dlp.exe --simulate`` contra URLs
vivas y por eso viven bajo la marca ``deriva``, excluida de la suite por
defecto (``addopts`` en ``pyproject.toml``). Se ejecutan con::

    uv run pytest -m deriva

**Línea base medida el 2026-09-29** (probe T02, URLs declaradas y verificadas
vivas ese día). Si alguna cambia, este archivo debe FALLAR y nombrarla:

- YouTube (``web_embedded``): exit 0 y ``acodec != none`` (hoy ``mp4a.40.2``).
- Instagram (sin cookies): exit 0, con audio (hoy vp9 + aac).
- TikTok (sin cookies): **exit 0 — la línea base YA NO es exit 1**: el
  2026-09-29 salieron 4/6 URLs frescas con exit 0; los bloqueos son POR POST,
  no por IP global. Si esta URL fresca pasa a exit 1, la deriva es real.
- Centinela de R8: ``--cookies-from-browser chrome`` → HOY exit 1 por
  App-Bound Encryption v20. Si pasa a exit 0, R8 desapareció y la decisión
  D3′ (cookies retiradas del MVP) puede reabrirse si cambia el terreno.

El centinela R8 además vela por el estado *estático* de la decisión: que
nadie reactive cookies de navegador por defecto ni en ``config.json``, ni en
los defaults de ``CookieSpec``/``Config``, ni ofreciendo navegadores en la GUI.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from descargador import gui
from descargador.config import Config, RUTA_CONFIG
from descargador.cookies import CookieSpec
from descargador.engine import FLAGS_SIN_VENTANA, RUTA_BINARIO

pytestmark = pytest.mark.deriva

#: Techo de cortesía por corrida: una simulación viva no debería tardar esto.
TECHO_RED: float = 120.0

#: URLs del probe T02 (2026-09-29), declaradas y verificadas vivas ese día.
URL_YOUTUBE = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
URL_INSTAGRAM = "https://www.instagram.com/reel/DIvimWSse4j/"
URL_TIKTOK = "https://www.tiktok.com/@bbc/video/7690991564889328929"

#: ``--print "%(acodec)s|%(vcodec)s"`` emite exactamente este formato.
CODECS_IMPRESOS = re.compile(r"^([A-Za-z0-9._-]+)\|([A-Za-z0-9._-]+)$")


def _simular(argv_extra: list[str], url: str) -> subprocess.CompletedProcess:
    """Corrida de simulación (sin descargar) sobre el binario del proyecto."""
    argv = [
        str(RUTA_BINARIO),
        "--simulate",
        "--no-playlist",
        "--encoding",
        "utf-8",
        *argv_extra,
        url,
    ]
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=TECHO_RED,
        **FLAGS_SIN_VENTANA,
    )


def _codecs_de(proc: subprocess.CompletedProcess) -> tuple[str, str]:
    """Última línea ``acodec|vcodec`` del stdout; falla con mensaje si no está."""
    lineas = [m for linea in proc.stdout.splitlines() if (m := CODECS_IMPRESOS.match(linea.strip()))]
    assert lineas, (
        "el motor no imprimió los códecs esperados; stdout completo:\n"
        f"{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    acodec, vcodec = lineas[-1].group(1), lineas[-1].group(2)
    assert acodec not in ("", "none", "NA"), f"sin stream de audio: acodec={acodec!r}"
    return acodec, vcodec


def test_deriva_youtube():
    """§7.2 línea 1: ``web_embedded`` + filtro ``avc1`` → exit 0 y acodec != none."""
    if not RUTA_BINARIO.exists():
        pytest.skip(f"no existe el binario del motor: {RUTA_BINARIO}")
    proc = _simular(
        [
            "--extractor-args",
            "youtube:player_client=web_embedded",
            "-f",
            "bv*[vcodec^=avc1][height<=720]+ba",
            "--print",
            "%(acodec)s|%(vcodec)s",
        ],
        URL_YOUTUBE,
    )
    assert proc.returncode == 0, (
        "la línea base de YouTube cambió: hoy web_embedded da exit 0 "
        f"(hoy acodec=mp4a.40.2). stderr del motor:\n{proc.stderr.strip()}"
    )
    acodec, _vcodec = _codecs_de(proc)
    assert acodec == "mp4a.40.2", (
        f"el códec de audio de YouTube derivó: hoy es mp4a.40.2, el motor reporta {acodec!r}"
    )


def test_deriva_instagram():
    """§7.2 línea 2: sin cookies → exit 0, con audio (hoy vp9 + aac)."""
    if not RUTA_BINARIO.exists():
        pytest.skip(f"no existe el binario del motor: {RUTA_BINARIO}")
    proc = _simular(
        ["-f", "bv*[height<=720]+ba/b", "--print", "%(acodec)s|%(vcodec)s"],
        URL_INSTAGRAM,
    )
    assert proc.returncode == 0, (
        "la línea base de Instagram cambió: hoy sin cookies da exit 0 (vp9+aac). "
        f"stderr del motor:\n{proc.stderr.strip()}"
    )
    acodec, _vcodec = _codecs_de(proc)
    assert acodec.startswith("mp4a"), (
        "el audio de Instagram derivó: hoy es AAC (el motor lista mp4a.40.5, que "
        f"ffprobe reporta como 'aac'; cf. probe T02), y el motor reporta {acodec!r}"
    )


def test_deriva_tiktok():
    """§7.2 línea 3: sin cookies → exit 0 en URL fresca (la base YA NO es exit 1).

    El 2026-09-29 el probe midió 4/6 URLs frescas con exit 0 y bloqueos POR
    POST (no por IP). Esta URL del probe estaba viva y funcionaba ese día: si
    hoy da exit 1, la línea base derivó y hay que re-medir antes de confiar.
    """
    if not RUTA_BINARIO.exists():
        pytest.skip(f"no existe el binario del motor: {RUTA_BINARIO}")
    proc = _simular(
        ["-f", "bv*[height<=720]+ba/b", "--print", "%(acodec)s|%(vcodec)s"],
        URL_TIKTOK,
    )
    assert proc.returncode == 0, (
        "la línea base de TikTok cambió: el 2026-09-29 esta URL fresca daba exit 0 "
        "sin cookies ni impersonate (4/6 URLs frescas, bloqueo por post). stderr:\n"
        f"{proc.stderr.strip()}"
    )
    acodec, _vcodec = _codecs_de(proc)
    assert acodec == "aac", (
        f"el códec de audio de TikTok derivó: hoy es aac, el motor reporta {acodec!r}"
    )


def test_centinela_r8():
    """R8 en dos capas: nadie reactiva cookies de navegador por defecto.

    Capa estática (lo que la decisión D3′ congela): ``config.json`` sin
    ``browser``, ``CookieSpec()``/``Config()`` por defecto sin flags, y la GUI
    sin navegadores ofrecidos. Capa viva (§7.2 línea 4): hoy
    ``--cookies-from-browser chrome`` da exit 1 por App-Bound Encryption v20;
    si algún día da exit 0, R8 desapareció y la decisión puede reabrirse.
    """
    datos = json.loads(RUTA_CONFIG.read_text(encoding="utf-8"))
    assert datos.get("browser", "") == "", (
        "config.json reactivó cookies de navegador (browser != ''): la vía está "
        "inviabile por App-Bound Encryption v20 (R8) y expone la sesión del usuario"
    )

    assert CookieSpec().to_args() == [], (
        "CookieSpec() por defecto produce flags de cookies: reactiva R8"
    )
    assert CookieSpec.de_config(Config()).to_args() == [], (
        "Config() por defecto deriva en flags de cookies: reactiva R8"
    )

    fuente_gui = Path(gui.__file__).read_text(encoding="utf-8").lower()
    assert "cookies-from-browser" not in fuente_gui, (
        "la GUI invoca --cookies-from-browser: reactiva R8"
    )
    for navegador in ("chrome", "brave", "edge", "firefox"):
        assert navegador not in fuente_gui, (
            f"la GUI menciona/ofrece el navegador {navegador!r}: reactiva R8"
        )

    if not RUTA_BINARIO.exists():
        pytest.skip(f"no existe el binario del motor: {RUTA_BINARIO}")
    proc = _simular(["--cookies-from-browser", "chrome"], URL_YOUTUBE)
    assert proc.returncode != 0, (
        "R8 DESAPARECIÓ: --cookies-from-browser chrome ya NO falla (exit 0). "
        "Las cookies del navegador vuelven a ser viables: reabrir la decisión 2 "
        "de cookies (D3′) en el diseño. stderr del motor:\n"
        f"{proc.stderr.strip()}"
    )
