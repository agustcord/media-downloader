"""Único módulo que ejecuta el binario de yt-dlp (D1).

Decisiones horneadas (ambas medidas empíricamente):
- ``web_embedded`` es el único ``player_client`` de YouTube que funciona en
  yt-dlp 2026.08.19; se inyecta **sólo** cuando la plataforma es YouTube.
- Las cookies se pasan según ``CookieSpec``; con el default (R8) no se inyecta
  ninguna flag, así que las descargas son anónimas.

El runner es inyectable: los tests construyen el argv con un doble y verifican
las flags sin red.

Dos defectos corregidos acá, ambos medidos sobre el binario real (2026-09-29)
con un MP4 servido por HTTP en ``127.0.0.1`` para no depender de la red:

**B1 — la codepage de Windows corrompía la ruta (P0).** Con stdout en pipe,
yt-dlp escribe con la codepage ANSI (cp1252), no UTF-8: la ``í`` de una ruta
de usuario con acento (p. ej. ``C:\\Users\\Usuario\\…``) salía como ``0xED``
y al decodificarla como UTF-8 llegaba ``U+FFFD`` → el ``Path`` señalado no
existía → **toda** descarga terminaba en "no se pudo verificar". Medido: sin
la flag el byte venía como ``b'…\\xedn…'``, con ``--encoding utf-8`` como
``b'…\\xc3\\xadn…'``. La flag se inyecta en las
tres corridas porque es una propiedad del proceso, no de la operación.

**B2 — el porcentaje se perdía.** ``--print`` *implica* ``--quiet`` (lo dice el
``--help``), y eso silencia la barra de progreso. ``--progress`` —"Show progress
bar, even if in quiet mode", también del ``--help``— la restaura **sin** perder
la ruta: medido, el stdout trae ``  3.4%`` … ``100.0%`` y la ruta impresa por
``after_move:filepath`` sigue siendo la última línea. No hizo falta ningún canal
alternativo para el destino.

**B3 — la ventana de la terminal.** yt-dlp
es un ejecutable de consola: en Windows, cada arranque abre una ventana negra.
Se arranca con ``CREATE_NO_WINDOW`` **y** un ``STARTUPINFO`` con
``STARTF_USESHOWWINDOW``/``SW_HIDE``. El mismo juego de banderas se usa para el
``taskkill`` del D12, porque si no, la cura abre otra ventana.

**D12 — reloj y cancelación.** La corrida se sondea cada ``PASO_SONDEO``: si
vence ``timeout_seg`` o si el evento de cancelación se prende, se mata el
**árbol** de procesos (``taskkill /F /T`` en Windows) y se levanta
``TimeoutDescargaError`` / ``CanceladoError``. Matar sólo al padre dejaría vivo
al ``ffmpeg`` del merge: medido en ``tests\\test_timeout_cancel.py`` con un hijo
que late en un archivo.
"""

from __future__ import annotations

import json
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import RUTA_PROYECTO
from .cookies import CookieSpec
from .platforms import detectar, normalizar

RUTA_BINARIO: Path = RUTA_PROYECTO / "bin" / "yt-dlp.exe"

#: Cada cuánto se mira el reloj y el evento de cancelación. Define la latencia
#: máxima de un Cancelar: 0,2 s es imperceptible para el usuario y no agrega
#: carga medible (la cola despierta en cuanto el motor escribe una línea).
PASO_SONDEO: float = 0.2

#: Cuánto se espera a que un proceso muerto suelte sus pipes y su código.
ESPERA_CIERRE: float = 5.0

#: D5′: medido como el único cliente funcional; da AAC (mp4a.40.2) listo para Drift.
EXTRACTOR_ARGS_YOUTUBE = "youtube:player_client=web_embedded"

#: B1: sin esto, la ruta impresa llega en cp1252 y no se puede resolver en disco.
ARGS_ENCODING: list[str] = ["--encoding", "utf-8"]

#: B2: ``--print`` implica ``--quiet``; esta flag devuelve la barra de progreso.
ARGS_PROGRESO: list[str] = ["--progress"]

_PLANTILLA_PROGRESO = "download:%(progress._percent_str)s"
_PORCENTAJE = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")
OnProgress = Callable[[float | None, str], None]


class MotorError(RuntimeError):
    """El motor salió con error. ``stderr`` se conserva para mostrárselo al usuario."""

    def __init__(self, mensaje: str, codigo: int = -1, stderr: str = "") -> None:
        super().__init__(mensaje)
        self.codigo = codigo
        self.stderr = stderr


class CanceladoError(RuntimeError):
    """El usuario canceló: el proceso se mató y **no** hay archivo que entregar."""


class TimeoutDescargaError(RuntimeError):
    """Venció el reloj: el proceso se mató y **no** hay archivo que entregar."""


