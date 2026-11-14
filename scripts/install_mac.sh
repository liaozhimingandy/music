#!/usr/bin/env bash
# Music Studio 一次性安装（macOS / Linux）
# 云端推理后端不需要本地模型权重 / GPU / torch。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY_ENV="$ROOT_DIR/backend/.venv"
NODE_DIR="$ROOT_DIR/frontend"
ENV_EXAMPLE="$ROOT_DIR/backend/app/.env.example"
ENV_FILE="$ROOT_DIR/backend/app/.env"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "未找到 python3，请安装 Python 3.10+" >&2
  exit 1
fi

echo "==> 创建 Python 虚拟环境"
if [[ ! -x "$PY_ENV/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$PY_ENV"
fi

echo "==> 安装后端依赖（FastAPI / httpx / Jinja2 / pydantic-settings）"
"$PY_ENV/bin/python" -m pip install --upgrade pip
"$PY_ENV/bin/python" -m pip install -e "$ROOT_DIR/backend"

echo "==> 安装前端依赖"
(cd "$NODE_DIR" && npm install)

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  echo "==> 已生成 backend/app/.env，请填入 OPENAIM_API_KEY 与 MUSIC_API_TOKEN"
else
  echo "==> 已存在 backend/app/.env，保持不动"
fi

echo ""
echo "安装完成。运行 scripts/start.sh 启动前后端。"
echo "只想离线预览：在 backend/app/.env 里设置 MUSIC_STUDIO_MOCK=true"
