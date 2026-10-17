@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "UPDATE_PY=python"
if exist "%~dp0venv\Scripts\python.exe" set "UPDATE_PY=%~dp0venv\Scripts\python.exe"
echo Dang cap nhat Edge TTS de sua loi HTTP 403...
"%UPDATE_PY%" -m pip install --upgrade "edge-tts>=7.3.1"
if errorlevel 1 (
  echo Cap nhat that bai. Kiem tra ket noi mang va thu lai.
  pause
  exit /b 1
)
echo Da cap nhat. Dong tool dang chay, sau do mo lai run.bat.
pause
