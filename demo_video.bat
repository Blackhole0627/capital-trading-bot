@echo off
REM One-click walkthrough for recording a demo video. Start your screen recorder first.
cd /d "%~dp0"
mode con: cols=120 lines=45
python demo_video.py --config config.json
pause
