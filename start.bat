@echo off
chcp 437 >nul
echo ====================================
echo   FFmpeg Media Compressor v1.0
echo ====================================
echo.

cd /d "%~dp0"

echo [1/3] Checking Python...
python --version >nul 2>&1
if not %errorlevel% == 0 (
    echo ERROR: Python not found. Please install Python 3.8+.
    pause
    exit /b 1
)
echo   Python OK

echo [2/3] Installing dependencies...
python -m pip install flask flask-cors requests --index-url https://pypi.org/simple/ -q 2>nul
python -c "import flask" >nul 2>&1
if not %errorlevel% == 0 (
    echo ERROR: Failed to install Flask. Trying alternative method...
    pip install flask flask-cors requests -q 2>nul
    python -c "import flask" >nul 2>&1
    if not %errorlevel% == 0 (
        echo WARNING: Flask installation failed. Run the following manually:
        echo   pip install flask flask-cors requests
        pause
        exit /b 1
    )
)
echo   Dependencies OK

echo [3/3] Starting server...
echo.
echo ====================================
echo   Open browser: http://localhost:8080
echo ====================================
python -m backend.main
pause
