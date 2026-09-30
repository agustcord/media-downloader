@echo off
REM ==========================================================================
REM  descargador.bat - Lanzador de Media Downloader
REM
REM  Uso:
REM    doble clic                    -> abre la GUI (sin consola)
REM    descargador.bat --self-test   -> imprime el interprete elegido y sale
REM    descargador.bat <argumentos>  -> python -m descargador <argumentos>
REM
REM  Diseno anti-R7 (no confiar en el PATH):
REM    El primer python.exe del PATH es el empaquetado de WorkBuddy 3.13.12 y
REM    NO tiene tkinter. Por eso este lanzador prueba "import tkinter" en cada
REM    candidato y solo lo acepta si pasa. Orden:
REM      1. %~dp0.venv\Scripts\pythonw.exe
REM      2. py -3.14
REM      3. py -3.13
REM
REM  Nota de encoding: este archivo es ASCII PURO a proposito. cmd no garantiza
REM  la decodificacion de un .bat UTF-8 y la ruta del proyecto tiene caracteres
REM  no ASCII; el ASCII evita cualquier mojibake en pantalla.
REM ==========================================================================
REM --- Contrato de encoding (importante para quien consuma --self-test) -------
REM La consola de Windows arranca en cp850 en esta maquina, pero Python decodifica
REM la salida de los subprocesos como UTF-8. Sin la linea siguiente, la "i"
REM acentuada de la ruta del proyecto viaja como un byte cp850 y el consumidor
REM la reconstruye corrupta: el interprete impreso deja de existir en disco.
REM Con chcp 65001 este lanzador SIEMPRE emite UTF-8, y el consumidor puede hacer
REM   subprocess.run([...], capture_output=True, text=True, encoding="utf-8")
REM sin sorpresas. Es seguro porque este archivo es ASCII puro: cambiar la pagina
REM de codigos a mitad de ejecucion no altera la lectura de sus propias lineas.
chcp 65001 >nul 2>&1

setlocal EnableExtensions

set "ROOT=%~dp0"
set "ARG1=%~1"

cd /d "%ROOT%"
if errorlevel 1 (
    echo ERROR: no se pudo establecer "%ROOT%" como directorio de trabajo.
    call :pausa
    exit /b 3
)

REM El paquete vive en tools\, no en la raiz. Con esto cualquier interprete
REM (incluidos los de fallback) puede hacer -m descargador sin estar instalado.
if defined PYTHONPATH (
    set "PYTHONPATH=%ROOT%tools;%PYTHONPATH%"
) else (
    set "PYTHONPATH=%ROOT%tools"
)

set "SELFTEST=0"
if /i "%ARG1%"=="--self-test" set "SELFTEST=1"

echo ==========================================================
echo  Descargador de redes
echo ==========================================================
echo  Raiz       : %ROOT%
echo  PYTHONPATH : %PYTHONPATH%

