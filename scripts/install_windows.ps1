# Music Studio 一次性安装（Windows PowerShell）
# 云端推理后端不需要本地模型权重 / GPU / torch。
$ErrorActionPreference = "Stop"

$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)
$pyEnv = Join-Path $ROOT "backend/.venv"
$envExample = Join-Path $ROOT "backend/app/.env.example"
$envFile = Join-Path $ROOT "backend/app/.env"

Write-Host "==> 创建 Python 虚拟环境"
if (-not (Test-Path (Join-Path $pyEnv "Scripts/python.exe"))) {
  python -m venv $pyEnv
  if ($LASTEXITCODE -ne 0) { throw "创建虚拟环境失败，请确认已安装 Python 3.10+ 并加入 PATH" }
}

Write-Host "==> 安装后端依赖（FastAPI / httpx / Jinja2 / pydantic-settings）"
& (Join-Path $pyEnv "Scripts/python.exe") -m pip install --upgrade pip
& (Join-Path $pyEnv "Scripts/python.exe") -m pip install -e (Join-Path $ROOT "backend")
if ($LASTEXITCODE -ne 0) { throw "后端依赖安装失败" }

Write-Host "==> 安装前端依赖"
Push-Location (Join-Path $ROOT "frontend")
npm install
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "前端依赖安装失败" }
Pop-Location

if (-not (Test-Path $envFile)) {
  Copy-Item $envExample $envFile
  Write-Host "==> 已生成 backend/app/.env，请填入 OPENAIM_API_KEY 与 MUSIC_API_TOKEN"
} else {
  Write-Host "==> 已存在 backend/app/.env，保持不动"
}

Write-Host ""
Write-Host "安装完成。运行 scripts/start.bat 启动前后端。"
Write-Host "只想离线预览：在 backend/app/.env 里设置 MUSIC_STUDIO_MOCK=true"
