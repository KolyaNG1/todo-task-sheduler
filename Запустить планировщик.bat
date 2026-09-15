@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal
set "PLANNER_FRONTEND_PORT=3000"
set "PLANNER_API_PORT=8100"

python "%CD%\scripts\wait_for_planner.py" "http://127.0.0.1:%PLANNER_API_PORT%/api/v1/health" "http://127.0.0.1:%PLANNER_FRONTEND_PORT%/"
if not errorlevel 1 goto ready

start "Planner server" /min python -m uvicorn planner.main:app --app-dir "%CD%\backend\src" --host 127.0.0.1 --port %PLANNER_API_PORT%
start "Planner interface" /min python -m http.server %PLANNER_FRONTEND_PORT% --directory "%CD%\frontend"

python "%CD%\scripts\wait_for_planner.py" "http://127.0.0.1:%PLANNER_API_PORT%/api/v1/health" "http://127.0.0.1:%PLANNER_FRONTEND_PORT%/"
if not errorlevel 1 goto ready

start "Ошибка запуска планировщика" cmd /k "echo Не удалось запустить API на порту %PLANNER_API_PORT% или интерфейс на порту %PLANNER_FRONTEND_PORT%.^& echo Проверьте, что Python установлен и порты не заняты."
exit /b 1

:ready
start "" "http://127.0.0.1:%PLANNER_FRONTEND_PORT%/"
endlocal
