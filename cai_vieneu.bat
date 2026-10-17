@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "TTS_PY=python"
if exist "venv\Scripts\python.exe" set "TTS_PY=%~dp0venv\Scripts\python.exe"
"%TTS_PY%" scripts\setup_vieneu.py --device cuda
if errorlevel 1 (
  echo Cai VieNeu that bai. Kiem tra thong bao phia tren.
  pause
  exit /b 1
)
echo VieNeu san sang. Mo lai ung dung va chon giong VieNeu.
pause
