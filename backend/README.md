# Music Studio Backend

FastAPI + SQLite3 的 AI 音乐创作后端。把一段创作灵感经过五个阶段变成可播放的歌曲：
`策划 → 歌词 → 润色 → 音乐制作方案 → 音频生成`。LLM 走 OpenAI 兼容端点，音频走 ModelScope ACE-Step 异步推理。

**全链路异步**：路由 / service / DAL 的 IO 方法都是 `async def`；数据库用 `aiosqlite`，HTTP 用 `httpx.AsyncClient`，文件 IO 用 `asyncio.to_thread`，后台生成任务用 `asyncio.Task` + `Semaphore`。等待云端生成时 event loop 仍可正常响应前端轮询。**任务状态以 SQLite 为持久化队列**，进程重启后自动恢复未完成作品，见下文「持久化队列」。

## 分层

```text
backend/app/
├── main.py            # FastAPI 入口（async lifespan 建表/恢复队列/清理、CORS、路由）
├── config.py          # pydantic-settings 配置（密钥只在 .env）
├── constants.py       # 阶段名、产物名、状态码、API 版本前缀 /v1
├── ids.py             # 全局 UUIDv7 生成器（时间有序、同毫秒单调递增）
├── deps.py            # FastAPI 依赖层：限流 / 请求体 / 客户端标识 / 路径校验
├── middleware.py      # 异常兜底：500 也带 CORS 头（在 CORS 内层）
├── routers/           # 接口层：api_v1 聚合路由 + async 端点 + Depends 注入服务
│   ├── __init__.py    # api_v1 = APIRouter(prefix=API_V1_PREFIX)，一次注册全部子路由
│   ├── history.py  generations.py  llm.py  meta.py（只写不带版本号的相对路径）
├── services/          # 业务层（不反向依赖 routers）
│   ├── runtime.py            # 单例装配 + 持久化队列 worker + asyncio.Task/Semaphore 调度
│   ├── song_service.py       # 作品 CRUD / 响应映射 / 幂等去重 / 并发闸门
│   ├── llm_service.py        # 扩写、写词、起标题
│   ├── workflow_service.py   # 五阶段编排 + 进度落库 + 断点续跑
│   ├── stage_steps.py        # 五个可复用阶段函数（async）
│   ├── prompt_manager.py     # Jinja2 渲染 prompts/（只读，async）
│   ├── llm_client.py         # httpx.AsyncClient OpenAI 兼容客户端 + 离线 mock
│   ├── music_provider.py     # ModelScope 提交/轮询/下载 + 离线 mock
│   ├── rate_limiter.py       # 进程内滑动窗口限流（async）
│   └── cover_theme.py        # 封面配色（纯函数）
├── dal/               # 数据访问层（aiosqlite 长连接 + 产物文件）
│   ├── database.py  song_dal.py  stage_dal.py  artifact_dal.py
├── models/            # 模型层：表结构 / 实体 / API Schema
│   ├── db_schema.py  entities.py  schemas.py
└── prompts/           # 四个阶段的 system.md / user.md（业务资产，代码不修改）
```

依赖方向严格单向：`routers → service → dal → model`。`deps.py` 属于接口层，负责校验与策略判断；`dal` 不含业务判断，`service` 不直接写 SQL。

## 接口版本前缀

所有接口（含健康检查）统一挂在 `/v1` 下：`/v1/health`、`/v1/history`、`/v1/llm`、`/v1/generations/*`。前缀常量 `API_V1_PREFIX` 只在 `constants.py` 定义，`routers/__init__.py` 用它构造 `api_v1` 聚合路由，`main.py` 只注册这一个路由；各子 router 的 `prefix` 一律不含版本号。前端 `VITE_APP_API_BASE_URL`、Vite 代理与 `audio_url` 模板同步使用 `/v1`。旧 `/api/*` 与无前缀 `/health` 已移除；未来升 v2 时新增一个聚合路由即可，v1 保持不变。

**跨域**：`CORSMiddleware` 默认放行所有本机回环来源（任意端口），生产用 `CORS_ORIGINS` 显式列域名。`middleware.py` 的 `UnhandledErrorMiddleware` 挂在 CORS 内层，把未捕获异常转成带 CORS 头的 JSON 500 —— 否则 Starlette 最外层的 `ServerErrorMiddleware` 生成纯文本 500，浏览器会把后端异常误报成跨域错误。详见 [docs/api.md](../docs/api.md) 3.4。

## 代码导览（给维护者）

所有源码都有模块级 docstring 与方法注释，按下面的顺序读最快：

