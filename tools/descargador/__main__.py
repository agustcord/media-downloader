"""Punto de entrada ``python -m descargador``.

Es el único lugar, junto con ``gui.py``, que toca tkinter. Lo primero que hace
es comprobar que el intérprete lo tiene: es la segunda barrera contra el riesgo
R7 (ya hay una en ``descargador.bat``, que elige intérprete probando
``import tkinter``).

Si ``gui.py`` todavía no existe, este módulo lo
dice y sale con código distinto de cero en vez de abrir una ventana vacía.
"""

from __future__ import annotations

import sys


def _avisar(texto: str) -> None:
    """Escribe en stderr si existe (con pythonw.exe los streams son None)."""
    stream = sys.stderr or sys.stdout
    if stream is not None:
        print(texto, file=stream)


def main() -> int:
    try:
        import tkinter
    except ImportError:
        _avisar(
            "Este intérprete de Python no tiene tkinter, así que no puede abrir la "
            "ventana. Usá descargador.bat: prueba intérpretes hasta encontrar uno "
            "con tkinter (riesgo R7)."
        )
        return 2

    try:
        from .gui import App
    except ImportError:
        _avisar(
            "La interfaz gráfica (tools\\descargador\\gui.py) todavía no existe: "
            "La GUI todavía no está disponible. El núcleo ya está implementado "
            "(platforms, config, cookies, engine, core, verify, normalize)."
        )
        return 1

    from .config import RUTA_CONFIG, cargar
    from .engine import YtdlpEngine

    root = tkinter.Tk()
    App(root, cargar(RUTA_CONFIG), YtdlpEngine())
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
