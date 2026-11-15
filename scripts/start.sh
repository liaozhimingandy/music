#!/usr/bin/env bash
# 同时启动后端（uvicorn）与前端（vite dev）；Ctrl+C 一起退出。
# 首次运行前先执行 scripts/install_mac.sh（或手工 uv sync --project backend）。
#
# 后端绑定地址由 backend/.env 的 HOST / PORT 决定（缺省 0.0.0.0:8788），
# 本脚本读出后按 uvicorn 官方写法传给 --host/--port。
# 注意：容器内固定监听 8000（backend/Dockerfile 的 CMD），与 .env 无关。
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND_PORT=${ACE_STEP_UI_PORT:-5175}

# 从 backend/.env 读一个键，仅用于回显提示；后端真实取值由 pydantic 决定。
env_value() {
  local key=$1 file="$ROOT_DIR/backend/.env" line=""
  [ -f "$file" ] || return 0
  line=$(grep -E "^[[:space:]]*${key}=" "$file" 2>/dev/null | tail -n 1) || line=""
  line=${line#*=}
  line=${line%$'\r'}
  line=${line%\"}; line=${line#\"}; line=${line%\'}; line=${line#\'}
  printf '%s' "$line"
}

BACKEND_HOST=$(env_value HOST)
[ -n "$BACKEND_HOST" ] || BACKEND_HOST=$(env_value MUSIC_STUDIO_HOST)
[ -n "$BACKEND_HOST" ] || BACKEND_HOST=$(env_value ACE_STEP_HOST)
BACKEND_HOST=${BACKEND_HOST:-0.0.0.0}

BACKEND_PORT=$(env_value PORT)
[ -n "$BACKEND_PORT" ] || BACKEND_PORT=$(env_value MUSIC_STUDIO_PORT)
[ -n "$BACKEND_PORT" ] || BACKEND_PORT=$(env_value ACE_STEP_PORT)
BACKEND_PORT=${BACKEND_PORT:-8788}

# 前端 dev server 的绑定地址与后端无关，独立变量。
FRONTEND_HOST=${FRONTEND_HOST:-0.0.0.0}

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
# 绑定地址取自 backend/.env 的 HOST / PORT（兼容 ACE_STEP_* 旧别名）。
uv run --project "$ROOT_DIR/backend" \
  uvicorn app.main:app --app-dir "$ROOT_DIR/backend" --host "$BACKEND_HOST" --port "$BACKEND_PORT" &
BACKEND_PID=$!

echo "Backend running (PID $BACKEND_PID) on $BACKEND_HOST:$BACKEND_PORT"

cd "$ROOT_DIR/frontend"
npm run dev -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" &
FRONTEND_PID=$!

echo "Frontend running (PID $FRONTEND_PID) on $FRONTEND_HOST:$FRONTEND_PORT"

wait
