# 后端接口清单（Music Studio）

> 后端为 FastAPI + SQLite3，分层 `routers → service → dal → model`，**全链路异步**（asyncio + aiosqlite + httpx.AsyncClient）。
> **所有接口统一 `/v1` 版本前缀**（含健康检查），交互式文档：`http://localhost:8788/docs`（文档端点本身不带版本号）。
> 字段与 `frontend/src/interface/index.ts` 一一对应，请求层由 `frontend/src/services/api.ts` 统一封装。

## 零、版本前缀约定

| 项 | 值 | 唯一来源 |
| --- | --- | --- |
| 后端前缀 | `API_V1_PREFIX = "/v1"` | `backend/app/constants.py` |
| 注册方式 | `api_v1 = APIRouter(prefix=API_V1_PREFIX)` 聚合四个子路由，`main.py` 只 `include_router(api_v1)` 一次 | `backend/app/routers/__init__.py` |
| 子路由路径 | `/health`、`/history`、`/llm`、`/generations`（**不写版本号**） | `backend/app/routers/*.py` |
| 前端基址 | `VITE_APP_API_BASE_URL=/v1`，业务里只写 `/history` 等相对路径 | `frontend/.env.development`、`frontend/src/services/api.ts` |
| 本地代理 | Vite dev / preview 把 `/v1` 代理到 `127.0.0.1:8788` | `frontend/vite.config.ts` |
| `audio_url` | `/v1/generations/{id}/audio`（模板常量 `AUDIO_URL_TEMPLATE`），前端用 `resolveAudioUrl()` 兼容同源代理与直连完整域名 | `song_service.py`、`services/api.ts` |

> 旧的 `/api/*` 与无前缀 `/health` 已下线（返回 404）。升级 v2 时在 `routers/__init__.py` 新建 `APIRouter(prefix="/v2")` 并按需复用子路由，v1 不受影响。

接口通用约定：所有路由都是 `async def`，服务通过 `Depends(get_song_service)` / `Depends(get_llm_service)` 注入；防护逻辑集中在 `backend/app/deps.py`，业务去重在 `SongService.create`。

## 一、接口总览

| # | 方法 | 路径 | 前端是否调用 | 说明 |
| --- | --- | --- | --- | --- |
| 1 | GET | `/v1/health` | 是（配额） | 健康检查：应用名、运行模式、数据库路径、当前 `music_model`，以及今日生成配额 `daily_limit` / `daily_used` / `daily_remaining` / `daily_reset_at`（不限流） |
| 2 | GET | `/v1/history?limit=25` | 是 | 作品列表，前端每 2 秒轮询；也是后端在线探测依据 |
| 3 | POST | `/v1/llm` | 仅辅助按钮 | 描述扩写 / 写词 / 起标题；**「开始生成」主链路不调用它**，后端五阶段已包含这些 LLM 步骤 |
| 4 | POST | `/v1/generations` | 是 | 创建作品，**立即返回 `queued`**，任务落入 SQLite 持久化队列，后台跑五阶段（重启可恢复） |
| 5 | GET | `/v1/generations/{id}` | 否（预留） | 作品详情 + 五阶段进度 |
| 6 | GET | `/v1/generations/{id}/stages` | 否（预留） | 只看阶段进度 |
| 7 | GET | `/v1/generations/{id}/audio` | 是（`audio_url`） | 音频二进制流 |
| 8 | DELETE | `/v1/generations/{id}` | 是 | 删除作品、音频文件与阶段产物 |

> 前端只对接真实后端，没有本地演示数据兜底；后端未启动时页面状态栏提示「后端未连接」。

## 二、生成任务的异步模型（持久化队列）

**数据库就是队列**：`songs` 表里 `status IN ('queued', 'processing')` 的每一行都是一个待办任务；`asyncio.Task` 只是本进程的执行载体，重启后由队列 worker 依据 DB 重建。

