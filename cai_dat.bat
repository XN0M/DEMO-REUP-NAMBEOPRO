@echo off
chcp 65001 >nul
title Cai Dat Tu Dong Cho Nambeo Pro
cd /d "%~dp0"

echo ================================================================
echo            CAI DAT TU DONG - NAMBEO PRO (1-CLICK SETUP)
echo ================================================================
echo.

echo [1/4] Kiem tra phien ban Python tren he thong...
python --version >nul 2>&1
if errorlevel 1 goto :need_python

python --version
echo.

echo [2/4] Khoi tao moi truong ao venv...
set "PY_EXEC=python"
if not exist "%~dp0venv\Scripts\python.exe" (
    python -m venv venv
)
if exist "%~dp0venv\Scripts\python.exe" (
    echo [*] Da thiet lap moi truong ao venv thanh cong!
    set "PY_EXEC=%~dp0venv\Scripts\python.exe"
) else (
    echo [CANH BAO] Khong the tao venv, se dung Python he thong.
)
echo.

echo [3/4] Dang cai dat cac thu vien can thiet tu requirements.txt...
echo (Qua trinh nay co the mat 1-3 phut tuy thuoc toc do mang cua ban)
%PY_EXEC% -m pip install --upgrade pip
%PY_EXEC% -m pip install -r requirements.txt
if errorlevel 1 goto :install_error
echo [*] Cai dat cac thu vien thanh cong!
echo.

echo [4/4] Cai dat Playwright Chromium ho tro boc tach video...
%PY_EXEC% -m playwright install chromium
echo.

echo ================================================================
echo         CHUC MUNG! CAI DAT NAMBEO PRO HOAN TAT 100%%
echo ================================================================
echo Bay gio ban co the khoi chay cong cu bang cach mo file: run.bat
echo ================================================================
echo.
pause
goto :eof

:need_python
echo.
echo [LOI] Chua tim thay Python tren may tinh!
echo.
echo HUONG DAN CAI DAT PYTHON:
echo 1. Truy cap: https://www.python.org/downloads/
echo 2. Tai ban Python 3.10 tro len.
echo 3. LUU Y QUAN TRONG: Khi cai dat, nho tich vao o 'Add python.exe to PATH'.
echo 4. Sau khi cai dat xong, hay mo lai file cai_dat.bat nay.
echo.
pause
exit /b 1

:install_error
echo.
echo [LOI] Co loi xay ra khi tai cac goi thu vien!
echo Vui long kiem tra ket noi Internet va thu chay lai cai_dat.bat.
echo.
pause
exit /b 1
