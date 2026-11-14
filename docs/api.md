# 后端接口清单（Music Studio）

> 后端为 FastAPI + SQLite3，分层 `routers → service → dal → model`，**全链路异步**（asyncio + aiosqlite + httpx.AsyncClient）。
> 除 `/health` 外统一 `/api` 前缀；交互式文档：`http://localhost:8788/docs`。
> 字段与 `frontend/src/interface/index.ts` 一一对应，请求层由 `frontend/src/services/api.ts` 统一封装。

所有路由都是 `async def`，服务通过 `Depends(get_song_service)` / `Depends(get_llm_service)` 注入；防护逻辑集中在 `backend/app/deps.py`，业务去重在 `SongService.create`。

## 一、接口总览

| # | 方法 | 路径 | 前端是否调用 | 说明 |
| --- | --- | --- | --- | --- |
| 1 | GET | `/health` | 否 | 健康检查，返回应用名、运行模式、数据库路径（不限流） |
| 2 | GET | `/api/history?limit=25` | 是 | 作品列表，前端每 2 秒轮询；也是后端在线探测依据 |
| 3 | POST | `/api/llm` | 是 | 描述扩写 / 写词 / 起标题，一次创作流程调用 2~3 次 |
| 4 | POST | `/api/generations` | 是 | 创建作品，**立即返回 `queued`**，后台 asyncio 任务跑五阶段 |
| 5 | GET | `/api/generations/{id}` | 否（预留） | 作品详情 + 五阶段进度 |
| 6 | GET | `/api/generations/{id}/stages` | 否（预留） | 只看阶段进度 |
| 7 | GET | `/api/generations/{id}/audio` | 是（`audio_url`） | 音频二进制流 |
| 8 | DELETE | `/api/generations/{id}` | 是 | 删除作品、音频文件与阶段产物 |

> 后端未启动时前端自动进入**演示模式**（`services/demo.ts` 内存数据），界面与生成流程仍可预览。

## 二、生成任务的异步模型

```
POST /api/generations  ──> 幂等查重 ──> 活跃任务闸门 ──> 写 songs(queued) + 预置 song_stages ──> 立即返回
                                                              │
                                                              └─ asyncio.create_task（Semaphore 限并发）
                                                                 planning → lyrics → polish → music → music_generation
                                                                 每阶段：start → 落盘 JSON → complete → 更新 current_stage
                                                                 全部完成：status=ready + audio_path + title/lyrics/style_prompt
```

- 后台任务由 `services/runtime.py` 用 `asyncio.Task` + `asyncio.Semaphore` 调度（不再是线程池），上限 `MUSIC_STUDIO_MAX_ACTIVE_JOBS`。
- 数据库走 `aiosqlite` 单条长连接 + 事务锁，文件 IO 走 `asyncio.to_thread`，HTTP 走 `httpx.AsyncClient`。等待 LLM / 远端音乐生成的几分钟里，event loop 仍能正常响应前端轮询。
- 应用关闭时依次取消未完成后台任务（写为 `failed`）→ 释放 httpx 连接池 → 关闭数据库连接。

前端只轮询 `GET /api/history`，用 `status` 判断进度：

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
- **无该头**：对 `title` / `task_type` / `mode` / `model_variant` / `inputs.*` 做规范化（文本去首尾空白、布尔统一、排除不影响生成的 `metadata`）后取 SHA256。

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

### 3.2 恶意 / 高频调用防护

| 防护 | 默认值 | 命中结果 |
| --- | --- | --- |
| 读接口滑动窗口限流（按客户端 IP 哈希） | 180 次 / 60s | `429` + `Retry-After` |
| `/api/llm` 限流（消耗 LLM 额度） | 20 次 / 60s | `429` + `Retry-After` |
| `POST /api/generations` 限流（消耗生成额度） | 5 次 / 60s | `429` + `Retry-After` |
| 全局活跃任务上限 | 4 | `429`（真新建时） |
| 单客户端活跃任务上限 | 2 | `429`（真新建时） |
| 请求体大小（Content-Length） | 64 KiB | `413` |
| 请求体字段约束 | `prompt ≤ 500`、`lyrics ≤ 8000`、`title ≤ 100`、`seed_prompt ≤ 1000`、`metadata ≤ 4096 字符`、`extra=forbid` | `422` |
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
| `429` | 限流或活跃任务超限 | `{"detail": "生成任务已达全局上限（4），请等待现有任务完成"}` |
| `502` | 上游 LLM 调用失败 | `{"detail": "LLM 调用失败：…"}` |

## 四、请求 / 响应结构

### POST /api/llm

```json
{ "task": "prompt", "seed_prompt": "夏末站台告别", "instrumental": false, "style_tags": [] }
```

`task` 取 `prompt` / `lyrics` / `title`。响应：

