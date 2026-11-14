# 后端接口清单

> 本文档由前端改造过程中梳理而来，用于前后端对齐。后端为 FastAPI，除 `/health` 外统一使用 `/api` 前缀。
> 核对依据：`backend/app/routers/*.py` 中的 `APIRouter(prefix=...)` 与路由装饰器、`backend/app/schemas/*.py` 中的请求/响应模型。

## 一、结论速览

| 分类 | 数量 | 说明 |
| --- | --- | --- |
| 后端已实现 | 17 | 配置 2、生成任务 9、作品列表 1、文本任务 2、模型清单 2、健康检查 1 |
| 精简后前端实际调用 | 6 | 作品列表、文本扩写、创建任务、删除作品、音频流、封面图 |
| 建议后端补充 | 7 | 进度查询/推送、取消、分页搜索、音频上传、波形、设备信息、统一健康探针 |

前端已完成精简：移除设置面板、接口清单面板、自定义模式、封面编辑等，**不再调用**配置、模型清单、外部模型列表、封面上传/删除/重生成、作品信息修改这几组接口（后端仍然保留，可供后续功能使用）。

---

## 二、精简后前端实际调用的接口

| # | 方法 | 路径 | 调用位置 | 说明 |
| --- | --- | --- | --- | --- |
| 1 | GET | `/api/history` | 作品库（每 2 秒轮询）、启动探活 | 返回 `GenerationResponse[]`；前端用 `Array.isArray(响应)` 判断后端是否在线 |
| 2 | POST | `/api/llm` | 「简单创作」自动扩写描述、写词、起标题；「AI 扩写描述」按钮 | 一次生成流程调用 2~3 次 |
| 3 | POST | `/api/generations` | 「开始生成」 | 提交生成任务，后端异步执行 |
| 4 | DELETE | `/api/generations/{id}` | 作品卡片 / 详情面板 / 播放栏「删除」 | 删除作品 |
| 5 | GET | `/api/generations/{id}/audio` | 播放栏 `audio_url`、下载按钮 | 音频二进制流，由后端在响应里拼好 URL |
| 6 | GET | `/api/generations/{id}/cover` | 作品卡片 / 详情面板 `cover_image_url` | 图片二进制流 |

> 后端未启动时前端自动进入**演示模式**：上述调用全部走本地内存示例数据，界面与完整生成流程仍可预览；后端启动后刷新页面自动切回真实接口。

---

## 三、后端已实现的接口（17 个）

### 健康检查

| 方法 | 路径 | 用途 | 请求 | 响应 | 当前前端 |
| --- | --- | --- | --- | --- | --- |
| GET | `/health` | 健康检查 | — | `{ "status": "ok" }` | 未使用 |

> 注意：该路径在 `/api` 之外，Vite 的 `/api` 代理不会覆盖它。建议补一个 `/api/health` 便于统一代理与探活。

### 配置（`/api/config`）

| 方法 | 路径 | 用途 | 请求 | 响应 | 当前前端 |
| --- | --- | --- | --- | --- | --- |
| GET | `/api/config` | 读取运行时配置 | — | `ConfigResponse`（约 45 个字段） | 未使用 |
| PUT | `/api/config` | 保存运行时配置 | `ConfigUpdateRequest`（可只传部分字段） | `ConfigResponse` | 未使用 |

### 生成任务（`/api/generations`）

| 方法 | 路径 | 用途 | 请求 | 响应 | 当前前端 |
| --- | --- | --- | --- | --- | --- |
| POST | `/api/generations` | 创建音乐生成任务 | `GenerationCreate` | `GenerationResponse` | 使用 |
| GET | `/api/generations/{id}` | 查询单个任务 | 路径参数 `id` | `GenerationResponse` | 未使用（列表已带回全量字段） |
| PUT | `/api/generations/{id}` | 修改作品标题/描述/歌词/状态 | `GenerationUpdate` | `GenerationResponse` | 未使用 |
| DELETE | `/api/generations/{id}` | 删除作品 | 路径参数 `id` | `{ "status": "deleted" }` | 使用 |
| GET | `/api/generations/{id}/audio` | 播放 / 下载音频 | 路径参数 `id` | 音频二进制流 | 使用 |
| GET | `/api/generations/{id}/cover` | 读取封面图 | 路径参数 `id` | 图片二进制流 | 使用 |
| POST | `/api/generations/{id}/cover` | 上传封面图 | `multipart/form-data: file` | `GenerationResponse` | 未使用 |
| DELETE | `/api/generations/{id}/cover` | 移除封面图 | 路径参数 `id` | `GenerationResponse` | 未使用 |
| POST | `/api/generations/{id}/cover/regenerate` | 重新生成封面 | 路径参数 `id` | `GenerationResponse` | 未使用 |

> 创建任务为异步执行，需要本地 ACE-Step 仓库及模型权重；`/cover/regenerate` 还依赖 fal / ComfyUI / A1111 之一。

### 作品列表（`/api/history`）

| 方法 | 路径 | 用途 | 请求 | 响应 | 当前前端 |
| --- | --- | --- | --- | --- | --- |
| GET | `/api/history` | 作品列表 | `limit`（默认 25） | `GenerationResponse[]` | 使用 |

> 目前固定上限 25 条，缺少分页与筛选。

### 文本任务（`/api/llm`）

