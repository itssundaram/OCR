@echo off
setlocal enabledelayedexpansion
title DOCINT — Stop All Processes

echo.
echo ============================================================
echo   DOCINT — Stopping processes and cleaning up
echo ============================================================
echo.

set FOUND=0

echo [1/3] Terminating port-bound processes (API and Frontend)...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do (
    echo   Killing PID %%p (port 8000)
    taskkill /PID %%p /F >nul 2>&1
    set FOUND=1
)

for /f "tokens=5" %%p in ('netstat -ano ^| findstr :5173 ^| findstr LISTENING') do (
    echo   Killing PID %%p (port 5173)
    taskkill /PID %%p /F >nul 2>&1
    set FOUND=1
)

echo.
echo [2/3] Terminating Redis RQ Worker process...
wmic process where "commandline like '%%run_worker.py%%'" call terminate >nul 2>&1

echo.
echo [3/3] Closing orphaned command windows...
taskkill /FI "WINDOWTITLE eq DOCINT-API*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq DOCINT-WORKER*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq DOCINT-FRONTEND*" /F >nul 2>&1

if "%FOUND%"=="0" (
    echo.
    echo   Nothing was running on 8000 or 5173.
) else (
    echo.
    echo   Done. Verifying...
    netstat -ano | findstr :8000
    netstat -ano | findstr :5173
    echo   (if nothing printed just above, the ports are clear)
)

echo.

if /i not "%~1"=="--silent" (
    pause
)
