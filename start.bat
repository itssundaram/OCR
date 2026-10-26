@echo off
setlocal enabledelayedexpansion
title DOCINT - Document Intelligence Platform
cd /d "%~dp0"

echo.
echo ============================================================
echo   DOCINT - Offline AI Document Intelligence Platform
echo ============================================================
echo.

echo [0/4] Stopping any previous DOCINT processes...
if exist "stop.bat" call stop.bat --silent
echo.

if not exist "backend\.env" (
    echo [ERROR] backend\.env not found.
    echo         Copy backend\.env.example to backend\.env and fill in your values.
    echo.
    pause & exit /b 1
)
if not exist "frontend\.env" (
    if exist "frontend\.env.example" copy "frontend\.env.example" "frontend\.env" > nul
)

echo [1/4] Python environment...
cd backend
if not exist "venv\Scripts\activate.bat" (
    echo        Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create the virtual environment. Is Python 3.10+ installed and on PATH?
        pause & exit /b 1
    )
)

call venv\Scripts\activate.bat

if not exist "venv\.install_success" (
    if exist "offline_package" (
        echo        Installing requirements from offline_package ^(no internet needed^)...
        python -m pip install --no-index --find-links=offline_package -r requirements.txt
    ) else (
        echo        offline_package folder not found - installing from the internet. See README to build it for air-gapped PCs.
        python -m pip install -r requirements.txt
    )
    if errorlevel 1 (
        echo [ERROR] Failed to install requirements.
        pause & exit /b 1
    )
    echo OK > venv\.install_success
)
echo [OK]   Virtual environment ready.
echo.

echo [2/4] Checking Oracle, Redis, Tesseract, Ollama and model weights...
python scripts\preflight.py
if errorlevel 1 (
    if not "%DOCINT_SKIP_PREFLIGHT%"=="1" (
        echo.
        echo [FATAL] Fix the FAIL lines above, then run start.bat again.
        echo         To start anyway:  set DOCINT_SKIP_PREFLIGHT=1
        pause & exit /b 1
    )
)
cd ..
echo.

echo [3/4] Starting the API (port 8000) and every worker...
start "DOCINT-API" cmd /k "cd /d %~dp0backend && call venv\Scripts\activate.bat && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
rem run_worker.py starts fast_ocr, learned_anchor, surya, tesseract and the general worker, and restarts any that crash
start "DOCINT-WORKER" cmd /k "cd /d %~dp0backend && call venv\Scripts\activate.bat && python run_worker.py"
timeout /t 4 /nobreak > nul
echo.

echo [4/4] Starting the frontend (port 5173)...
if not exist "frontend\node_modules" (
    echo        frontend\node_modules not found - running npm install ^(needs internet; offline: copy node_modules, see README^)...
    cd frontend
    call npm install
    cd ..
)
start "DOCINT-FRONTEND" cmd /k "cd /d %~dp0frontend && npm run dev -- --host"
timeout /t 3 /nobreak > nul
echo.
echo ============================================================
echo   DOCINT is starting. Check the windows above for logs.
echo.
echo   API:      http://localhost:8000
echo   API Docs: http://localhost:8000/docs
echo   Health:   http://localhost:8000/api/v1/health
echo   Frontend: http://localhost:5173
echo ============================================================
echo.
echo Press any key to open the application in your browser...
pause > nul
start "" "http://localhost:5173"
