@echo off
cd /d "%~dp0"
if exist CapitalBot.exe (
    CapitalBot.exe --config config.json
) else (
    python run_bot.py --config config.json
)
pause