```
POST /v1/generations ─ 幂等查重 ─ 活跃任务闸门 ─ 写 songs(queued) + 预置 song_stages ─ 立即返回
                                                        │
                         ┌──────────────────────────────┴──────────────────────────────┐
                         ▼                                                             ▼
                 submit_job（本次请求）                         应用启动 / 队列巡检（重启后）
                         └──────────────────────────────┬──────────────────────────────┘
                                                        ▼
                         asyncio.Task + Semaphore（上限 MUSIC_STUDIO_MAX_ACTIVE_JOBS）
                         planning → lyrics → polish → music → music_generation
                         每阶段：start → 原子落盘 JSON → complete → 更新 current_stage
                         全部完成：status=ready + audio_path + 清零 recovery_attempts
```

- **启动恢复**：`main.py` 的 lifespan 在 `init_schema()` 后调用 `runtime.start_queue_worker()`，立刻扫描一次未完成任务重新入队；之后每 `MUSIC_STUDIO_QUEUE_POLL_SECONDS`（默认 3 秒）巡检一次，兜住任务崩溃 / 提交失败。`SongDAL.claim_recovery` 用带条件的 UPDATE 原子认领（`recovery_attempts + 1`），`runtime._running` 按 song_id 去重，不会重复跑同一首歌。
- **关闭不丢任务**：`shutdown()` 只 cancel 内存中的 task，**不改数据库状态**；被中断的任务仍是 `queued/processing`，下次启动继续。已落盘的阶段 JSON 直接跳过重跑，音乐阶段复用 `music_task.json` 里的远端 `task_id`，不重复计费。
- **只有两类情况不会继续**：① 接口层限制（413 / 422 / 429 / 幂等去重）——请求根本没落库；② 真实异常——`WorkflowService` 把作品置为 `failed` 终态（含 `error_message`），或同一任务连续中断超过 `MUSIC_STUDIO_MAX_RECOVERY_ATTEMPTS`（默认 5）被标记停止恢复，避免无限重跑消耗额度。任务正常完成后恢复计数清零，因此长期任务多次重启不会误触上限。
- 数据库走 `aiosqlite` 单条长连接 + 事务锁，文件 IO 走 `asyncio.to_thread`，HTTP 走 `httpx.AsyncClient`。等待 LLM / 远端音乐生成的几分钟里，event loop 仍能正常响应前端轮询。
- 关闭顺序：停巡检 → cancel 任务（保留 DB 状态）→ 释放 httpx 连接池 → 关闭数据库连接。
- **单进程约束**：队列 worker 与 `_running` 去重都在本进程内，请用单 worker 启动 uvicorn；需要多进程 / 多机部署时要额外引入行级租约或外部队列（Celery / Redis / 数据库 advisory lock）。

前端只轮询 `GET /v1/history`，用 `status` 判断进度：

| status | 前端显示 | 含义 |
| --- | --- | --- |
| `queued` | 排队中 | 已入库，等待后台任务 |
| `processing` | 生成中 | 五阶段执行中（`current_stage` 给出当前阶段） |
| `ready` | 已完成 | 音频已下载，可播放 |
| `failed` | 生成失败 | `error_message` 给出原因，已完成阶段产物保留可续跑 |

## 三、幂等去重与接口防护

### 3.1 重复提交（幂等）

`SongService.create` 对每次请求计算 `input_fingerprint`：

- **有 `Idempotency-Key` 请求头**：以该键的哈希为指纹（标准幂等语义，适合网络重试）；
- **无该头**：对 `title` / `task_type` / `mode` / `model_variant` / `inputs.*` 做规范化（文本去首尾空白、布尔统一、排除不影响生成的 `metadata`）后取 SHA256；`model_variant` 未传时按 `MUSIC_MODEL` 配置值归一化，保证「传/不传」算出同一指纹。

命中规则（查 `songs` 表，依赖 `idx_songs_fingerprint` 索引）：

