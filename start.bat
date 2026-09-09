@echo off
rem AI Study Companion one-click launcher (backend 8000 + frontend 5173)
rem Uses %~dp0 so no hardcoded paths; run from this folder.
setlocal
set ROOT=%~dp0
set PY=C:\Users\%USERNAME%\.workbuddy\binaries\python\envs\default\Scripts\python.exe
set NPM=C:\Users\%USERNAME%\.workbuddy\binaries\node\versions\22.22.2-2\npm.cmd

if not exist "%PY%" (
  echo [ERROR] Managed python venv not found: %PY%
  pause
  exit /b 1
)

start "asc-backend" "%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir "%ROOT%backend"
start "asc-frontend" "%NPM%" run dev --prefix "%ROOT%frontend"

echo.
echo Backend : http://127.0.0.1:8000/api/health
echo Frontend: http://localhost:5173
echo.
echo Close the two popup windows to stop the services.
pause
