@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install Python 3 from https://www.python.org/downloads/
  pause
  exit /b 1
)
python app.py
pause
