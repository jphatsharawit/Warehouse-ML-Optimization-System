@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Warehouse Optimization System
cd /d "%~dp0"

echo ========================================================================
echo  WAREHOUSE OPTIMIZATION SYSTEM - LAUNCHER (AUTO-FIX)
echo ========================================================================
echo.

rem -----------------------------------------------------
rem  1. AUTO-CLEANUP (The Fix)
rem  ฆ่า Process ที่แย่ง Port 8000 อยู่ เพื่อให้ระบบรันได้แน่นอน
rem -----------------------------------------------------
echo [INFO] Cleaning up previous sessions (Port 8000)...
for /f "tokens=5" %%a in ('netstat -aon ^| find ":8000" ^| find "LISTENING"') do (
    taskkill /f /pid %%a >nul 2>&1
)
timeout /t 1 >nul

rem -----------------------------------------------------
rem  2. SYSTEM CHECK
rem -----------------------------------------------------
echo [INFO] Checking system prerequisites...

where node >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Node.js is not installed. Please install Node.js.
    pause
    exit /b
)

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed. Please install Python.
    pause
    exit /b
)

rem -----------------------------------------------------
rem  3. FRONTEND SETUP
rem -----------------------------------------------------
echo.
echo [INFO] Initializing Frontend...

if not exist "warehouse-ui" (
    echo [ERROR] Directory 'warehouse-ui' not found.
    pause
    exit /b
)

cd "warehouse-ui"
if not exist "node_modules" call npm install >nul 2>&1
if not exist "dist" call npm run build >nul 2>&1
cd ..

rem -----------------------------------------------------
rem  4. BACKEND SETUP
rem -----------------------------------------------------
echo.
echo [INFO] Initializing Backend...

if not exist "project" (
    echo [ERROR] Directory 'project' not found.
    pause
    exit /b
)

cd "project"
if not exist ".venv" python -m venv .venv

echo        - Verifying dependencies...
".venv\Scripts\pip" install -q fastapi uvicorn numpy pandas scikit-learn pydantic python-dotenv python-multipart aiofiles watchfiles

if not exist ".env" echo DATA_CSV=data.csv > .env

rem -----------------------------------------------------
rem  5. START SERVER (Force Port 8000)
rem -----------------------------------------------------
echo.
echo [INFO] Starting API Server on Port 8000...

rem ใช้ start แบบไม่รอ เพื่อให้หน้าต่างนี้ทำงานต่อได้
start "Warehouse API Server" cmd /k ".venv\Scripts\python.exe -m uvicorn app:app --reload --host 127.0.0.1 --port 8000"

cd ..

rem -----------------------------------------------------
rem  6. LAUNCH UI
rem -----------------------------------------------------
echo [INFO] Launching User Interface...
timeout /t 4 >nul
start "" "http://127.0.0.1:8000/ui/"

echo.
echo ========================================================================
echo  [SUCCESS] System launched.
echo  If the server is still offline, please refresh the web page (F5).
echo ========================================================================
pause