| 已有记录状态 | 行为 |
| --- | --- |
| `queued` / `processing` | **无条件复用**，无论过了多久，绝不重复计费 |
| `ready` / `failed` 且创建时间在 `MUSIC_STUDIO_DEDUPE_WINDOW`（默认 30s）内 | 复用，防双击与客户端重试 |
| 窗口外的已完成记录 | 允许重新生成相同内容 |

「查重 + 插入」用 `asyncio.Lock` 串行化，杜绝并发双击各自插一条。命中去重时**返回 200 + 已有作品**（不是 409），响应结构与新建完全一致，仅多一个响应头：

```
X-Deduplicated: true
```

需要强制重新生成时加查询参数 `?force=true`。前端契约无需改动；`StudioPage.vue` 会把 413/422/429 的 `detail` 直接展示给用户。

#### 每日生成配额

每次生成都会真实消耗 LLM 与音乐生成额度，所以后端有一个**按自然日**的总额度兜底（`MUSIC_STUDIO_DAILY_LIMIT`，默认 50 首/天，`0` 表示不限制）：

- **计数口径**：当天创建的 `songs` 行数，排队/生成中/成功/失败都算（失败前可能已经调用过 LLM）；幂等重试命中已有作品不建新行，因此不吃配额。
- **日界**：按 `MUSIC_STUDIO_DAILY_RESET_UTC_OFFSET`（默认 `8`，即北京时间 00:00）划分，服务器跑 UTC 也不会错位。
- **检查位置**：`SongService.create` 内、幂等去重之后——重试与查询永远不会被额度拦掉。
- **命中时**：`429` + `Retry-After`（到明日重置的秒数）+ 中文说明，例如
  `今日生成额度已用完（每天最多 50 首），11-16 00:00 自动重置。可以继续试听作品库里的作品，或明天再来；部署者可用 MUSIC_STUDIO_DAILY_LIMIT 调整上限。`
- **未命中时**：创建响应带额度头 `X-Daily-Limit` / `X-Daily-Remaining` / `X-Daily-Reset`（已在 CORS 里 `expose_headers`，前端跨域也能读到），创作面板据此显示「今日还剩 N 首」，额度为 0 时按钮直接禁用并说明原因。

### 3.2 恶意 / 高频调用防护

| 防护 | 默认值 | 命中结果 |
| --- | --- | --- |
| 读接口滑动窗口限流（按客户端 IP 哈希） | 180 次 / 60s | `429` + `Retry-After` |
| `/v1/llm` 限流（消耗 LLM 额度） | 20 次 / 60s | `429` + `Retry-After` |
| `POST /v1/generations` 限流（消耗生成额度） | 5 次 / 60s | `429` + `Retry-After` |
| 全局活跃任务上限 | 4 | `429`（真新建时） |
| 单客户端活跃任务上限 | 2 | `429`（真新建时） |
| 每日生成配额（自然日，默认东八区） | 50 首/天 | `429` + `Retry-After`（到明日重置）+ `X-Daily-Remaining: 0` |
| 请求体大小（Content-Length） | 64 KiB | `413` |
| 请求体字段约束 | `inputs.prompt` 非空且 ≤ 500、`lyrics ≤ 8000`、`title ≤ 100`、`seed_prompt ≤ 1000`、`metadata ≤ 4096 字符`、`extra=forbid` | `422` |
| `limit` 查询参数 | `1 ~ 100` | `422` |
| `song_id` 路径参数 | `^[0-9a-f]{32}$` | `422` |

设计要点：

