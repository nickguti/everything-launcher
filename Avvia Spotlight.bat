@echo off
rem Avvia Everything Launcher senza finestra di console.
rem Alla prima esecuzione crea l'ambiente virtuale e installa le dipendenze.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo Prima esecuzione: creo l'ambiente virtuale e installo le dipendenze...
    where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
    if errorlevel 1 (
        echo.
        echo Python non trovato. Installa Python 3.12 da https://www.python.org/downloads/
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Installazione delle dipendenze non riuscita.
        pause
        exit /b 1
    )
)

start "" ".venv\Scripts\pythonw.exe" "main.py"
