@echo off
chcp 65001 >nul
title Nambeo Pro - AI Video Suite
cd /d "%~dp0"

echo ================================================================
echo           NAMBEO PRO - AI VIDEO AUTOMATION SUITE
echo ================================================================
echo.

set "PYTHON_CMD=python"
if exist "%~dp0venv\Scripts\python.exe" (
    echo [*] Phat hien moi truong ao venv...
    set "PYTHON_CMD=%~dp0venv\Scripts\python.exe"
)

echo [*] Dang kiem tra Python...
%PYTHON_CMD% --version >nul 2>&1
if errorlevel 1 goto :no_python

echo [*] Dang khoi dong may chu va mo trinh duyet...
%PYTHON_CMD% start_app.py

if errorlevel 1 goto :error_exit
goto :eof

:no_python
echo.
echo [LOI] Khong tim thay Python tren may tinh cua ban!
echo Vui long chay file cai_dat.bat truoc de cai dat moi truong.
echo Hoac tai Python tai: https://www.python.org/downloads/
echo.
pause
exit /b 1

:error_exit
echo.
echo [CANH BAO] Ung dung bi dung hoac xay ra loi!
echo Neu day la lan dau tien su dung, vui long chay file: cai_dat.bat
echo.
pause
exit /b 1
