# 安装与运行指南

Music Studio 由「Vue 3 前端 + FastAPI 后端」组成。后端调用云端 LLM 与 ModelScope ACE-Step 推理，**不需要下载本地模型权重、不需要 GPU**。

> 只是想把它跑在服务器上？直接用 Docker：见 [Docker 部署指南](./deployment.md)，服务器不需要装 Python / Node。

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

后端没启动时页面状态栏会提示「后端未连接」，界面功能不可用；启动后端后刷新即恢复。

## 安装后端（Windows）

在仓库根目录执行：

```powershell
uv sync --project backend
Copy-Item backend/.env.example backend/.env
notepad backend/.env     # 填入 OPENAIM_API_KEY 与 MUSIC_API_TOKEN
```

依赖由 [uv](https://docs.astral.sh/uv/) 管理；没装 uv 时先按其[官方说明](https://docs.astral.sh/uv/getting-started/installation/)安装（Windows 一行命令即可，装完重开终端）。

## 安装后端（macOS / Linux）

```bash
uv sync --project backend
cp backend/.env.example backend/.env
$EDITOR backend/.env     # 填入密钥
```

没装 uv 时先执行官方安装脚本（见 https://docs.astral.sh/uv/getting-started/installation/ ）。

> `.env` 已被 `.gitignore` 忽略，不要提交密钥。未配置 LLM Key 时，扩写/写词/起标题会返回本地兜底文本；未配置 `MUSIC_API_TOKEN` 时音频生成会自动切到离线 mock。
>
> `uv sync --project backend` 会一并装好 `aiosqlite`（异步 SQLite 驱动）、`httpx`、`Jinja2` 等 8 个轻量依赖，版本由 `backend/uv.lock` 锁定，不需要 torch / CUDA / 本地模型权重。后端全链路异步，**请用单进程 uvicorn 启动**（队列 worker 在进程内去重）。生成任务状态存 SQLite，重启后自动恢复，详见 [接口清单](./api.md) 第二章。

## 启动

```bash
# 后端（仓库根目录；Windows / macOS / Linux 命令一致）
uv run --project backend uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8788

# 前端（另一个终端）
cd frontend && npm run dev
```

Windows 可用 `scripts/start.bat`、macOS/Linux 用 `scripts/start.sh` 一次启动两端。

打开 http://localhost:5175 。后端交互文档在 http://localhost:8788/docs ，健康检查在 `/v1/health`。

## 离线 mock 模式

不消耗任何 API 额度即可跑通完整五阶段流程（本地合成 8 秒 WAV，前端可播放）：

```bash
MUSIC_STUDIO_MOCK=true uv run --project backend uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8788
```

也可把 `MUSIC_STUDIO_MOCK=true` 写进 `backend/.env`。

## 验证

```bash
uv run --project backend python backend/tests/smoke_test.py
```

该测试使用临时数据库 + mock 模式，89 项断言（启用 DELETE 路由时为 94 项）覆盖 /v1 版本前缀与 CORS、建表与旧库补列、UUIDv7 单调性、LLM 三任务、全异步五阶段、**阶段遥测**（模型 / token / 耗时落库）、幂等去重（含 `Idempotency-Key` / `force`）、413 / 422 / 429、每日生成配额与空描述防护、MUSIC_MODEL 配置兜底、音频下载、级联删除，以及**进程重启后的队列恢复**（含执行中任务被中断后的断点续跑、恢复次数超限与 `failed` 终态）。

## 数据与重置

- 数据库：`data/music_studio.db`
- 阶段产物与音频：`data/outputs/<作品ID>/`
- 运行日志：`data/logs/music-studio-YYYY-MM-DD.log`（按天一个文件，默认保留 7 天）
- 重置全部数据：停止服务后删除 `data/` 目录即可，下次启动自动重建。

日志级别由 `DEBUG` 控制：`false`（默认）记 INFO——启停、每个阶段用了哪个模型 /
多少 token / 耗时、创建与失败原因；`true` 额外记 LLM 请求参数、模型返回内容前
300 字等细节（异常堆栈会带局部变量，生产别开）。保留天数用
`MUSIC_STUDIO_LOG_RETENTION_DAYS` 调整（默认 7）。
- **重启无需手工干预**：未完成的 `queued` / `processing` 作品在启动时会重新入队；想让某条任务不再恢复，把它的 `status` 改成 `failed`，或直接删除该作品。配置项 `MUSIC_STUDIO_QUEUE_POLL_SECONDS`（默认 3）与 `MUSIC_STUDIO_MAX_RECOVERY_ATTEMPTS`（默认 5）见 `backend/.env.example`。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 前端显示「后端未连接」 | 后端 8788 未启动；或 Vite 的 `/v1` 代理目标不对（见 `frontend/vite.config.ts`，可用 `VITE_API_PROXY_TARGET` 改端口） |
| 浏览器报 `No 'Access-Control-Allow-Origin'` | 直连后端是不同源：先 `curl -i -H "Origin: http://127.0.0.1:5177" http://localhost:8000/v1/health` 看 CORS 头。默认放行所有回环来源；若实际是纯文本 500，说明后端异常被误报成跨域（查后端日志）。也可把 `VITE_APP_API_BASE_URL` 设回 `/v1` 走同源代理彻底避开 |
| 提交生成返回 429 | 三种可能：① 请求频率超限（`MUSIC_STUDIO_RATE_LIMIT_CREATE`）；② 活跃任务已满（`MUSIC_STUDIO_MAX_ACTIVE_JOBS`）；③ 今日额度用完（`MUSIC_STUDIO_DAILY_LIMIT`，默认 50 首/天）。`detail` 里写了具体原因，按 `Retry-After` 退避；阈值见 `backend/.env.example`（生产不建议关闭总开关，配额设 0 可关闭每日限制） |
| 重复点「开始生成」产生了两条 | 正常应命中幂等去重（响应带 `X-Deduplicated: true`）；确实要再生成一次可加 `?force=true` |
| 创建作品后一直「生成中」 | 查看后端日志（`data/logs/`）；`GET /v1/generations/{id}/stages` 可看卡在哪个阶段 |
| 换了 LLM 模型，数据库里 `song_stages.model` 没变 | 断点续跑的阶段会跳过执行并复用旧产物，日志里是「复用已落盘产物…跳过执行」。想用新模型重跑设 `MUSIC_STUDIO_RESUME=false`；`songs.metadata.llm_model` 记录本次任务整体用的模型 |
| 状态变「生成失败」 | 读 `error_message`；常见原因是密钥无效、网络超时、模型返回非 JSON |
| 想中断后继续 | 现在会自动继续：重启后端后队列 worker 扫描未完成作品重新入队；`data/outputs/<id>/` 下的阶段 JSON 跳过重跑，音乐任务复用 `music_task.json` 里的 `task_id` 不重复计费 |
| 重启后任务一直卡「生成中」 | 看后端日志是否有阶段异常；连续中断超过 `MUSIC_STUDIO_MAX_RECOVERY_ATTEMPTS` 会被标为 `failed` 并写明原因，可重新提交生成 |
| 端口冲突 | 改 `backend/.env` 的 `PORT`，或前端开发服务器加 `--port 5177` |
| Pages 线上页面无法生成 | 纯静态托管没有后端，需自建后端并把 `VITE_APP_API_BASE_URL` 指向后端地址 |
| Pages 打开是白屏 / 提示找不到 js | 资源 base 与部署路径不一致。绑定自定义域名（如 `music.openaim.cn`）时站点在根路径，`VITE_BASE_PATH` 必须是 `/`（工作流默认沿用 `.env.production`，不再强制注入）；只有项目页 `/<owner>.github.io>/<repo>/` 才需要在仓库变量里设 `PAGES_BASE_PATH=/<repo>/` |
| 启动日志提示「LLM_CA_CERT 指向的证书不存在」 | 证书路径按 `backend/app` → `backend` → 仓库根 → 进程 CWD 依次尝试；把 `root.crt` 放到 `backend/app/` 下、`.env` 里写 `root.crt`（或 `.\root.crt`）即可，反斜杠会自动转正斜杠 |