1. `constants.py` + `config.py` —— 阶段名、产物名、状态枚举与全部运行开关（含防护配置）。
2. `ids.py` + `deps.py` —— UUIDv7 与接口防护；先看这两个能理解「一次请求进来先过哪几关」。
3. `models/` —— `db_schema.py`（两张表 DDL + 旧库补列映射）、`entities.py`（数据库行 ↔ 实体）、`schemas.py`（前后端契约与字段长度约束）。
4. `dal/` —— 每个方法只做一次 SQL / 文件读写；`database.py` 统一管长连接、事务锁与 WAL。
5. `services/stage_steps.py` —— 五个 async 阶段函数，是「一首歌怎么被写出来」的核心。
6. `services/workflow_service.py` —— 串起五阶段、写进度、断点续跑。
7. `services/song_service.py` —— 前端字段的唯一映射点（`musical_key → key`）、幂等去重与活跃任务闸门。
8. `services/runtime.py` + `routers/` + `main.py` —— 依赖注入组装点、持久化队列、对外接口与应用生命周期。

常见改动的落点：

| 要改什么 | 依次改哪里 |
| --- | --- |
| 新增数据库字段 | `models/db_schema.py`（DDL + `SONG_TABLE_MIGRATIONS` + `SONG_COLUMNS`）→ `entities.py` → `schemas.py` → `song_service.to_response()` |
| 调整防护阈值 | `config.py` 默认值 + `.env.example` + `docs/api.md` 配置表 |
| 调整提示词变量 | `stage_steps.py` 里的 `get_stage(...)` 参数 + `prompts/<阶段>/user.md`（模板由业务侧维护） |
| 新增生成阶段 | `constants.py` 注册阶段名与产物名 → `stage_steps.py` 加阶段函数 → `workflow_service.run()` 串入流程 |
| 新增接口 | `models/schemas.py` 定义模型 → `routers/` 加 `async def` + `Depends`（相对路径，不写 /v1）→ `deps.py` 选防护策略 → `docs/api.md` 同步 |
| 升级 API 版本 | `constants.py` 加前缀 → `routers/__init__.py` 新建聚合路由 → `main.py` 注册 → 前端 `API_BASE_URL` / Vite 代理同步 |
| 调整轮询 / 播放行为 | 前端 `pages/StudioPage.vue`、`services/api.ts`、`common/config.ts` |

**红线**：

- `backend/app/prompts/` 下的 8 个 md 是业务资产，代码只读取、不修改；改模板变量前先与提示词负责人确认。
- 密钥只放被忽略的 `.env`，不要写回代码或提交。
- 新增 async 方法时记得全链路 `await`；`dal.connection()` 是受锁保护的事务，**不要在它内部再嵌套调用** `connection()`（会死锁）。

## 异步与并发

| 关注点 | 实现 |
| --- | --- |
| 后台生成任务 | `runtime.submit_job()` 用 `asyncio.create_task`，`_tasks` 持强引用、`_running` 按 song_id 去重，Semaphore 限并发 |
| 队列持久化 | `songs` 表的 `queued/processing` 行即待办任务；`start_queue_worker()` 启动立即恢复 + 周期巡检，`SongDAL.claim_recovery()` 原子认领 |
| 应用关闭 | `runtime.shutdown()` 停巡检 → cancel 任务（**不改 DB 状态**，留待重启续跑）→ 释放 httpx 连接池 → `close_connection()` 关库 |
| 数据库 | 单条 `aiosqlite` 长连接 + `asyncio.Lock` 事务锁；WAL 只在 `init_schema()` 设一次 |
| 文件 IO | `ArtifactStore` 的读写都经 `asyncio.to_thread`，音频写盘不阻塞 event loop |
| 网络 IO | `httpx.AsyncClient`；轮询等待用 `asyncio.sleep`，下载用 `aiter_bytes()` |

> 历史坑：早期「每操作一连接 + 每次重设 WAL」在 Windows 并发读写时会抛
> `attempt to write a readonly database`（稳定复现在第三阶段）。改为长连接后消失，
> 详见 `dal/database.py` 模块说明，切勿改回。

## 持久化队列（重启不丢任务）

`songs.status IN ('queued','processing')` 就是队列，`asyncio.Task` 只是执行载体：

1. `POST /v1/generations` 先落库，再 `submit_job()` 交给后台任务；
2. 应用启动 `init_schema()` 后调用 `start_queue_worker()`，**立即**扫描未完成记录重新入队，之后每 `MUSIC_STUDIO_QUEUE_POLL_SECONDS`（默认 3 秒）巡检一次；
3. 关闭时只 cancel 内存任务，**不写 failed**，下次启动继续。阶段产物 JSON 已存在就跳过重跑，音乐阶段复用 `music_task.json` 的远端 `task_id`，不重复计费；
4. 不会继续的情况：接口限制（413 / 422 / 429 / 幂等，请求未落库）、真实异常（`WorkflowService` 写 `failed` 终态）、同一任务连续中断超过 `MUSIC_STUDIO_MAX_RECOVERY_ATTEMPTS`（默认 5，任务成功后清零）。

