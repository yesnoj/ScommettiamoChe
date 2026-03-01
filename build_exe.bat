@echo off
chcp 65001 >nul
echo.
echo ============================================
echo   Build Pronostici App v10 - EXE
echo ============================================
echo.

:: Verifica Python 3.12
py -3.12 --version >nul 2>&1
if errorlevel 1 (
    echo [ERRORE] Python 3.12 non trovato.
    echo Installa Python 3.12 da https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Installa/aggiorna dipendenze
echo [1/3] Controllo dipendenze...
py -3.12 -m pip install --quiet --upgrade pyinstaller flask requests beautifulsoup4 pywebview
if errorlevel 1 (
    echo [ERRORE] Installazione dipendenze fallita.
    pause
    exit /b 1
)
echo       OK

:: Pulisce build precedente
echo [2/3] Pulizia build precedente...
if exist dist\pronostici_app_v10.exe del /q dist\pronostici_app_v10.exe
if exist build rmdir /s /q build
echo       OK

:: Build
echo [3/3] Compilazione in corso (attendere)...
py -3.12 -m PyInstaller --onefile --noconsole --name "PronosticiCalcio" pronostici_app_v10.py
if errorlevel 1 (
    echo.
    echo [ERRORE] Build fallita. Controlla i messaggi sopra.
    pause
    exit /b 1
)

:: Copia l'exe nella cartella corrente per comodita'
echo.
echo Copia exe nella cartella corrente...
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