REM ------------------------------------------------------------- 1. el motor
if not exist "%ROOT%bin\yt-dlp.exe" (
    echo(
    echo ERROR: falta el motor "%ROOT%bin\yt-dlp.exe".
    echo(
    echo   Descargalo asi ^(requiere conexion^):
    echo     curl -L -o "bin\yt-dlp.exe" https://github.com/yt-dlp/yt-dlp/releases/download/2026.08.19/yt-dlp.exe
    echo(
    call :pausa
    exit /b 2
)

set "YTVER="
for /f "delims=" %%v in ('"%ROOT%bin\yt-dlp.exe" --version 2^>nul') do set "YTVER=%%v"
if not defined YTVER set "YTVER=desconocida ^(el binario no responde^)"
echo  yt-dlp     : %YTVER%

REM ------------------------------------------- 2. interprete CON tkinter (R7)
set "PYEXE="
set "PYWEXE="

call :probar "%ROOT%.venv\Scripts\pythonw.exe"
if not defined PYEXE call :lanzador "py -3.14"
if not defined PYEXE call :lanzador "py -3.13"

if not defined PYEXE (
    echo(
    echo ERROR: ningun interprete Python de esta maquina tiene tkinter.
    echo(
    echo   Se probaron, en este orden:
    echo     1. %ROOT%.venv\Scripts\pythonw.exe
    echo     2. py -3.14
    echo     3. py -3.13
    echo(
    echo   Causa conocida ^(riesgo R7^): el primer python.exe del PATH es el
    echo   empaquetado de WorkBuddy y no incluye tkinter. Este lanzador
    echo   deliberadamente NO lo usa, aunque sea el primero del PATH.
    echo(
    echo   Solucion: ejecutar   uv sync   en esta carpeta. Eso crea .venv con
    echo   CPython y Tcl/Tk 8.6, que es el candidato 1.
    echo(
    call :pausa
    exit /b 4
)

echo  Consola    : %PYEXE%
echo  GUI        : %PYWEXE%

REM --------------------------------------------------------- 3. diagnosticos
where ffmpeg.exe >nul 2>&1
if errorlevel 1 (
    echo  AVISO: ffmpeg.exe no esta en el PATH. Sin ffmpeg no se pueden unir
    echo         video y audio: las descargas saldrian SIN audio.
) else (
    echo  ffmpeg     : OK
)

where deno.exe >nul 2>&1
if errorlevel 1 (
    echo  AVISO: deno.exe no esta en el PATH. Es opcional: solo lo usan los
    echo         extractores que resuelven retos en JavaScript.
) else (
    echo  deno       : OK
)

REM ------------------------------------------------------------ 4. self-test
if "%SELFTEST%"=="1" (
    echo(
    echo SELFTEST completo: interprete resuelto, GUI no abierta.
    echo INTERPRETE=%PYEXE%
    echo PYTHONW=%PYWEXE%
    exit /b 0
)

REM --------------------------------------------------------------- 5. lanzar
echo(
if not defined ARG1 (
    echo Abriendo GUI sin consola: "%PYWEXE%" -m descargador
    start "" "%PYWEXE%" -m descargador
    exit /b 0
)

echo Ejecutando con consola: "%PYEXE%" -m descargador %*
"%PYEXE%" -m descargador %*
exit /b %ERRORLEVEL%

REM ==========================================================================
REM  Subrutinas
REM ==========================================================================

:pausa
REM Solo se pausa en doble clic (sin argumentos). Una invocacion automatizada
REM nunca debe quedar esperando una tecla.
if not defined ARG1 pause
exit /b 0

:probar
REM %1 = ruta completa a un ejecutable candidato. Lo acepta solo si importa
REM tkinter. pythonw no escribe en consola, pero SI devuelve exit code.
if not exist "%~1" exit /b 1
"%~1" -c "import tkinter" >nul 2>&1
if errorlevel 1 exit /b 1
call :aceptar "%~1"
exit /b 0

:lanzador
REM %1 = comando del launcher de Python, por ejemplo "py -3.14".
REM Primero resuelve la ruta real del interprete y luego lo prueba.
set "RESOLVED="
for /f "delims=" %%i in ('%~1 -c "import sys; print(sys.executable)" 2^>nul') do set "RESOLVED=%%i"
if not defined RESOLVED exit /b 1
if not exist "%RESOLVED%" exit /b 1
"%RESOLVED%" -c "import tkinter" >nul 2>&1
if errorlevel 1 exit /b 1
call :aceptar "%RESOLVED%"
exit /b 0

:aceptar
REM %1 = ejecutable que ya paso la prueba de tkinter. Deriva la pareja
REM consola/GUI: python.exe <-> pythonw.exe en el mismo directorio.
set "BASE=%~1"
set "BDIR=%~dp1"
set "BNAME=%~nx1"
if /i "%BNAME%"=="python.exe" (
    set "PYEXE=%BASE%"
    if exist "%BDIR%pythonw.exe" (
        set "PYWEXE=%BDIR%pythonw.exe"
    ) else (
        set "PYWEXE=%BASE%"
    )
) else (
    set "PYWEXE=%BASE%"
    if exist "%BDIR%python.exe" (
        set "PYEXE=%BDIR%python.exe"
    ) else (
        set "PYEXE=%BASE%"
    )
)
exit /b 0