`SongDAL.claim_recovery()` 用带状态与次数条件的 `UPDATE ... recovery_attempts + 1` 原子认领，配合 `runtime._running` 防止重复入队。当前实现面向**单进程 uvicorn**；多 worker 需额外引入行级租约或外部队列。

## 幂等与接口防护

- **重复提交**：`SongService.create` 对请求内容算指纹（或取 `Idempotency-Key`），命中活跃任务 / 去重窗口内的已有作品时直接返回该作品（200 + `X-Deduplicated: true`），不重复计费。「查重 + 插入」用 `asyncio.Lock` 串行化；`?force=true` 可强制新建。
- **活跃任务闸门**放在去重之后：幂等重试不会因「任务已满」被误拦，只有真新建才检查全局 / 单客户端上限。
- **每日配额**：`MUSIC_STUDIO_DAILY_LIMIT`（默认 50 首/天，0 不限）限制「一天能新建几首」，同样在去重之后检查；计数口径是当天创建的 songs 行数（失败也算），日界按 `MUSIC_STUDIO_DAILY_RESET_UTC_OFFSET`（默认 8 = 北京时间）划分，实现见 `services/quota.py`。用完时 429 + `Retry-After`（到明日重置）+ 中文说明，未用完时响应带 `X-Daily-Remaining`。
- **限流**：`deps.py` 的 `enforce_read_policy` / `enforce_llm_policy` / `enforce_generation_policy` 按客户端 IP 哈希做滑动窗口计数，返回 429 + `Retry-After`；`reject_oversized_body` 返回 413；Pydantic 的 `extra="forbid"` 与字段长度约束返回 422。
- 完整规则与默认值见 [docs/api.md](../docs/api.md) 第三章。

## 安装与启动

