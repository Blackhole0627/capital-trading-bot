@echo off
REM Install Python dependencies (run once). Requires Python 3.10+ from python.org.
cd /d "%~dp0"
python --version >nul 2>&1 || (echo Python no encontrado. Instalalo desde https://www.python.org/downloads/ y marca "Add to PATH". & pause & exit /b 1)
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if not exist config.json copy config.example.json config.json
echo.
echo Listo. Edita config.json con tu API key de Capital.com y ejecuta start_bot.bat
pause