| 方法 | 路径 | 用途 | 请求 | 响应 | 当前前端 |
| --- | --- | --- | --- | --- | --- |
| POST | `/api/llm` | 描述扩写 / 写词 / 起标题 / 封面提示词 | `LLMTaskRequest` | `LLMTaskResponse` | 使用 |
| GET | `/api/llm/models` | 列出 OpenAI 兼容端点可用模型 | `endpoint`、`api_key` | `string[]` | 未使用 |

> `/api/llm` 未配置 OpenAI 兼容端点时走 ACE LM 占位逻辑。

### 模型清单（`/api/models`）

| 方法 | 路径 | 用途 | 请求 | 响应 | 当前前端 |
| --- | --- | --- | --- | --- | --- |
| GET | `/api/models` | 检查点清单与可用状态 | — | `ModelSpec[]`（`status`: `missing` / `available` / `downloading`） | 未使用 |
| POST | `/api/models/{id}/download` | 下载模型权重 | 路径参数 `id` | `{ "status": "started", "model_id": ... }` | 未使用 |

---

## 四、建议后端补充的接口（7 个）

| # | 方法 | 路径 | 用途 | 请求 | 建议响应 | 优先级 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | GET | `/api/generations/{id}/status` | 单任务进度查询 | 路径参数 `id` | `{ status, progress, stage, error }` | 高 |
| 2 | WS / SSE | `/api/generations/{id}/events` | 生成进度实时推送 | 订阅 | 阶段与进度事件流 | 高 |
| 3 | POST | `/api/generations/{id}/cancel` | 取消生成任务 | 路径参数 `id` | `{ "status": "cancelled" }` | 高 |
| 4 | GET | `/api/history?cursor=&keyword=` | 分页 / 搜索作品 | `cursor`、`keyword`、`status` | `{ items, nextCursor, total }` | 中 |
| 5 | POST | `/api/uploads/audio` | 上传参考音频（续写 / 提取 / 补全） | `multipart/form-data: file` | `{ audio_id, duration }` | 中 |
| 6 | GET | `/api/generations/{id}/waveform` | 音频波形数据 | 路径参数 `id` | 峰值数组或 PNG | 低 |
| 7 | GET | `/api/system/hardware` | 设备与加速信息 | — | `{ device, vram, backend }` | 低 |

补充说明：

1. 目前每 2 秒拉取一次全量列表来判断任务状态，作品数上来后浪费明显；用 1 + 2 替代轮询。
2. 生成中任务现在只能删除本地占位卡片，服务端任务无法中止，建议补 3。
3. 作品数超过 25 条后必须补 4，否则历史记录查不到。
4. 建议补 `GET /api/health`，与 `/api/*` 统一走代理。

---

## 五、主要数据模型

### `GenerationCreate`（POST `/api/generations`）

```json
{
  "title": "晚风经过站台",
  "task_type": "text2music",
  "mode": "simple",
  "model_variant": "turbo",
  "inputs": {
    "prompt": "华语独立流行，温柔女声，木吉他与弦乐铺底",
    "lyrics": "[主歌]…",
    "instrumental": false,
    "bpm": null,
    "duration_seconds": null,
    "key": null,
    "time_signature": null
  },
  "metadata": {}
}
```

`task_type`: `text2music` / `cover` / `repaint`；`mode`: `simple` / `custom`；`model_variant`: `base` / `turbo` / `shift`。
精简后的前端固定提交 `task_type=text2music`、`mode=simple`、`model_variant=turbo`，`inputs` 只带 `prompt` / `lyrics` / `instrumental`（其余字段后端有默认值，可省略）。

### `GenerationResponse`（列表与创建返回值）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 作品 ID |
| `title` | string \| null | 标题 |
| `status` | string | `queued` / `processing` / `ready` / `failed` |
| `task_type` / `mode` / `model_variant` | string | 任务类型、模式、模型版本 |
| `prompt` / `lyrics` | string \| null | 描述与歌词 |
| `instrumental` | bool | 是否纯音乐 |
| `duration_seconds` / `bpm` / `key` / `time_signature` | number \| string \| null | 音频参数 |
| `audio_url` / `cover_image_url` | string \| null | 音频与封面地址 |
| `cover_color` / `cover_icon` | string \| null | 无封面时的占位渐变与图标 |
| `metadata_json`（序列化为 `metadata`） | object | 扩展参数 |
| `created_at` / `updated_at` | datetime | 创建 / 更新时间 |

### `LLMTaskRequest` / `LLMTaskResponse`（POST `/api/llm`）

```json
// 请求
{ "task": "prompt", "seed_prompt": "一首关于夏末告别的民谣", "instrumental": false, "style_tags": [], "language": null }
// 响应
{ "task": "prompt", "output": "华语独立民谣…", "provider": "ace", "metadata": {} }
```

`task`: `prompt` / `lyrics` / `title` / `image`；`provider`: `ace` / `openai-compat`。

---

## 六、联调说明

- 前端开发/预览服务在 `5175`，通过 Vite 代理把 `/api` 转发到 `http://127.0.0.1:8788`（配置见 `frontend/vite.config.ts`）。
- 后端默认端口 `8788`；`/health` 不走 `/api` 代理，需要直接访问后端地址。
- 未安装 ACE-Step 仓库、模型权重与 torch/CUDA 时，创建任务会失败；前端可单独启动用于界面预览。
- 完整安装步骤见 [installation.md](./installation.md)。
