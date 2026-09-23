@echo off
REM AURA - run.bat
REM Sets up a virtual environment (first run only), installs dependencies,
REM and starts AURA. Safe to run repeatedly.

echo ========================================
echo  AURA - Personal Desktop Assistant
echo ========================================

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH.
    echo Please install Python 3.10 or later from https://www.python.org/downloads/
    echo and make sure "Add python.exe to PATH" is checked during install.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo Creating virtual environment in .venv ...
    python -m venv .venv
)

call .venv\Scripts\activate.bat

echo Installing/updating dependencies ...
pip install --disable-pip-version-check -q -r requirements.txt

if not exist ".env" (
    echo No .env found - copying .env.example to .env
    copy .env.example .env >nul
    echo Add your GROQ_API_KEY to .env for full conversational ability.
    echo Without a key AURA still runs, using its offline parser.
)

echo Starting AURA ...
python -m app.main

pause