- **活跃任务闸门放在幂等判断之后**（在 `SongService.create` 内）。一次合法的 `Idempotency-Key` 重试只是取回已有作品、不会新增任务，不该被「任务已满」误拦。
- 限流按客户端 IP 的 SHA256 哈希分桶；默认**不信任** `X-Forwarded-For`（可伪造），仅当部署在可信反向代理后才开 `MUSIC_STUDIO_TRUST_PROXY=true`。
- 所有请求体继承 `StrictRequestModel`（`extra="forbid"`），避免夹带巨量未知字段绕过体积检查。
- 限流器是进程内实现（`services/rate_limiter.py`）。多 worker / 多机部署时把 `check()` 换成 Redis 原子计数即可，上层 `deps.py` 无需改动。

### 3.3 错误码

| 状态码 | 含义 | 典型响应 |
| --- | --- | --- |
| `200` | 创建成功，或命中幂等返回已有作品 | `GenerationResponse`，后者带 `X-Deduplicated: true` |
| `404` | 作品 / 音频不存在 | `{"detail": "作品不存在"}` |
| `413` | 请求体过大 | `{"detail": "请求体过大（上限 65536 字节）"}` |
| `422` | 参数校验失败（Pydantic / Query / Path） | `{"detail": [...]}` |
| `429` | 限流、活跃任务超限或今日额度用完 | `{"detail": "生成任务已达全局上限（4），请等待现有任务完成"}` / `{"detail": "今日生成额度已用完（每天最多 50 首），11-16 00:00 自动重置。…"}` |
| `500` | 服务端内部错误 | `{"detail": "服务器内部错误，请查看后端日志"}` |
| `502` | 上游 LLM 调用失败 | `{"detail": "LLM 调用失败：…"}` |

### 3.4 跨域（CORS）

前端 dev 直连后端（`http://127.0.0.1:5177` → `http://localhost:8000`）属于不同源。后端 `CORSMiddleware` 的配置：

| 项 | 默认值 | 说明 |
| --- | --- | --- |
| `allow_origins` | `CORS_ORIGINS`（空） | 额外白名单，支持逗号分隔或 JSON 数组；生产显式列出前端域名 |
| `allow_origin_regex` | `^https?://(localhost\|127\.0\.0\.1\|\[::1\])(:\d+)?$` | **默认放行任意端口的本机回环来源**，换端口不用改配置 |
| `allow_credentials` / `allow_methods` / `allow_headers` | `true` / `*` / `*` | 与 axios 的 JSON 请求头、下载音频兼容 |

**排坑**：浏览器报 `No 'Access-Control-Allow-Origin'` 时不一定是 CORS 配错。Starlette 的 `ServerErrorMiddleware` 在最外层，后端未捕获异常生成的纯文本 500 不经过 CORS 中间件，浏览器就会误报跨域。本项目在 `app/middleware.py` 用纯 ASGI 中间件（挂在 CORSMiddleware 内层）把异常转成 JSON 500，让响应继续走 CORS 流程，同时在后端日志留完整 traceback。排查时先 `curl -i -H "Origin: http://127.0.0.1:5177" http://localhost:8000/v1/health`，再看后端日志。

> 想彻底避开跨域：把 `VITE_APP_API_BASE_URL` 设回 `/v1` 走 Vite 同源代理，并用 `VITE_API_PROXY_TARGET` 指向自己启动的后端端口。

## 四、请求 / 响应结构

### POST /v1/llm

仅由「AI 扩写描述」等**用户主动点击的辅助按钮**调用；点「开始生成」不会走这里。

```json
{ "task": "prompt", "seed_prompt": "夏末站台告别", "instrumental": false, "style_tags": [] }
```

`task` 取 `prompt` / `lyrics` / `title`。响应：

```json
{ "task": "prompt", "output": "……", "provider": "deepseek-ai/DeepSeek-V4.1-Flash-1017", "metadata": {} }
```

未配置 LLM Key 或开启离线模式时返回本地兜底文本，`provider` 为 `local`。

### POST /v1/generations

前端「开始生成」只提交用户原始描述，一次请求即入队：

```json
{
  "task_type": "text2music",
  "mode": "simple",
  "inputs": { "prompt": "……", "instrumental": false }
}
```

