@echo off
cd /d "%~dp0"

start "Планировщик — сервер" /min python -m uvicorn planner.main:app --app-dir "%CD%\backend\src" --host 127.0.0.1 --port 8000
start "Планировщик — интерфейс" /min python -m http.server 5173 --directory "%CD%\frontend"

timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:5173/"
