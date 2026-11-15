# Music Studio 一次性安装（Windows PowerShell）
# 依赖管理用 uv（https://docs.astral.sh/uv/），没有的话先装：
#   powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
# 云端推理后端不需要本地模型权重 / GPU / torch。
$ErrorActionPreference = "Stop"

$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)
$envExample = Join-Path $ROOT "backend/app/.env.example"
$envFile = Join-Path $ROOT "backend/app/.env"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  throw "未找到 uv，请先安装：powershell -c ""irm https://astral.sh/uv/install.ps1 | iex"""
}

Write-Host "==> 同步后端依赖（uv sync，虚拟环境在 backend/.venv）"
Push-Location $ROOT
uv sync --project backend
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "后端依赖安装失败" }
Pop-Location

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
