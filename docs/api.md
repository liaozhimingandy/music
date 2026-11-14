# 后端接口清单（Music Studio）

> 后端为 FastAPI + SQLite3，分层 `routers → service → dal → model`。
> 除 `/health` 外统一 `/api` 前缀；交互式文档：`http://localhost:8788/docs`。
> 字段与 `frontend/src/interface/index.ts` 一一对应，请求层由 `frontend/src/services/api.ts` 统一封装。

## 一、接口总览

| # | 方法 | 路径 | 前端是否调用 | 说明 |
| --- | --- | --- | --- | --- |
| 1 | GET | `/health` | 否 | 健康检查，返回应用名、运行模式、数据库路径 |
| 2 | GET | `/api/history?limit=25` | 是 | 作品列表，前端每 2 秒轮询；也是后端在线探测依据 |
| 3 | POST | `/api/llm` | 是 | 描述扩写 / 写词 / 起标题，一次创作流程调用 2~3 次 |
| 4 | POST | `/api/generations` | 是 | 创建作品，**立即返回 `queued`**，后台线程跑五阶段流程 |
| 5 | GET | `/api/generations/{id}` | 否（预留） | 作品详情 + 五阶段进度 |
| 6 | GET | `/api/generations/{id}/stages` | 否（预留） | 只看阶段进度 |
| 7 | GET | `/api/generations/{id}/audio` | 是（`audio_url`） | 音频二进制流 |
| 8 | DELETE | `/api/generations/{id}` | 是 | 删除作品、音频文件与阶段产物 |

> 后端未启动时前端自动进入**演示模式**（`services/demo.ts` 内存数据），界面与生成流程仍可预览。

## 二、生成任务的异步模型

```
POST /api/generations  ──> 写 songs(queued) + 预置 song_stages ──> 立即返回
                                   │
                                   └─ 线程池执行 MusicFlow 五阶段
                                      planning → lyrics → polish → music → music_generation
                                      每阶段：start_stage → 落盘 JSON → complete_stage → 更新 current_stage
                                      全部完成：status=ready + audio_path + title/lyrics/style_prompt
```

前端只轮询 `GET /api/history`，用 `status` 判断进度：

| status | 前端显示 | 含义 |
| --- | --- | --- |
| `queued` | 排队中 | 已入库，等待后台线程 |
| `processing` | 生成中 | 五阶段执行中（`current_stage` 给出当前阶段） |
| `ready` | 已完成 | 音频已下载，可播放 |
| `failed` | 生成失败 | `error_message` 给出原因，已完成阶段产物保留可续跑 |

## 三、请求 / 响应结构

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

`inputs.prompt` 作为五阶段流程的原始创作输入；`title` / `lyrics` 作为初值，流程结束后由润色与制作方案的结果覆盖（后端流程为准）。`instrumental=true` 时最终送生成接口的歌词为 `[Instrumental]`。

### GenerationResponse（`/api/history`、`POST /api/generations`）

| 字段 | 类型 | 来源 / 说明 |
| --- | --- | --- |
| `id` | string | UUID hex；同时是 `data/outputs/<id>/` 任务目录名 |
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

## 四、数据模型（SQLite3）

数据库文件：`data/music_studio.db`（WAL 模式，`/data/` 已忽略）。建表 DDL 见 `backend/app/models/db_schema.py`。

**songs**（按前端需要的字段建模）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT PK | 作品 ID |
| `title` `task_type` `mode` `model_variant` | TEXT | 前端展示元信息 |
| `status` `current_stage` | TEXT | 轮询状态 / 当前阶段 |
| `prompt` `lyrics` `style_prompt` | TEXT | 创作文本 |
| `instrumental` | INTEGER | 布尔 |
| `duration_seconds` `bpm` | INTEGER | 时长 / 速度 |
| `musical_key` `time_signature` | TEXT | 调性 / 拍号（响应里映射为 `key`） |
| `audio_url` `audio_path` `provider_task_id` | TEXT | 远端地址 / 本地文件 / ModelScope 任务 ID |
| `cover_color` `cover_icon` `cover_image_url` | TEXT | 封面 |
| `error_message` `metadata_json` `source_prompt` | TEXT | 错误 / 扩展信息 / 原始输入 |
| `created_at` `updated_at` | TEXT | 时间戳 |

**song_stages**：`id` / `song_id`（外键级联删除）/ `stage` / `status` / `output_file` / `error` / `started_at` / `finished_at`，`(song_id, stage)` 唯一。

## 五、五阶段流程与提示词

| 阶段 | 模板目录 | 产物 | 主要校验 |
| --- | --- | --- | --- |
| `planning` | `prompts/planning/` | `stage1.json` | `stage=PLANNING`、有 `creation_spec` |
| `lyrics` | `prompts/lyrics/` | `stage2.json` | `stage=LYRICS`、`song.lyrics` 非空 |
| `polish` | `prompts/polish/` | `stage2_polished.json` | 润色后 `song.title` / `song.lyrics` 非空 |
| `music` | `prompts/music/` | `stage3.json` | `target_model` 与配置一致、标题未被改动 |
| `music_generation` | — | `music_result.json` + 音频 | ModelScope 轮询成功并下载完成 |

产物落在 `data/outputs/<id>/`；存在即跳过重跑（断点续跑）。音乐生成另用参数指纹（model + prompt + lyrics + duration 的 SHA256）避免重复提交，任务记录 `music/music_task.json` 保存 `task_id`，超时后可继续轮询。

> `backend/app/prompts/` 下的模板是业务资产，代码只读取、不修改。

## 六、运行模式与配置

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

## 七、验证

```bash
# 离线端到端冒烟（建表 / LLM / 创建 / 五阶段 / 音频 / 删除）
backend/.venv/Scripts/python.exe backend/tests/smoke_test.py

# 启动真实服务
uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8788
curl http://localhost:8788/health
```