- `inputs.prompt` **必填且不能全为空白**（前端按钮会禁用空输入，服务端再以 422 兜底）；它作为五阶段流程的原始创作输入。
- `title` / `inputs.lyrics` / `model_variant` 都可省略：标题与歌词由后端润色阶段产出并覆盖，模型由后端 `MUSIC_MODEL` 回填。显式传值时作为初值/展示标记保留，不会改变实际生成用的模型。
- `instrumental=true` 时最终送生成接口的歌词为 `[Instrumental]`。

可选查询参数 `force=true`（跳过去重）；可选请求头 `Idempotency-Key: <任意字符串>`（自定义幂等键）。

#### 模型取值约定（model_variant 用哪里的枚举）

| 项 | 说明 |
| --- | --- |
| 唯一来源 | `backend/app/config.py` 的 `music_model`，即环境变量 `MUSIC_MODEL`，默认 **`ACE-Step/acestep-v15-xl-sft`**（ModelScope 上的 ACE-Step 模型 ID） |
| 谁在使用 | `MusicProvider` 提交给 `/v1/audios/generations` 的 `model` 参数、`stage_steps.run_music_prompt` 的 `target_model` 模板变量与强校验、`metadata.target_model` |
| 前端 | **不需要维护枚举**：提交时不传 `model_variant`；要查询当前值读 `GET /v1/health` 的 `music_model`；展示用 `variantLabel()` / `variantShortLabel()` 兜底未知值 |
| 历史值 | 旧演示数据的 `base` / `turbo` / `shift` 只是展示标记；`GenerationResponse.model_variant` 固定为 `str`，避免旧记录让 `/v1/history` 500 |
| 换模型 | 先确认 `prompts/music/system.md` 里的 `target_model` 示例兼容——提示词不可修改，而阶段四会强校验 `target_model == settings.music_model`，改错会导致生成失败 |

### GenerationResponse（`/v1/history`、`POST /v1/generations`）

| 字段 | 类型 | 来源 / 说明 |
| --- | --- | --- |
| `id` | string | **UUIDv7** 的 32 位小写 hex（时间有序）；同时是 `data/outputs/<id>/` 任务目录名 |
| `title` / `prompt` / `lyrics` | string? | 润色后的标题、扩写描述、拍平后的分段歌词 |
| `task_type` / `mode` / `model_variant` | string | `task_type` / `mode` 为前端提交值；`model_variant` 由 `MUSIC_MODEL` 回填，响应侧固定为 `str` 以兼容历史 `base` / `turbo` / `shift`（见上节取值约定） |
| `status` | string | `queued` / `processing` / `ready` / `failed` |
| `instrumental` | bool | 是否纯音乐 |
| `duration_seconds` / `bpm` / `key` / `time_signature` | number? / string? | 制作方案与策划阶段解析结果 |
| `audio_url` | string? | 有本地音频时为 `/v1/generations/{id}/audio` |
| `cover_color` / `cover_icon` | string? | 由作品 ID 稳定推导，前端画渐变封面 |
| `cover_image_url` | string? | 当前固定为 null（前端已移除封面上传） |
| `error_message` | string? | 失败原因 |
| `metadata` | object | `source` / `target_model` / `genre` / `mood` / `remote_audio_url` |
| `created_at` / `updated_at` | string | ISO 8601 UTC |
| `current_stage` / `style_prompt` | string? | 附加字段，前端可忽略 |

`GET /v1/generations/{id}` 额外返回 `stages: [{ stage, label, status, output_file, error, started_at, finished_at, model, usage, metrics }]`。

阶段遥测字段说明（排查问题直接看后三个）：

