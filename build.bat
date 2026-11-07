@echo off
setlocal
cd /d "%~dp0"
if not exist .venv (
    py -3 -m venv .venv
    if errorlevel 1 goto :error
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
if errorlevel 1 goto :error
python -m pip install -r requirements.txt nuitka
if errorlevel 1 goto :error
python -m nuitka --onefile --enable-plugin=pyside6 --windows-console-mode=disable --output-dir=dist --output-filename=NtBar.exe ntbar.py
if errorlevel 1 goto :error
echo.
echo Build complete: dist\NtBar.exe
pause
exit /b 0
:error
echo.
echo Build failed.
pause
exit /b 1
