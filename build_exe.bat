@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
echo.
echo ============================================
echo   Build Pronostici App v11 - EXE
echo ============================================
echo.

:: ------------------------------------------------------------------
:: Trova una versione di Python usabile (>= 3.10).
:: Prova in ordine: py -3.12 / -3.13 / -3.14 / -3, poi python.
:: Serve >= 3.10 perche' Flask + pywebview recenti non supportano 3.9.
:: ------------------------------------------------------------------
set "PY="
for %%V in (3.12 3.13 3.14 3) do (
    py -%%V --version >nul 2>&1 && set "PY=py -%%V" && goto :pyfound
)
python --version >nul 2>&1 && set "PY=python"
:pyfound

if "!PY!"=="" (
    echo [ERRORE] Nessuna installazione Python trovata.
    echo Installa Python 3.12 o superiore da https://www.python.org/downloads/
    echo Al momento dell'installazione flagga "Add python.exe to PATH" e "py launcher".
    pause
    exit /b 1
)

echo Uso !PY!
!PY! --version
echo.

:: ------------------------------------------------------------------
:: Verifica che il sorgente esista
:: ------------------------------------------------------------------
if not exist pronostici_app_v11.py (
    echo [ERRORE] pronostici_app_v11.py non trovato in questa cartella.
    pause
    exit /b 1
)

:: ------------------------------------------------------------------
:: [1/3] Installa/aggiorna dipendenze
:: ------------------------------------------------------------------
echo [1/3] Installazione dipendenze...
!PY! -m pip install --quiet --upgrade pyinstaller flask requests beautifulsoup4 pywebview
if errorlevel 1 (
    echo.
    echo [ERRORE] Installazione dipendenze fallita.
    pause
    exit /b 1
)
echo       OK

:: ------------------------------------------------------------------
:: [2/3] Pulizia build precedente
:: ------------------------------------------------------------------
echo [2/3] Pulizia build precedente...
if exist dist\PronosticiCalcio.exe del /q dist\PronosticiCalcio.exe
if exist PronosticiCalcio.exe del /q PronosticiCalcio.exe
if exist build rmdir /s /q build
echo       OK

:: ------------------------------------------------------------------
:: [3/3] Build vera e propria
:: ------------------------------------------------------------------
echo [3/3] Compilazione in corso (attendere, puo' richiedere 1-2 minuti)...
!PY! -m PyInstaller --noconfirm pronostici_app_v11.spec
if errorlevel 1 (
    echo.
    echo [ERRORE] PyInstaller ha restituito un errore.
    pause
    exit /b 1
)

:: Verifica che l'exe sia stato creato davvero
if not exist dist\PronosticiCalcio.exe (
    echo.
    echo [ERRORE] Build non riuscita: dist\PronosticiCalcio.exe non e' stato creato.
    pause
    exit /b 1
)

:: Copia l'exe nella cartella corrente per comodita'
copy /y dist\PronosticiCalcio.exe . >nul

echo.
echo ============================================
echo   BUILD COMPLETATA con successo!
echo   File: PronosticiCalcio.exe
echo ============================================
echo.
echo NOTA: tieni PronosticiCalcio.exe e pronostici_data.json
echo       nella stessa cartella.
echo.
pause
