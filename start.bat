@echo off
rem AI Study Companion one-click launcher (backend 8000 + frontend 5173)
rem Uses %~dp0 so no hardcoded paths; run from this folder.
setlocal enabledelayedexpansion
set ROOT=%~dp0
set PY=C:\Users\%USERNAME%\.workbuddy\binaries\python\envs\default\Scripts\python.exe
set PORT=8000

if not exist "%PY%" (
  echo [ERROR] Managed python venv not found: %PY%
  pause
  exit /b 1
)

rem npm 路径不能写死版本号：托管 node 版本目录名会随重装变化（曾因写死 22.22.2-2
rem 而实际目录是 22.22.2-3，导致脚本静默找不到 npm）。这里按 22.* 动态探测。
set NPM=
for /d %%D in ("C:\Users\%USERNAME%\.workbuddy\binaries\node\versions\22.*") do (
  if exist "%%D\npm.cmd" set NPM=%%D\npm.cmd
)
if not defined NPM (
  echo [ERROR] npm.cmd not found under .workbuddy\binaries\node\versions\22.*
  pause
  exit /b 1
)

rem 8000 被客户端版 exe 占用时，可用 ASC_PORT 换端口跑源码版（前端会同端口代理）
if defined ASC_PORT set PORT=%ASC_PORT%

if not "%PORT%"=="8000" (
  echo [INFO] Using port %PORT% ; frontend proxy target set accordingly.
  set ASC_API_TARGET=http://127.0.0.1:%PORT%
)

start "asc-backend" "%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT% --app-dir "%ROOT%backend"
start "asc-frontend" cmd /c "set ASC_API_TARGET=http://127.0.0.1:%PORT% && "%NPM%" run dev --prefix "%ROOT%frontend""

echo.
echo Backend : http://127.0.0.1:%PORT%/api/health
echo Frontend: http://localhost:5173
echo.
echo Close the two popup windows to stop the services.
pause
