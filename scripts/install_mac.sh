#!/usr/bin/env bash
# Music Studio 一次性安装（macOS / Linux）
# 依赖管理用 uv（https://docs.astral.sh/uv/），没装的话：
#   curl -LsSf https://astral.sh/uv/install.sh | sh
# 云端推理后端不需要本地模型权重 / GPU / torch。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
NODE_DIR="$ROOT_DIR/frontend"
ENV_EXAMPLE="$ROOT_DIR/backend/app/.env.example"
ENV_FILE="$ROOT_DIR/backend/app/.env"

if ! command -v uv >/dev/null 2>&1; then
  echo "未找到 uv，请先安装：curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

echo "==> 同步后端依赖（uv sync，虚拟环境在 backend/.venv）"
(cd "$ROOT_DIR" && uv sync --project backend)

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
