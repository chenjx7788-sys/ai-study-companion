@echo off
rem ============================================================
rem  AI Study Companion - one-click start/stop (backend 8000 + frontend 5173)
rem  Double-click to run:
rem     - services not running  -> start backend + frontend
rem     - services running      -> stop (only kills THIS project's processes)
rem
rem  Usage:
rem     dev.bat          auto: start or stop
rem     dev.bat start    force start (stop-then-start if already running)
rem     dev.bat stop     force stop
rem ============================================================
setlocal enabledelayedexpansion
set ROOT=%~dp0

rem ---- locate managed python venv ----
set PY=C:\Users\%USERNAME%\.workbuddy\binaries\python\envs\default\Scripts\python.exe
if not exist "%PY%" (
  echo [ERROR] managed python not found: %PY%
  pause
  exit /b 1
)

rem ---- locate managed node/npm (22.* dynamic match) ----
set NPM=
for /d %%D in ("C:\Users\%USERNAME%\.workbuddy\binaries\node\versions\22.*") do (
  if exist "%%D\npm.cmd" set NPM=%%D\npm.cmd
)
if not defined NPM (
  echo [ERROR] npm.cmd not found under .workbuddy\binaries\node\versions\22.*
  pause
  exit /b 1
)

set PORT=8000
if defined ASC_PORT set PORT=%ASC_PORT%

rem ---- detect listening ports ----
set B_RUN=0
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>&1
if !errorlevel!==0 set B_RUN=1

set F_RUN=0
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>&1
if !errorlevel!==0 set F_RUN=1

set ACTION=%~1

rem ---- decide action: no arg -> stop if anything running, else start ----
if "%ACTION%"=="" (
  if "!B_RUN!"=="1" set ACTION=stop
  if "!F_RUN!"=="1" set ACTION=stop
)
if "%ACTION%"=="" set ACTION=start

if /I "%ACTION%"=="stop" goto :do_stop
if /I "%ACTION%"=="start" goto :do_start
echo [ERROR] unknown arg: %ACTION%  (use start / stop / none)
pause
exit /b 1

:do_stop
  echo [stop] stopping this project's services ...
  call :kill_backend
  call :kill_frontend
  echo.
  echo Stopped. Run again (or "dev.bat start") to restart.
  pause
  exit /b 0

:do_start
  echo [start] starting backend :%PORT% + frontend :5173 ...
  if "!B_RUN!"=="1" call :kill_backend
  if "!F_RUN!"=="1" call :kill_frontend
  start "asc-backend" "%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT% --app-dir "%ROOT%backend"
  start "asc-frontend" cmd /c "set ASC_API_TARGET=http://127.0.0.1:%PORT% && "%NPM%" run dev --prefix "%ROOT%frontend""
  echo.
  echo Backend : http://127.0.0.1:%PORT%/api/health
  echo Frontend: http://localhost:5173
  echo.
  echo Starting... backend ready in ~6-10s. Close the two popup windows to stop.
  pause
  exit /b 0

rem ---- kill only this project's backend (uvicorn) / frontend (vite) ----
:kill_backend
  powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | Where-Object { $_.CommandLine -match 'uvicorn app\.main:app' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
  exit /b 0

:kill_frontend
  powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name = 'node.exe'\" | Where-Object { $_.CommandLine -match 'vite' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
  exit /b 0