```json
{ "task": "prompt", "output": "……", "provider": "deepseek-ai/DeepSeek-V4.1-Flash-1017", "metadata": {} }
```

未配置 LLM Key 或开启离线模式时返回本地兜底文本，`provider` 为 `local`。

### POST /api/generations

```json
{
  "title": "夏末告别",
  "task_type": "text2music",
  "mode": "simple",
  "model_variant": "turbo",
  "inputs": { "prompt": "……", "lyrics": "[Instrumental]", "instrumental": false }
}
```

可选查询参数 `force=true`（跳过去重）；可选请求头 `Idempotency-Key: <任意字符串>`（自定义幂等键）。

`inputs.prompt` 作为五阶段流程的原始创作输入；`title` / `lyrics` 作为初值，流程结束后由润色与制作方案的结果覆盖（后端流程为准）。`instrumental=true` 时最终送生成接口的歌词为 `[Instrumental]`。

### GenerationResponse（`/api/history`、`POST /api/generations`）

| 字段 | 类型 | 来源 / 说明 |
| --- | --- | --- |
| `id` | string | **UUIDv7** 的 32 位小写 hex（时间有序）；同时是 `data/outputs/<id>/` 任务目录名 |
| `title` / `prompt` / `lyrics` | string? | 润色后的标题、扩写描述、拍平后的分段歌词 |
| `task_type` / `mode` / `model_variant` | string | 前端提交值 |
| `status` | string | `queued` / `processing` / `ready` / `failed` |
| `instrumental` | bool | 是否纯音乐 |
| `duration_seconds` / `bpm` / `key` / `time_signature` | number? / string? | 制作方案与策划阶段解析结果 |
| `audio_url` | string? | 有本地音频时为 `/api/generations/{id}/audio` |
| `cover_color` / `cover_icon` | string? | 由作品 ID 稳定推导，前端画渐变封面 |
| `cover_image_url` | string? | 当前固定为 null（前端已移除封面上传） |
| `error_message` | string? | 失败原因 |
| `metadata` | object | `source` / `target_model` / `genre` / `mood` / `remote_audio_url` |
| `created_at` / `updated_at` | string | ISO 8601 UTC |
| `current_stage` / `style_prompt` | string? | 附加字段，前端可忽略 |

`GET /api/generations/{id}` 额外返回 `stages: [{ stage, label, status, output_file, error, started_at, finished_at }]`。

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

**song_stages**：`id` / `song_id`（外键级联删除）/ `stage` / `status` / `output_file` / `error` / `started_at` / `finished_at`，`(song_id, stage)` 唯一。

## 六、五阶段流程与提示词

| 阶段 | 模板目录 | 产物 | 主要校验 |
| --- | --- | --- | --- |
| `planning` | `prompts/planning/` | `stage1.json` | `stage=PLANNING`、有 `creation_spec` |
| `lyrics` | `prompts/lyrics/` | `stage2.json` | `stage=LYRICS`、`song.lyrics` 非空 |
| `polish` | `prompts/polish/` | `stage2_polished.json` | 润色后 `song.title` / `song.lyrics` 非空 |
| `music` | `prompts/music/` | `stage3.json` | `target_model` 与配置一致、标题未被改动 |
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
| `MUSIC_STUDIO_DATA_DIR` | `<仓库根>/data` | 数据库与产物目录 |

接口防护配置（完整注释见 `backend/app/.env.example`）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MUSIC_STUDIO_RATE_LIMIT_ENABLED` | `true` | 总开关；关闭后所有限流与并发闸门失效，仅用于本地调试 |
| `MUSIC_STUDIO_RATE_LIMIT_WINDOW` | `60` | 滑动窗口长度（秒） |
| `MUSIC_STUDIO_RATE_LIMIT_READ` / `_LLM` / `_CREATE` | `180` / `20` / `5` | 各接口窗口内最大请求数 |
| `MUSIC_STUDIO_MAX_ACTIVE_JOBS` | `4` | 全局同时排队/生成中的任务上限 |
| `MUSIC_STUDIO_MAX_ACTIVE_PER_CLIENT` | `2` | 单客户端活跃任务上限 |
| `MUSIC_STUDIO_DEDUPE_WINDOW` | `30` | 已完成作品的重复提交窗口（秒） |
| `MUSIC_STUDIO_MAX_REQUEST_BYTES` | `65536` | 请求体上限（字节） |
| `MUSIC_STUDIO_TRUST_PROXY` | `false` | 是否信任 `X-Forwarded-For`（仅可信反代后开启） |

## 八、验证

```bash
# 离线端到端冒烟：建表 / UUIDv7 / LLM / 五阶段 / 幂等 / 413-422-429 / 音频 / 级联删除（52 项）
backend/.venv/Scripts/python.exe backend/tests/smoke_test.py

# 启动真实服务
uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8788
curl http://localhost:8788/health
```
