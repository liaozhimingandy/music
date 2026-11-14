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
