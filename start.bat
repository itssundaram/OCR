@echo off
setlocal enabledelayedexpansion
title DOCINT — Document Intelligence Platform
cd /d "%~dp0"

echo.
echo ============================================================
echo   DOCINT — Offline AI Document Intelligence Platform
echo ============================================================
echo.

echo [0/3] Cleaning up any previous DOCINT processes on ports 8000/5173...
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

echo [1/3] Setting up Python Virtual Environment...
cd backend
if not exist "venv\Scripts\activate.bat" (
    echo        Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment. Ensure Python is installed.
        pause & exit /b 1
    )
)

call venv\Scripts\activate.bat

if not exist "venv\.install_success" (
    echo        Installing requirements offline...
    python -m pip install --no-index --find-links=offline_package -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install requirements.
        pause & exit /b 1
    )
    echo OK > venv\.install_success
)
cd ..
echo [OK]   Virtual environment ready.
echo.


rem echo [3/7] Applying database migrations...
rem cd backend
rem python -m alembic upgrade head
rem if errorlevel 1 (
rem     echo [ERROR] Database migration failed.
rem     pause & exit /b 1
rem )
rem cd ..
rem echo [OK]   Migrations applied.
rem echo.


echo [2/3] Starting FastAPI backend (Port 8000)...
start "DOCINT-API" cmd /k "cd /d %~dp0backend && call venv\Scripts\activate.bat && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
echo        Starting RQ Worker...
start "DOCINT-WORKER" cmd /k "cd /d %~dp0backend && call venv\Scripts\activate.bat && python run_worker.py"
timeout /t 4 /nobreak > nul
echo.

echo [3/3] Starting Frontend (Port 5173)...
if not exist "frontend\node_modules" (
    echo        Installing frontend dependencies...
    cd frontend
    call npm install
    cd ..
)
start "DOCINT-FRONTEND" cmd /k "cd /d %~dp0frontend && npm run dev"
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
