"""Dobles de prueba compartidos.

``RunnerFalso`` sustituye a ``subprocess``: registra el argv que el motor
construye y devuelve una salida prefijada. Es lo que permite verificar la
invección de flags sin red y sin ejecutar yt-dlp.
"""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from descargador.core import MENSAJE_CANCELADO
from descargador.engine import CanceladoError, TimeoutDescargaError


@dataclass
class Salida:
    """Resultado de una corrida del motor (espejo de ``descargador.engine``)."""

    codigo: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass
class RunnerFalso:
    """Doble del runner: guarda cada argv que recibe y emite líneas prefijadas.

    ``demora`` simula segundos de trabajo del motor. No es decorativo: mientras
    "trabaja", el doble **honra el contrato** de ``timeout_seg`` y ``cancel``
    (sondea el reloj y el evento, y levanta la misma excepción que el runner
    real). Así las corridas lentas se prueban en milisegundos y las pruebas de
    proceso real quedan para ``tests\\test_timeout_cancel.py``.
    """

    stdout: str = ""
    stderr: str = ""
    codigo: int = 0
    lineas: tuple[str, ...] = ()
    demora: float = 0.0
    llamadas: list[list[str]] = field(default_factory=list)
    timeout_recibido: float | None = None
    cancel_recibido: threading.Event | None = None

    def __call__(self, argv, on_line, timeout_seg=None, cancel=None):
        self.llamadas.append(list(argv))
        self.timeout_recibido = timeout_seg
        self.cancel_recibido = cancel
        self._trabajar(timeout_seg, cancel)
        for linea in self.lineas:
            on_line(linea)
        return Salida(codigo=self.codigo, stdout=self.stdout, stderr=self.stderr)

    def _trabajar(self, timeout_seg: float | None, cancel: threading.Event | None) -> None:
        inicio = time.monotonic()
        while True:
            if cancel is not None and cancel.is_set():
                raise CanceladoError(MENSAJE_CANCELADO)
            transcurrido = time.monotonic() - inicio
            if timeout_seg is not None and timeout_seg > 0 and transcurrido >= timeout_seg:
                raise TimeoutDescargaError(
                    f"Se agotó el tiempo de espera ({transcurrido:.1f} s) "
                    "y el proceso de descarga se detuvo."
                )
            if transcurrido >= self.demora:
                return
            time.sleep(0.01)

    @property
    def argv(self) -> list[str]:
        """Último argv construido por el motor."""
        if not self.llamadas:
            raise AssertionError("el runner falso no recibió ninguna llamada")
        return self.llamadas[-1]

    def valor_de(self, flag: str) -> str | None:
        """Valor que sigue a ``flag`` en el último argv, o ``None`` si no está."""
        argv = self.argv
        if flag not in argv:
            return None
        indice = argv.index(flag)
        return argv[indice + 1] if indice + 1 < len(argv) else None


@pytest.fixture
def runner():
    """Fábrica de runners falsos; el objeto devuelto expone ``.argv``."""

    def _crear(**kwargs) -> RunnerFalso:
        return RunnerFalso(**kwargs)

    return _crear


def crear_mp4(
    ruta: Path,
    *,
    vcodec: str = "libx264",
    audio: bool = True,
    duracion: float = 2.0,
    tamano: str = "320x240",
) -> Path:
    """Genera un MP4 sintético con ffmpeg. Falla el test si ffmpeg no puede."""
    argv = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={tamano}:rate=10",
    ]
    if audio:
        argv += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100"]
    argv += ["-t", f"{duracion}", "-c:v", vcodec]
    if vcodec == "libx265":
        argv += ["-tag:v", "hvc1"]
    if audio:
        argv += ["-c:a", "aac", "-shortest"]
    argv += [str(ruta)]
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode != 0 or not Path(ruta).exists():
        raise AssertionError(f"ffmpeg no pudo generar {ruta}: {proc.stderr}")
    return Path(ruta)


@pytest.fixture
def fabricar_mp4(tmp_path):
    """Fábrica de MP4 sintéticos dentro del directorio temporal del test."""

    def _crear(nombre: str, **kwargs) -> Path:
        return crear_mp4(tmp_path / nombre, **kwargs)

    return _crear
