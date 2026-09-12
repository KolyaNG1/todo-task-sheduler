@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PLANNER_FRONTEND_PORT=3000"

start "Planner server" /min python -m uvicorn planner.main:app --app-dir "%CD%\backend\src" --host 127.0.0.1 --port 8000
start "Planner interface" /min python -m http.server %PLANNER_FRONTEND_PORT% --directory "%CD%\frontend"

ping 127.0.0.1 -n 3 >nul
start "" "http://127.0.0.1:%PLANNER_FRONTEND_PORT%/"