依赖用 [uv](https://docs.astral.sh/uv/) 管理（`pyproject.toml` + `uv.lock`），不再需要 pip / requirements.txt：

```bash
# 1. 依赖（Python 3.10+；uv 安装方式见 https://docs.astral.sh/uv/getting-started/installation/）
uv sync --project backend                        # 建 backend/.venv，按 uv.lock 安装

# 2. 配置（模板在 backend/.env.example，复制到 backend/.env 填入 LLM / ModelScope 密钥）
cp backend/.env.example backend/.env

# 3. 启动（在仓库根目录执行；绑定地址读 backend/.env 的 HOST/PORT，默认 0.0.0.0:8788）
uv run --project backend uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8788
```

运行时依赖仅 8 个：fastapi / uvicorn / pydantic / pydantic-settings / python-dotenv / httpx / Jinja2 / aiosqlite，无需 torch 或本地模型权重。加依赖用 `uv add --project backend <包名>`，它会同时更新 `uv.lock`（锁文件必须一起提交，镜像构建用 `uv sync --frozen` 保证一致）。

交互式文档：`http://localhost:8788/docs`（文档端点不带版本号）；健康检查：`/v1/health`。
Windows 也可用 `scripts/start.bat` 一次启动前后端。

### 用 Docker 跑

```bash
# 只起后端，数据落到命名卷
docker build -t music-studio-backend ./backend
docker run -d --name music-backend -p 8000:8000 -v music-data:/app/data \
  -e MUSIC_API_TOKEN=xxx -e LLM_API_KEY=xxx music-studio-backend

# 只跑后端（前端走 GitHub Pages / Cloudflare 静态托管，直接调 /v1，需在 CORS_ORIGINS 放行）
cd .. && docker compose up -d --build
```

镜像要点：`python:3.12-slim`、以 uid `10001` 的非 root 用户运行、数据目录固定 `/app/data`、
健康检查打 `/v1/health`、**只能单进程**（队列 worker 在进程内去重，`--workers` 会重复消费任务）。
完整部署流程见 [Docker 部署指南](../docs/deployment.md)。

## 离线演示

不想消耗 API 额度时设置（或写在 `.env`）：

```bash
MUSIC_STUDIO_MOCK=true
```

此时 LLM 返回满足阶段校验的桩 JSON，音乐生成本地合成 8 秒 WAV，全流程约 1 秒跑完，前端播放/进度条完全可用。

## 日志与调试

日志由 `app/logging_setup.py` 统一用 **loguru** 收口（存量代码里的标准库
`logging.getLogger(__name__)` 会被拦截转发进来，无需改写）。

| 开关 | 默认 | 说明 |
| --- | --- | --- |
| `DEBUG` | `false` | `true` → DEBUG 级别；`false` → INFO 级别。同时决定异常堆栈是否带局部变量（生产别开） |
| `MUSIC_STUDIO_LOG_FILE` | `true` | 关掉后只输出到控制台 |
| `MUSIC_STUDIO_LOG_RETENTION_DAYS` | `7` | 日志保留天数，更早的由 loguru 自动删除 |
| `MUSIC_STUDIO_LOG_DIR` | `<data_dir>/logs` | 日志目录 |

文件按天轮转：`data/logs/music-studio-2026-11-15.log`（UTF-8，零点切分）。

**INFO 级别就能看到**（排查常规问题够用）：

```text
INFO | app.logging_setup:log_startup_summary:185 - 配置生效：LLM 模型=aim-1 ｜ 音乐模型=ACE-Step/acestep-v15-xl-sft ｜ DEBUG=False ｜ 断点续跑=True ｜ 每日上限=50
INFO | app.services.song_service:create:358 - 创建作品：01a1dcbe…（模式=simple，时长=-，配额 1/50，指纹 89acfcf4…）
INFO | app.services.workflow_service:run:161 - 开始 MusicFlow 任务：01a1dcbe…（输入 11 字，LLM=aim-1，断点续跑=True）
INFO | app.services.workflow_service:_execute:279 - 开始执行阶段：planning
INFO | app.services.workflow_service:_execute:296 - 阶段完成：planning ｜ 模型=aim-1 ｜ tokens=输入 2515 / 输出 9816 / 合计 12331 ｜ 耗时=99.7s
```

**DEBUG 级别额外记录**：LLM 请求参数（模型 / 端点 / 提示词长度）、模型返回
内容前 300 字、网关实际返回的模型与请求模型不一致的告警、用户输入摘要、
aiosqlite 清理产物等细节。

## 排障

- **换了 LLM 模型但数据库里 `song_stages.model` 没变 / 是空的**：先翻日志找
  `复用已落盘产物 …跳过执行（模型沿用上次记录的 X）`。断点续跑的设计是
  「阶段产物 JSON 存在就跳过」，重启后接着跑的任务不会重新调用模型，所以模型
  仍是上次那次的。想用新模型重跑就设 `MUSIC_STUDIO_RESUME=false`（产物会被覆盖，
  LLM 与音乐额度都会重新消耗）。另外 `songs.metadata.llm_model` 记录的是本次任务
  整体使用的模型，可用来判断某首歌到底是哪个模型产出的。
- **`LLM_CA_CERT` 提示找不到证书**：相对路径不再按启动目录解析，而是依次尝试
  `backend/app` → `backend` → 仓库根 → 进程 CWD，所以把 `root.crt` 放在
  `backend/app/` 下、`.env` 里写 `root.crt` 或 `.\root.crt` 都能命中（反斜杠会
  自动转成正斜杠，Linux 同样有效）。配置了却解析不到时，启动日志会打印
  `LLM 请求启用自定义 CA 证书`（成功）或 `指向的证书不存在（已尝试：…）`（失败）。
- **前端提示跨域**：先看响应状态码。500 也会被浏览器报成 CORS，用
  `curl -i http://localhost:8788/v1/history` 确认后端本身是否正常。

## 测试

```bash
uv run --project backend python backend/tests/smoke_test.py
```

冒烟测试用临时数据库 + mock 模式，91 项断言（启用 DELETE 路由时为 96 项）覆盖：/v1 版本前缀与 CORS、旧路径下线、建表与旧库补列、UUIDv7 单调性、三个 LLM 任务、全异步五阶段推进、阶段遥测（每阶段模型 / token / 耗时落库）、幂等去重（含 `Idempotency-Key` / `force`）、413 / 422 / 429、每日生成配额与空描述防护、MUSIC_MODEL 配置兜底、并发闸门顺序、500 响应的 CORS 头、音频下载、级联删除、前端字段契约，以及**重启恢复队列**（遗留任务重新入队、运行中中断后状态保留与断点续跑、复用阶段不覆盖已记录的模型、恢复次数超限、failed 终态不重跑）。

## 数据

- 数据库：`<仓库根>/data/music_studio.db`（WAL，`/data/` 已忽略）
- 运行日志：`<仓库根>/data/logs/music-studio-YYYY-MM-DD.log`（每天一个文件，保留 7 天）
- 阶段产物：`<仓库根>/data/outputs/<作品ID>/`（`stage1.json`、`stage2_polished.json`、`stage3.json`、`music/final_song.*`）
- 阶段产物存在即跳过重跑；音乐生成另有参数指纹 + `task_id` 落盘，中断后可续跑或只补下载。
- 旧库启动时自动补 `input_fingerprint` / `client_key` / `recovery_attempts` 三列与相关索引，历史数据不丢失。

## 接口与配置

见 [docs/api.md](../docs/api.md)、[安装指南](../docs/installation.md)。
