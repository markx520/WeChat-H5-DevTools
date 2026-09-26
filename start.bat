@echo off
rem ========================================================
rem WeChat-H5-DevTools One-Click Launcher for Windows
rem ========================================================

echo [*] Checking Python environment...
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    echo Please download and install Python 3.9 - 3.12 from https://www.python.org/
    pause
    exit /b 1
)

python main.py
if %errorlevel% neq 0 (
    pause
)
