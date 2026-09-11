@echo off
chcp 65001 >nul
cd /d "%~dp0"

start "Planner server" /min python -m uvicorn planner.main:app --app-dir "%CD%\backend\src" --host 127.0.0.1 --port 8000
start "Planner interface" /min python -m http.server 5173 --directory "%CD%\frontend"

timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:5173/"
