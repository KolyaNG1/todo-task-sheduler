@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal
set "PLANNER_FRONTEND_PORT=3000"
set "PLANNER_API_PORT=8100"

python "%CD%\scripts\stop_planner.py" %PLANNER_FRONTEND_PORT% %PLANNER_API_PORT%
if errorlevel 1 goto stop_error

start "Planner server" /min python -m uvicorn planner.main:app --app-dir "%CD%\backend\src" --host 127.0.0.1 --port %PLANNER_API_PORT%
start "Planner interface" /min python -m http.server %PLANNER_FRONTEND_PORT% --bind 127.0.0.1 --directory "%CD%\frontend"

python "%CD%\scripts\wait_for_planner.py" "http://127.0.0.1:%PLANNER_API_PORT%/api/v1/health" "http://127.0.0.1:%PLANNER_FRONTEND_PORT%/"
if not errorlevel 1 goto ready

start "Planner startup error" cmd /k "echo Planner could not start on ports %PLANNER_API_PORT% and %PLANNER_FRONTEND_PORT%. Check Python and port availability."
exit /b 1

:stop_error
start "Planner restart error" cmd /k "echo Old planner process could not be stopped. Close Planner server and Planner interface windows, then retry."
exit /b 1

:ready
start "" "http://127.0.0.1:%PLANNER_FRONTEND_PORT%/"
endlocal
