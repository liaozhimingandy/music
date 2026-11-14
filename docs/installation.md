# 安装与运行指南

Music Studio 由「Vue 3 前端 + FastAPI 后端」组成。后端调用云端 LLM 与 ModelScope ACE-Step 推理，**不需要下载本地模型权重、不需要 GPU**。

## 环境要求

| 组件 | 版本 | 用途 |
| --- | --- | --- |
| Node.js | 18+ | 前端构建 / 开发服务器 |
| Python | 3.10+ | FastAPI 后端 |
| 云端密钥 | — | LLM 的 `OPENAIM_API_KEY`、ModelScope 的 `MUSIC_API_TOKEN` |

## 只看界面（无需 Python / 无需密钥）

```bash
cd frontend
npm install
npm run dev       # http://localhost:5175
```

后端没启动时前端会自动进入**演示模式**，界面与生成流程都能预览。

## 安装后端（Windows）

在仓库根目录执行：

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -e backend
Copy-Item backend/app/.env.example backend/app/.env
notepad backend/app/.env     # 填入 OPENAIM_API_KEY 与 MUSIC_API_TOKEN
```

## 安装后端（macOS / Linux）

```bash
python3 -m venv backend/.venv
backend/.venv/bin/pip install -e backend
cp backend/app/.env.example backend/app/.env
$EDITOR backend/app/.env     # 填入密钥
```

> `.env` 已被 `.gitignore` 忽略，不要提交密钥。未配置 LLM Key 时，扩写/写词/起标题会返回本地兜底文本；未配置 `MUSIC_API_TOKEN` 时音频生成会自动切到离线 mock。

## 启动

```bash
# 后端（仓库根目录）
backend/.venv/Scripts/uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8788   # Windows
# backend/.venv/bin/uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8788     # macOS / Linux

# 前端（另一个终端）
cd frontend && npm run dev
```

Windows 可用 `scripts/start.bat`、macOS/Linux 用 `scripts/start.sh` 一次启动两端。

打开 http://localhost:5175 。后端交互文档在 http://localhost:8788/docs ，健康检查在 `/health`。

## 离线演示模式

不消耗任何 API 额度即可跑通完整五阶段流程（本地合成 8 秒 WAV，前端可播放）：

```bash
MUSIC_STUDIO_MOCK=true backend/.venv/Scripts/uvicorn app.main:app --app-dir backend --port 8788
```

也可把 `MUSIC_STUDIO_MOCK=true` 写进 `backend/app/.env`。

## 验证

```bash
backend/.venv/Scripts/python.exe backend/tests/smoke_test.py   # Windows
# backend/.venv/bin/python backend/tests/smoke_test.py         # macOS / Linux
```

该测试使用临时数据库 + mock 模式，覆盖建表、LLM 三任务、创建作品、五阶段推进、音频下载与级联删除。

## 数据与重置

- 数据库：`data/music_studio.db`
- 阶段产物与音频：`data/outputs/<作品ID>/`
- 重置全部数据：停止服务后删除 `data/` 目录即可，下次启动自动重建。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 前端显示「演示模式」 | 后端 8788 未启动；或 Vite 的 `/api` 代理目标不对（见 `frontend/vite.config.ts`） |
| 创建作品后一直「生成中」 | 查看后端日志；`GET /api/generations/{id}/stages` 可看卡在哪个阶段 |
| 状态变「生成失败」 | 读 `error_message`；常见原因是密钥无效、网络超时、模型返回非 JSON |
| 想中断后继续 | `data/outputs/<id>/` 下的阶段 JSON 会跳过重跑；音乐任务 `music_task.json` 保存了 `task_id` |
| 端口冲突 | 改 `ACE_STEP_PORT`，或前端开发服务器加 `--port 5177` |
| Pages 线上页面无法生成 | 纯静态托管没有后端，页面按设计进入演示模式 |
