"""Cierra el riesgo R7: el lanzador elige un intérprete **con tkinter** (criterio 2).

El primer ``python.exe`` del PATH en esta máquina es el empaquetado de WorkBuddy
y **no** tiene tkinter, así que este test no puede limitarse a comprobar que el
``.bat`` imprime algo: comprueba que lo que imprime existe, importa tkinter y no
es el intérprete de WorkBuddy.

Decodificación: la salida del ``.bat`` es UTF-8 con CRLF (el lanzador hace
``chcp 65001``), así que se decodifica utf-8 y se hace ``.strip()``; el CR
residual rompería la ruta del intérprete.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

RUTA_PROYECTO = Path(__file__).resolve().parents[1]
BAT = RUTA_PROYECTO / "descargador.bat"

#: El intérprete que NO sirve (riesgo R7). Si aparece, el test falla a propósito.
MARCAS_PROHIBIDAS = ("workbuddy",)


def _correr_self_test() -> subprocess.CompletedProcess:
    """Ejecuta ``descargador.bat --self-test`` y devuelve el resultado crudo."""
    return subprocess.run(
        ["cmd", "/c", str(BAT), "--self-test"],
        cwd=str(RUTA_PROYECTO),
        capture_output=True,
        timeout=180,
    )


def _linea_self_test(salida: bytes, clave: str) -> str:
    """Valor de una línea ``CLAVE=...``, decodificado y sin el CR final."""
    texto = salida.decode("utf-8")  # el .bat emite UTF-8 (chcp 65001)
    for linea in texto.splitlines():
        if linea.startswith(clave + "="):
            return linea.split("=", 1)[1].strip()  # el CR residual rompe la ruta
    raise AssertionError(f"El .bat no imprimió {clave}=. Salida:\n{texto}")


def _linea_self_test_cruda(salida: bytes, clave: str) -> str:
    """Igual que la anterior pero partiendo sólo por ``\\n``: conserva el CR."""
    texto = salida.decode("utf-8")
    for linea in texto.split("\n"):
        if linea.startswith(clave + "="):
            return linea.split("=", 1)[1]
    raise AssertionError(f"El .bat no imprimió {clave}=. Salida:\n{texto}")


@pytest.fixture(scope="module")
def salida_self_test() -> subprocess.CompletedProcess:
    """El ``.bat`` se corre una sola vez por módulo: tarda unos segundos."""
    return _correr_self_test()


@pytest.fixture(scope="module")
def interprete(salida_self_test) -> str:
    """Ruta del intérprete elegido por el lanzador, ya sin CR."""
    return _linea_self_test(salida_self_test.stdout, "INTERPRETE")


def test_el_lanzador_existe():
    assert BAT.is_file(), f"Falta el lanzador: {BAT}"


def test_self_test_termina_bien(salida_self_test):
    assert salida_self_test.returncode == 0, (
        f"El self-test falló: {salida_self_test.stdout.decode('utf-8', 'replace')}"
    )


def test_el_interprete_impreso_existe_en_disco(interprete):
    assert interprete, "INTERPRETE= vino vacío"
    assert Path(interprete).is_file(), f"El intérprete impreso no existe: {interprete!r}"


def test_el_interprete_impreso_importa_tkinter(interprete):
    probe = subprocess.run(
        [interprete, "-c", "import tkinter; print(tkinter.TkVersion)"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert probe.returncode == 0, f"{interprete} no importa tkinter: {probe.stderr}"
    assert probe.stdout.strip(), "tkinter importó pero no reportó versión"


def test_el_interprete_impreso_no_es_el_de_workbuddy(interprete):
    """Si imprime el Python de WorkBuddy, FALLA: es justo el riesgo R7."""
    for marca in MARCAS_PROHIBIDAS:
        assert marca not in interprete.lower(), (
            f"El lanzador eligió un intérprete prohibido ({marca}): {interprete}"
        )


def test_la_salida_es_crlf_y_por_eso_exige_strip(salida_self_test):
    """Caso negativo: sin ``.strip()`` el CR se come la ruta del intérprete.

    Medido en esta máquina: ``Path(interprete_con_CR).is_file()`` es ``False`` y
    pasarle esa ruta a ``subprocess`` lanza ``FileNotFoundError``.
    """
    assert b"\r\n" in salida_self_test.stdout, "Se esperaba salida CRLF (consola de Windows)"
    limpia = _linea_self_test(salida_self_test.stdout, "INTERPRETE")
    cruda = _linea_self_test_cruda(salida_self_test.stdout, "INTERPRETE")
    assert cruda.endswith("\r"), "Partiendo por \\n el CR queda pegado: por eso hace falta strip()"
    assert Path(limpia).is_file()
    assert not Path(cruda).is_file(), "Sin strip() el CR sobra: la ruta no resuelve"