| 字段 | 含义 |
| --- | --- |
| `model` | 该阶段实际使用的模型：阶段 1~4 是 LLM 模型名，阶段 5 是音乐生成模型 ID |
| `usage` | OpenAI 风格 token 用量 `{prompt_tokens, completion_tokens, total_tokens}`；阶段 5（音乐生成）与 mock 客户端为 `null` |
| `metrics` | 扩展指标，至少含 `elapsed_ms`（该阶段耗时）；阶段 5 另有 `provider_task_id`（远端任务号，可拿去 ModelScope 后台查证）与 `duration_seconds` |

## 五、数据模型（SQLite3）

数据库文件：`data/music_studio.db`（WAL，`/data/` 已忽略）。DDL 见 `backend/app/models/db_schema.py`。

**songs**（按前端需要的字段建模）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT PK | UUIDv7 hex，时间有序主键 |
| `title` `task_type` `mode` `model_variant` | TEXT | 前端展示元信息 |
| `status` `current_stage` | TEXT | 轮询状态 / 当前阶段 |
| `prompt` `lyrics` `style_prompt` | TEXT | 创作文本 |
| `instrumental` | INTEGER | 布尔 |
| `duration_seconds` `bpm` | INTEGER | 时长 / 速度 |
| `musical_key` `time_signature` | TEXT | 调性 / 拍号（响应里映射为 `key`） |
| `audio_url` `audio_path` `provider_task_id` | TEXT | 远端地址 / 本地文件 / ModelScope 任务 ID |
| `cover_color` `cover_icon` `cover_image_url` | TEXT | 封面 |
| `error_message` `metadata_json` `source_prompt` | TEXT | 错误 / 扩展信息 / 原始输入 |
| `input_fingerprint` `client_key` | TEXT | **幂等指纹 / 客户端 IP 哈希**（不返回给前端） |
| `created_at` `updated_at` | TEXT | 时间戳 |

索引：`idx_songs_created_at`、`idx_songs_status`、`idx_songs_fingerprint`、`idx_songs_client`。

> **旧库平滑升级**：`init_schema()` 会用 `PRAGMA table_info(songs)` 检查缺失列并 `ALTER TABLE ADD COLUMN`，再建依赖新列的索引。已有的 `data/music_studio.db` 无需删除，历史作品（含 UUIDv4 ID）完整保留。
>
> **连接模型**：进程内复用一条 `aiosqlite` 长连接 + `asyncio.Lock` 事务锁。WAL 只在启动时设置一次；曾因「每操作一连接 + 每次重设 WAL」在 Windows 并发下触发 `attempt to write a readonly database`，详见 `dal/database.py` 的模块说明。

**song_stages**：`id` / `song_id`（外键级联删除）/ `stage` / `status` / `output_file` / `error` / `started_at` / `finished_at`，加阶段遥测列 `model` / `prompt_tokens` / `completion_tokens` / `total_tokens` / `metrics_json`，`(song_id, stage)` 唯一。

## 六、五阶段流程与提示词

| 阶段 | 模板目录 | 产物 | 主要校验 |
| --- | --- | --- | --- |
| `planning` | `prompts/planning/` | `stage1.json` | `stage=PLANNING`、有 `creation_spec` |
| `lyrics` | `prompts/lyrics/` | `stage2.json` | `stage=LYRICS`、`song.lyrics` 非空 |
| `polish` | `prompts/polish/` | `stage2_polished.json` | 润色后 `song.title` / `song.lyrics` 非空 |
| `music_prompt` | `prompts/music/`（目录沿用旧名，提示词不能改名） | `stage3.json` | `target_model` 与配置一致、标题未被改动 |
| `music_generation` | — | `music_result.json` + 音频 | ModelScope 轮询成功并下载完成 |

产物落在 `data/outputs/<id>/`；存在即跳过重跑（断点续跑）。音乐生成另用参数指纹（model + prompt + lyrics + duration 的 SHA256）避免重复提交，任务记录 `music/music_task.json` 保存 `task_id`，超时后可继续轮询。

> `backend/app/prompts/` 下的模板是业务资产，代码只读取、不修改。

