@echo off
cd /d "%~dp0"
python server.py
if errorlevel 1 (
  echo.
  echo Cropper could not start. Install the free dependencies with:
  echo python -m pip install -r requirements.txt
  pause
)
