@echo off
REM Build standalone Windows executables (no Python needed on the target PC).
cd /d "%~dp0"
python -m pip install pyinstaller >nul
rmdir /s /q build 2>nul
python -m PyInstaller --onefile --console --name CapitalBot --distpath dist --workpath build --specpath build run_bot.py
python -m PyInstaller --onefile --console --name CapitalBacktest --distpath dist --workpath build --specpath build run_backtest.py
python -m PyInstaller --onefile --console --name CapitalCheck --distpath dist --workpath build --specpath build check_connection.py
python -m PyInstaller --onefile --console --name CapitalHistory --distpath dist --workpath build --specpath build export_history.py
python -m PyInstaller --onefile --console --name CapitalOrderTest --distpath dist --workpath build --specpath build order_test.py
copy /y config.example.json dist\config.example.json >nul
copy /y start_bot.bat dist\start_bot.bat >nul
copy /y README.md dist\README.md >nul
echo.
echo Build done: dist\CapitalBot.exe, CapitalBacktest.exe, CapitalCheck.exe, CapitalHistory.exe
pause
