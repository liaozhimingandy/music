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

if not defined ACE_STEP_PORT set "ACE_STEP_PORT=8788"
if not defined ACE_STEP_UI_PORT set "ACE_STEP_UI_PORT=5175"
if not defined ACE_STEP_HOST set "ACE_STEP_HOST=0.0.0.0"

echo Starting Music Studio backend on %ACE_STEP_HOST%:%ACE_STEP_PORT%
start "Music Studio Backend" cmd /k "cd /d "%ROOT_DIR%" && uv run --project backend uvicorn app.main:app --app-dir backend --host %ACE_STEP_HOST% --port %ACE_STEP_PORT%"

echo Starting Music Studio frontend on %ACE_STEP_HOST%:%ACE_STEP_UI_PORT%
start "Music Studio Frontend" cmd /k "cd /d "%ROOT_DIR%\frontend" && npm run dev -- --host %ACE_STEP_HOST% --port %ACE_STEP_UI_PORT%"

echo.
echo Backend:  http://localhost:%ACE_STEP_PORT%/v1/health
echo API docs: http://localhost:%ACE_STEP_PORT%/docs
echo Frontend: http://localhost:%ACE_STEP_UI_PORT%
echo 离线演示：在 backend\app\.env 中设置 MUSIC_STUDIO_MOCK=true

exit /b 0