## 七、运行模式与配置

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `OPENAIM_API_KEY` | 空 | LLM Key（兼容 `OPENAI_API_KEY` / `LLM_API_KEY`） |
| `LLM_BASE_URL` / `LLM_MODEL` | 见 `.env.example` | OpenAI 兼容端点与模型 |
| `LLM_CA_CERT` | 空 | 可选自签 CA 路径，文件不存在时回退系统校验 |
| `MUSIC_API_TOKEN` | 空 | ModelScope token，**只放 .env，不进代码** |
| `MUSIC_API_BASE_URL` / `MUSIC_MODEL` | 见 `.env.example` | 生成接口与模型 |
| `MUSIC_STUDIO_MOCK` | `false` | `true` 时全流程离线跑（本地 WAV + 桩数据），不消耗密钥 |
| `ACE_STEP_HOST` / `ACE_STEP_PORT` | `0.0.0.0` / `8788` | 启动脚本沿用 |
| `CORS_ORIGINS` | 空 | 额外允许的跨域来源，逗号分隔或 JSON 数组（见 3.4） |
| `MUSIC_STUDIO_CORS_ORIGIN_REGEX` | 回环地址正则 | 默认放行 localhost / 127.0.0.1 / [::1] 任意端口；设为空可关闭 |
| `MUSIC_STUDIO_DATA_DIR` | `<仓库根>/data` | 数据库与产物目录 |

接口防护配置（完整注释见 `backend/app/.env.example`）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MUSIC_STUDIO_RATE_LIMIT_ENABLED` | `true` | 总开关；关闭后所有限流与并发闸门失效，仅用于本地调试 |
| `MUSIC_STUDIO_RATE_LIMIT_WINDOW` | `60` | 滑动窗口长度（秒） |
| `MUSIC_STUDIO_RATE_LIMIT_READ` / `_LLM` / `_CREATE` | `180` / `20` / `5` | 各接口窗口内最大请求数 |
| `MUSIC_STUDIO_MAX_ACTIVE_JOBS` | `4` | 全局同时排队/生成中的任务上限 |
| `MUSIC_STUDIO_MAX_ACTIVE_PER_CLIENT` | `2` | 单客户端活跃任务上限 |
| `MUSIC_STUDIO_DAILY_LIMIT` | `50` | 每日生成配额（自然日）；`0` 表示不限制 |
| `MUSIC_STUDIO_DAILY_RESET_UTC_OFFSET` | `8` | 配额日界的时区偏移（小时），默认北京时间 00:00 重置 |
| `MUSIC_STUDIO_DEDUPE_WINDOW` | `30` | 已完成作品的重复提交窗口（秒） |
| `MUSIC_STUDIO_MAX_REQUEST_BYTES` | `65536` | 请求体上限（字节） |
| `MUSIC_STUDIO_TRUST_PROXY` | `false` | 是否信任 `X-Forwarded-For`（仅可信反代后开启） |

持久化队列配置：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MUSIC_STUDIO_QUEUE_POLL_SECONDS` | `3` | 队列巡检间隔（秒）；启动时还会立即额外扫描一次 |
| `MUSIC_STUDIO_MAX_RECOVERY_ATTEMPTS` | `5` | 同一任务连续中断后最多自动恢复几次，`0` 表示禁用；任务正常完成后清零 |

## 八、验证

```bash
# 离线端到端冒烟：/v1 前缀 / CORS / 建表 / UUIDv7 / LLM / 五阶段 / 阶段遥测 / 幂等 / 413-422-429 / 每日配额 / 空描述 / 模型兜底 / 音频 / 级联删除 / 重启恢复队列（89 项；启用 DELETE 路由时 94 项）
uv run --project backend python backend/tests/smoke_test.py

# 启动真实服务
uv run --project backend uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8788
curl http://localhost:8788/v1/health
curl http://localhost:8788/v1/history
```