def _flags_sin_ventana() -> dict:
    """Banderas para que Windows no abra una consola al arrancar el subproceso.

    B3: sin esto, cada descarga abre una ventana negra. Se aplica a las tres
    corridas del motor y también al ``taskkill`` de ``_matar_arbol``.
    """
    if sys.platform != "win32":
        return {}
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0  # SW_HIDE
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": info,
    }


#: B3: calculado una vez; es inmutable en la práctica y se reutiliza en cada arranque.
FLAGS_SIN_VENTANA: dict = _flags_sin_ventana()


def texto_duracion(segundos: float) -> str:
    """``1800`` → ``"30 minutos"``; ``45`` → ``"45 segundos"``. Para el usuario."""
    if segundos >= 60:
        valor, unidad = round(segundos / 60), "minutos"
    else:
        valor, unidad = round(segundos), "segundos"
    return f"{valor} {unidad[:-1]}" if valor == 1 else f"{valor} {unidad}"


@dataclass(frozen=True)
class Salida:
    """Resultado de una corrida del binario."""

    codigo: int = 0
    stdout: str = ""
    stderr: str = ""


def _leer_hasta_eof(stream, cola: queue.Queue) -> None:
    """Lee el pipe en un hilo propio y marca el final con ``None``.

    Hace falta un hilo porque el sondeo del reloj vive en el *otro* lado: si la
    lectura bloqueara al que pregunta por el *timeout*, el reloj nunca vencería.
    """
    try:
        if stream is not None:
            for linea in stream:
                cola.put(linea)
    except (ValueError, OSError):
        pass  # el pipe se cerró porque matamos al proceso
    finally:
        cola.put(None)


def _guardar_todo(stream, contenedor: list[str]) -> None:
    """Junta el stderr en un hilo: leerlo después puede trabar el pipe."""
    try:
        if stream is not None:
            contenedor.append(stream.read())
    except (ValueError, OSError):
        pass


def _matar_arbol(proc) -> None:
    """Mata el proceso **y sus hijos** (D12).

    yt-dlp invoca ``ffmpeg`` para el merge; matar sólo al padre deja un
    ``ffmpeg.exe`` vivo escribiendo en el directorio de salida. En Windows se
    usa ``taskkill /F /T`` que es la única vía sin dependencias externas.
    """
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=ESPERA_CIERRE,
                **FLAGS_SIN_VENTANA,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    if proc.poll() is None:
        proc.kill()  # red: si taskkill no existiera, al menos muere el padre
    try:
        proc.wait(timeout=ESPERA_CIERRE)
    except subprocess.TimeoutExpired:
        pass


def _ejecutar(
    argv: list[str],
    on_line: Callable[[str], None],
    timeout_seg: float | None = None,
    cancel: threading.Event | None = None,
) -> Salida:
    """Corre el binario entregando su stdout línea a línea, con reloj y cancel.

    ``timeout_seg=None`` (o ``<= 0``) significa **sin límite**, que es el
    comportamiento de siempre. ``cancel`` es un ``threading.Event`` que la GUI
    puede prender desde el botón Cancelar.

    Contrato: si el reloj vence o se cancela, el proceso se mata **y** se
    levanta la excepción correspondiente. Nunca se devuelve una ``Salida`` de
    una corrida que se interrumpió.
    """
    if cancel is not None and cancel.is_set():
        raise CanceladoError("Descarga cancelada antes de empezar.")

    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **FLAGS_SIN_VENTANA,
    )
    cola: queue.Queue = queue.Queue()
    errores: list[str] = []
    lector = threading.Thread(
        target=_leer_hasta_eof, args=(proc.stdout, cola), name="descargador-lectura", daemon=True
    )
    lector.start()
    lector_stderr = threading.Thread(
        target=_guardar_todo, args=(proc.stderr, errores), name="descargador-stderr", daemon=True
    )
    lector_stderr.start()

    inicio = time.monotonic()
    lineas: list[str] = []
    try:
        while True:
            if cancel is not None and cancel.is_set():
                _matar_arbol(proc)
                raise CanceladoError("Descarga cancelada por el usuario.")
            transcurrido = time.monotonic() - inicio
            if timeout_seg is not None and timeout_seg > 0 and transcurrido >= timeout_seg:
                _matar_arbol(proc)
                raise TimeoutDescargaError(
                    f"Se agotó el tiempo de espera ({texto_duracion(timeout_seg)}) "
                    "y el proceso de descarga se detuvo."
                )
            try:
                item = cola.get(timeout=PASO_SONDEO)
            except queue.Empty:
                if not lector.is_alive():
                    break  # el lector murió: no va a llegar nada más
                continue
            if item is None:
                break
            lineas.append(item)
            on_line(item.rstrip("\r\n"))
    finally:
        lector.join(ESPERA_CIERRE)
        lector_stderr.join(ESPERA_CIERRE)

    codigo = proc.wait(timeout=ESPERA_CIERRE)
    return Salida(codigo=codigo, stdout="".join(lineas), stderr="".join(errores))


