#!/usr/bin/env bash
# 同时启动后端（uv run uvicorn）与前端（vite dev）；Ctrl+C 一起退出。
# 首次运行前先执行 scripts/install_mac.sh（或手工 uv sync --project backend）。
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_PORT=${ACE_STEP_PORT:-8788}
FRONTEND_PORT=${ACE_STEP_UI_PORT:-5175}
HOST=${ACE_STEP_HOST:-0.0.0.0}

if ! command -v uv >/dev/null 2>&1; then
  echo "未找到 uv，请先安装：curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

if [ ! -d "$ROOT_DIR/backend/.venv" ]; then
  echo "backend/.venv 不存在，先跑 uv sync --project backend（或 scripts/install_mac.sh）" >&2
  exit 1
fi

trap 'kill 0' SIGINT SIGTERM EXIT

# uv run 会复用 backend/.venv，不需要手动 activate / 设 PYTHONPATH：
# --app-dir 已经把 backend 加进 sys.path。
uv run --project "$ROOT_DIR/backend" \
  uvicorn app.main:app --app-dir "$ROOT_DIR/backend" --host "$HOST" --port "$BACKEND_PORT" &
BACKEND_PID=$!

echo "Backend running (PID $BACKEND_PID) on $HOST:$BACKEND_PORT"

cd "$ROOT_DIR/frontend"
npm run dev -- --host "$HOST" --port "$FRONTEND_PORT" &
FRONTEND_PID=$!

echo "Frontend running (PID $FRONTEND_PID) on $HOST:$FRONTEND_PORT"

wait
