@echo off
setlocal

rem 依赖管理用 uv（https://docs.astral.sh/uv/）。没有的话先装：
rem   powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
set "ROOT_DIR=%~dp0.."
for %%I in ("%ROOT_DIR%") do set "ROOT_DIR=%%~fI"

where uv >nul 2>nul
if errorlevel 1 (
  echo uv is not available in PATH.
  echo Install uv first: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
  exit /b 1
)

if not exist "%ROOT_DIR%\backend\.venv\Scripts\python.exe" (
  echo backend\.venv missing. Run: uv sync --project backend
  echo Or run scripts\install_windows.ps1 first.
  exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
  echo npm is not available in PATH.
  echo Install Node.js and retry.
  exit /b 1
)

rem 后端绑定地址由 backend\.env 的 HOST / PORT 决定（缺省 0.0.0.0:8788），
rem 由 backend\run.py 读 settings 后启动 uvicorn。这里**不**传 --host/--port，
rem 否则命令行参数会盖掉 .env 的配置（这正是「改了 .env 没效果」的根因）。
rem 下面的读取仅用于回显提示；实际生效地址以后端启动日志为准。
if not defined ACE_STEP_UI_PORT set "ACE_STEP_UI_PORT=5175"
if not defined FRONTEND_HOST set "FRONTEND_HOST=0.0.0.0"

set "BACKEND_HOST=0.0.0.0"
set "BACKEND_PORT=8788"
for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b /c:"HOST=" "%ROOT_DIR%\backend\.env" 2^>nul`) do set "BACKEND_HOST=%%B"
for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b /c:"PORT=" "%ROOT_DIR%\backend\.env" 2^>nul`) do set "BACKEND_PORT=%%B"

echo Starting Music Studio backend on %BACKEND_HOST%:%BACKEND_PORT%
start "Music Studio Backend" cmd /k "cd /d "%ROOT_DIR%" && uv run --project backend python backend\run.py"

echo Starting Music Studio frontend on %FRONTEND_HOST%:%ACE_STEP_UI_PORT%
start "Music Studio Frontend" cmd /k "cd /d "%ROOT_DIR%\frontend" && npm run dev -- --host %FRONTEND_HOST% --port %ACE_STEP_UI_PORT%"

echo.
echo Backend:  http://localhost:%BACKEND_PORT%/v1/health
echo API docs: http://localhost:%BACKEND_PORT%/docs
echo Frontend: http://localhost:%ACE_STEP_UI_PORT%
echo 离线演示：在 backend\.env 中设置 MUSIC_STUDIO_MOCK=true

exit /b 0
