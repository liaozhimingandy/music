# Music Studio Backend

FastAPI + SQLite3 的 AI 音乐创作后端。把一段创作灵感经过五个阶段变成可播放的歌曲：
`策划 → 歌词 → 润色 → 音乐制作方案 → 音频生成`。LLM 走 OpenAI 兼容端点，音频走 ModelScope ACE-Step 异步推理。

## 分层

```text
backend/app/
├── main.py            # FastAPI 入口（lifespan 建表、CORS、路由）
├── config.py          # pydantic-settings 配置（密钥只在 .env）
├── constants.py       # 阶段名、产物名、状态码
├── routers/           # 接口层：参数校验 + 响应组装
│   ├── history.py  generations.py  llm.py  meta.py
├── services/          # 业务层：service（不反向依赖 routers）
│   ├── runtime.py            # 单例装配 + 后台线程池
│   ├── song_service.py       # 作品 CRUD / 前端响应映射 / 提交任务
│   ├── llm_service.py        # 扩写、写词、起标题
│   ├── workflow_service.py   # 五阶段编排 + 进度落库 + 断点续跑
│   ├── stage_steps.py        # 五个可复用阶段函数（纯逻辑）
│   ├── prompt_manager.py     # Jinja2 渲染 prompts/（只读）
│   ├── llm_client.py         # httpx OpenAI 兼容客户端 + 离线 mock
│   ├── music_provider.py     # ModelScope 提交/轮询/下载 + 离线 mock
│   └── cover_theme.py        # 封面配色
├── dal/               # 数据访问层（SQLite3 + 产物文件）
│   ├── database.py  song_dal.py  stage_dal.py  artifact_dal.py
├── models/            # 模型层：表结构 / 实体 / API Schema
│   ├── db_schema.py  entities.py  schemas.py
└── prompts/           # 四个阶段的 system.md / user.md（业务资产，代码不修改）
```

依赖方向严格单向：`routers → service → dal → model`。`dal` 不含业务判断，`service` 不直接写 SQL。

## 代码导览（给维护者）

所有源码都有模块级 docstring 与方法注释，按下面的顺序读最快：

1. `constants.py` + `config.py` —— 阶段名、产物名、状态枚举与全部运行开关。
2. `models/` —— `db_schema.py`（两张表 DDL）、`entities.py`（数据库行 ↔ 实体）、`schemas.py`（前后端契约）。
3. `dal/` —— 每个方法只做一次 SQL / 文件读写；`database.py` 统一管事务与 WAL。
4. `services/stage_steps.py` —— 五个纯函数，是「一首歌怎么被写出来」的核心。
5. `services/workflow_service.py` —— 串起五阶段、写进度、断点续跑。
6. `services/song_service.py` —— 前端字段的唯一映射点（`musical_key → key` 等）。
7. `routers/` + `main.py` —— 对外接口与应用生命周期。

常见改动的落点：

| 要改什么 | 依次改哪里 |
| --- | --- |
| 新增数据库字段 | `models/db_schema.py`（DDL + `SONG_COLUMNS`）→ `entities.py` → `schemas.py` → `song_service.to_response()` |
| 调整提示词变量 | `stage_steps.py` 里的 `get_stage(...)` 参数 + `prompts/<阶段>/user.md`（模板由业务侧维护） |
| 新增生成阶段 | `constants.py` 注册阶段名与产物名 → `stage_steps.py` 加阶段函数 → `workflow_service.run()` 串入流程 |
| 新增接口 | `models/schemas.py` 定义模型 → `routers/` 加端点 → `docs/api.md` 同步 |
| 调整轮询 / 播放行为 | 前端 `pages/StudioPage.vue`、`services/api.ts`、`common/config.ts` |

**红线**：`backend/app/prompts/` 下的 8 个 md 是业务资产，代码只读取、不修改；改模板变量前先与提示词负责人确认。密钥只放被忽略的 `.env`，不要写回代码或提交。

## 安装与启动

```bash
# 1. 虚拟环境（Python 3.10+）
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -e backend       # Windows
# backend/.venv/bin/pip install -e backend                       # macOS / Linux

# 2. 配置
cp backend/app/.env.example backend/app/.env    # 填入 LLM / ModelScope 密钥

# 3. 启动（在仓库根目录执行）
backend/.venv/Scripts/uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8788
```

交互式文档：`http://localhost:8788/docs`；健康检查：`/health`。
Windows 也可用 `scripts/start.bat` 一次启动前后端。

## 离线演示

不想消耗 API 额度时设置（或写在 `.env`）：

```bash
MUSIC_STUDIO_MOCK=true
```

此时 LLM 返回满足阶段校验的桩 JSON，音乐生成本地合成 8 秒 WAV，全流程约 1 秒跑完，前端播放/进度条完全可用。

## 测试

```bash
backend/.venv/Scripts/python.exe backend/tests/smoke_test.py
```

冒烟测试用临时数据库 + mock 模式，覆盖建表、三个 LLM 任务、创建作品、五阶段推进、音频下载、级联删除，并校验响应字段覆盖前端 `GenerationResponse`。

## 数据

- 数据库：`<仓库根>/data/music_studio.db`（WAL，`/data/` 已忽略）
- 阶段产物：`<仓库根>/data/outputs/<作品ID>/`（`stage1.json`、`stage2_polished.json`、`stage3.json`、`music/final_song.*`）
- 阶段产物存在即跳过重跑；音乐生成另有参数指纹 + `task_id` 落盘，中断后可续跑或只补下载。

## 接口与配置

见 [docs/api.md](../docs/api.md)、[安装指南](../docs/installation.md)。