def _porcentaje(linea: str) -> float | None:
    """Porcentaje de la línea de progreso, o ``None`` si no es interpretable."""
    hallado = _PORCENTAJE.search(linea)
    if hallado is None:
        return None
    try:
        return float(hallado.group(1))
    except ValueError:
        return None


def _ultima_ruta(stdout: str) -> str | None:
    """Última línea no vacía del stdout: la ruta que imprime ``after_move:filepath``.

    Con ``--progress`` el stdout ya no es sólo la ruta: antes vienen las líneas
    de porcentaje. Sigue valiendo la última porque ``after_move`` es la etapa
    final del pipeline (medido: ``100.0%`` … ``100.0%`` y después la ruta).
    """
    for linea in reversed(stdout.splitlines()):
        if linea.strip():
            return linea.strip()
    return None


class YtdlpEngine:
    """Envoltorio fino sobre ``bin\\yt-dlp.exe``."""

    def __init__(
        self,
        binario: Path | str | None = None,
        runner: Callable[[list[str], Callable[[str], None]], Salida] | None = None,
    ) -> None:
        self.binario = Path(binario) if binario is not None else RUTA_BINARIO
        self._runner = runner or _ejecutar

    def _correr(
        self,
        argv: list[str],
        on_progress: OnProgress | None = None,
        timeout_seg: float | None = None,
        cancel: threading.Event | None = None,
    ) -> Salida:
        def _entregar(linea: str) -> None:
            if on_progress is not None:
                on_progress(_porcentaje(linea), linea)

        salida = self._runner(argv, _entregar, timeout_seg=timeout_seg, cancel=cancel)
        if salida.codigo != 0:
            raise MotorError(
                f"yt-dlp salió con código {salida.codigo}: {salida.stderr.strip()}",
                codigo=salida.codigo,
                stderr=salida.stderr,
            )
        return salida

    def version(self, timeout_seg: float | None = None, cancel: threading.Event | None = None) -> str:
        """Versión del binario (visible en la GUI para detectar deriva)."""
        salida = self._correr(
            [str(self.binario), *ARGS_ENCODING, "--version"],
            timeout_seg=timeout_seg,
            cancel=cancel,
        )
        return salida.stdout.strip()

    def inspect(
        self,
        url: str,
        cookies: CookieSpec | None = None,
        timeout_seg: float | None = None,
        cancel: threading.Event | None = None,
    ) -> dict:
        """Metadatos del contenido sin descargarlo.

        Acepta el mismo ``timeout_seg``/``cancel`` que ``download`` para que la
        GUI pueda interrumpir una inspección con la misma API (D12).
        """
        limpia = normalizar(url)
        try:
            plataforma = detectar(limpia)
        except ValueError:
            plataforma = ""
        argv = [str(self.binario), *ARGS_ENCODING, "--simulate", "--no-warnings"]
        argv += (cookies or CookieSpec()).to_args()
        if plataforma == "youtube":
            argv += ["--extractor-args", EXTRACTOR_ARGS_YOUTUBE]
        argv += ["--dump-single-json", limpia]
        salida = self._correr(argv, timeout_seg=timeout_seg, cancel=cancel)
        try:
            return json.loads(salida.stdout)
        except json.JSONDecodeError as exc:
            raise MotorError(
                f"yt-dlp no devolvió JSON válido: {exc}",
                codigo=salida.codigo,
                stderr=salida.stderr,
            ) from exc

    def download(
        self,
        url: str,
        plataforma: str,
        argv_extra: list[str] | None = None,
        cookies: CookieSpec | None = None,
        on_progress: OnProgress | None = None,
        timeout_seg: float | None = None,
        cancel: threading.Event | None = None,
    ) -> Path:
        """Descarga ``url`` y devuelve la ruta del archivo resultante.

        ``timeout_seg`` y ``cancel`` son los de D12: ``None`` significa "sin
        límite / no cancelable", que es el comportamiento anterior. Si el reloj
        vence o se cancela, levanta ``TimeoutDescargaError`` /
        ``CanceladoError`` y **no** devuelve ninguna ruta.
        """
        argv = [str(self.binario), *ARGS_ENCODING]
        argv += (cookies or CookieSpec()).to_args()
        if plataforma == "youtube":
            argv += ["--extractor-args", EXTRACTOR_ARGS_YOUTUBE]
        argv += list(argv_extra or [])
        argv += [
            "--newline",
            *ARGS_PROGRESO,
            "--progress-template",
            _PLANTILLA_PROGRESO,
            "--print",
            "after_move:filepath",
            url,
        ]
        salida = self._correr(argv, on_progress, timeout_seg=timeout_seg, cancel=cancel)
        ruta = _ultima_ruta(salida.stdout)
        if ruta is None:
            raise MotorError(
                "yt-dlp terminó sin reportar la ruta del archivo descargado",
                codigo=salida.codigo,
                stderr=salida.stderr,
            )
        return Path(ruta)
