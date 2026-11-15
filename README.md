# Music Studio · 音乐创作台

> 项目名称：**Music Studio**（原名 ACE-Step Studio）。ACE-Step 1.5 仅作为底层生成引擎依赖保留，不再是项目名。

本地优先的 AI 音乐创作工作台。后端基于 FastAPI + SQLite3，按 `service → dal → model` 分层编排五阶段 MusicFlow（策划 → 歌词 → 润色 → 音乐制作方案 → 音频生成），调用 OpenAI 兼容 LLM 与 ModelScope ACE-Step 1.5 云端推理；前端是中文 Vue 3 单页应用，提供「描述想法 → 生成歌曲 → 作品库 → 播放」的完整流程，数据（SQLite + 音频文件）都留在本机。

## 当前状态

- 前端全中文，UI 使用 **Vue 3 单文件组件（`.vue`）+ TDesign Vue Next + TailwindCSS**，界面精简为「简单创作 / 作品库 / 歌曲详情 / 播放栏」四块。
- 播放器借鉴网易云音乐：黑胶唱片随播放旋转、红色播放按钮、进度/音量滑杆、上一首/下一首；可通过「收起播放器」隐藏，隐藏后右下角出现迷你播放器悬浮恢复，显隐状态存 localStorage。播放条左侧会实时显示**正在唱的那句歌词**。
- **歌词同步高亮**（重点功能）：详情页右侧歌词固定展示、播放时自动滚动居中，当前行红色加粗、相邻行渐隐，点击任意一句可跳转到该句。后端只产出纯文本歌词，因此时间轴由 `common/lyric.ts` 按字重估算分配（支持 LRC 时间戳与 `metadata.lrc`，有真时间戳时自动切换精确模式）；估算偏差可用「提前 / 延后」±0.3s 校准，按作品存 localStorage。
- 生成过程有**过渡动效**：作品库卡片流光描边 + 封面频谱条 + 阶段进度条（`current_stage` 驱动，宽度平滑过渡）、提交按钮扫光、首次加载骨架屏、新作品入场淡入、出歌时高亮一次；全部尊重系统「减少动态效果」设置。
- 创作区支持**回车直接生成**（`Shift + Enter` 换行，输入法组合态不误触发）。
- 歌词 / 歌曲描述输入框限制 **最多 500 字**，实时显示字数与接近上限提示，`maxlength` + 提交/扩写时双重截断，防止粘贴或程序赋值绕过。
- 前端**不提供删除入口**：作品由服务器定时清理，作品库与详情页会提示「请及时下载保存」。
- 作品详情的 `stages` 带**阶段遥测**（每阶段使用的模型、token 用量、耗时），排查生成问题与核算成本不用再翻日志。
- 文件布局参考 `vue_tuiwen`：`common/ interface/ services/ pages/ components/` 分层，`@` 路径别名，`.env.*` 环境变量，三份 tsconfig。
- 请求层统一走 **axios 封装**（`src/services/index.ts`），带请求/响应拦截器与错误归一化；只对接真实后端，未连接时页面状态栏提示「后端未连接」。
- **GitHub Pages 部署自动化**：push 到 `main` 即自动构建并发布前端静态站。
- 后端按 **routers → service → dal → model** 分层，**全链路异步**（asyncio + aiosqlite + httpx.AsyncClient）：`POST /v1/generations` 立即返回 `queued`，后台 `asyncio.Task` 跑五阶段并逐阶段写 SQLite，前端 2 秒轮询 `/v1/history` 看进度；服务通过 FastAPI `Depends` 注入，作品 ID 用时间有序的 **UUIDv7**。
- 后端接口统一 **`/v1` 版本前缀**（含 `/v1/health`）：前缀常量只在 `backend/app/constants.py` 定义，`routers/__init__.py` 聚合注册；前端 `API_BASE_URL`、Vite 代理、`audio_url` 全部同步使用 `/v1`，旧 `/api/*` 已下线。
- 生成接口内置**幂等去重**（内容指纹 / `Idempotency-Key`，重复提交直接返回已有作品）、**每日生成配额**（默认 50 首/天，`MUSIC_STUDIO_DAILY_LIMIT` 可配，0 表示不限，用完返回 429 + 友好提示）与**接口防护**（滑动窗口限流、活跃任务上限、请求体大小、字段长度与路径 ID 白名单），防双击与恶意刷接口。
- 默认端口：后端 `8788`，前端 `5175`（刻意避开 8000）。

## 快速开始

只看界面（无需 Python、无需模型权重）：

```bash
cd frontend
npm install
npm run dev            # http://localhost:5175
```

构建与本地预览：

```bash
npm run type-check     # vue-tsc 类型检查
npm run build          # 类型检查 + vite build
npm run preview        # http://localhost:5175，同样代理 /v1 到 8788
```

启动后端（Python 3.10+，**不需要本地模型权重**，只需 `.env` 里的云端 API 密钥）。依赖用 [uv](https://docs.astral.sh/uv/) 管理：

```bash
# 一次性安装（没有 uv：见 https://docs.astral.sh/uv/getting-started/installation/）
uv sync --project backend                                    # 建 backend/.venv，按 uv.lock 安装
cp backend/app/.env.example backend/app/.env                 # 填入 LLM / ModelScope 密钥

# 启动（仓库根目录）
uv run --project backend uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8788
```

依赖版本由 `backend/uv.lock` 锁定，`uv sync` 按锁文件安装；新增依赖用 `uv add --project backend <包名>`（会一并更新锁文件，别手改）。生产镜像里是 `uv sync --frozen --no-dev`，与本地完全一致。

只想看效果、不消耗 API 额度：设置 `MUSIC_STUDIO_MOCK=true` 启动，全流程离线跑通并生成可播放的本地 WAV。也可用 `scripts/start.bat` / `scripts/start.sh` 一次启动前后端。

离线端到端验证：`uv run --project backend python backend/tests/smoke_test.py`。

完整步骤见 [安装指南](./docs/installation.md)，接口说明见 [后端接口清单](./docs/api.md)。

## 生成链路与模型配置

- 点「开始生成」**只发一次** `POST /v1/generations`，请求体只带用户原始描述；后端五阶段自动完成策划 / 写词 / 润色 / 制作方案 / 音频，前端轮询 `GET /v1/history` 看进度。
- `POST /v1/llm` 仅服务「AI 扩写描述」等用户主动点击的辅助按钮，不在生成主链路里（后端流程已经包含 LLM 步骤，前端重复调用只会白耗配额）。
- 音乐模型 ID 由后端 `MUSIC_MODEL`（`backend/app/config.py`）唯一决定，默认 `ACE-Step/acestep-v15-xl-sft`；可用 `GET /v1/health` 的 `music_model` 查询。前端不维护模型枚举，提交时也不传 `model_variant`。
- 描述为空 / 全空格时生成按钮禁用，服务端另以 `422` 兜底。
- **每日生成配额**：默认每天最多新建 50 首（`MUSIC_STUDIO_DAILY_LIMIT`，`0` = 不限，日界按 `MUSIC_STUDIO_DAILY_RESET_UTC_OFFSET` 默认东八区）。用完时创建接口返回 429 + 中文提示（含重置时间），前端创作面板会提前显示「今日还剩 N 首」并在额度为 0 时禁用按钮；幂等重试不消耗配额。
- **后台队列是持久化的**：任务状态存 SQLite，后端重启 / `--reload` 后会自动扫描 `queued` / `processing` 的作品重新入队，已落盘的阶段产物直接跳过重跑；只有接口限流（429/413/422）、幂等去重或真实异常（终态 `failed`）才不会继续。默认巡检 3 秒、同一任务最多连续恢复 5 次，详见 [接口清单](./docs/api.md) 第二章。

## GitHub Pages 部署

工作流：[`.github/workflows/deploy-github-pages.yml`](./.github/workflows/deploy-github-pages.yml)。

**一次性设置**：仓库 `Settings → Pages → Build and deployment → Source` 选择 **GitHub Actions**，并在 `Custom domain` 填自定义域名（当前配置为 `music.openaim.cn`）。不需要配置任何 token 或 secret（使用官方 Pages Actions 的 OIDC 权限）。

**触发方式**：

- push 到 `main` 且 `frontend/**` 或工作流文件发生变化；
- 或在 Actions 页面手动运行 `Deploy Frontend to GitHub Pages`。

流程：`npm ci` → `npm run build`（含类型检查）→ 校验产物入口资源 → 生成 `404.html` / `.nojekyll` / `CNAME` → 上传并部署。

**base 路径（重要）**：工作流**不再**强制注入 `VITE_BASE_PATH`，默认沿用 `frontend/.env.production` 里的 `/`——绑定自定义域名（如 `https://music.openaim.cn`）时站点就在根路径，base 必须是 `/`，否则 `index.html` 会去 `/<repo>/assets/*.js` 取文件并 404（表现为「找不到 js 文件」）。只有部署成 GitHub Pages **项目页**（`https://<owner>.github.io/<repo>/`）时，才需要在仓库 `Settings → Secrets and variables → Actions → Variables` 加一个 `PAGES_BASE_PATH=/<repo>/`，工作流会自动透传。

**自定义域名**：工作流顶部的 `PAGES_CUSTOM_DOMAIN` 会把域名写进产物根的 `CNAME`，避免每次 Actions 部署把 Settings 里的域名冲掉；换域名时改这一处即可（或留空不写 CNAME）。

> Pages 是纯静态托管，没有 FastAPI 后端，线上页面无法生成作品（页面会提示「后端未连接」）。若要连真实后端，把 `.env.production` 的 `VITE_APP_API_BASE_URL` 改成完整地址（如 `https://api.example.com/v1`），并确保后端开启 CORS；同源反代时填 `/v1` 即可。

## Docker 部署（自建服务器）

前后端各一个镜像：`backend`（FastAPI，数据落在 `/app/data` 卷）、`frontend`（nginx 托管 SPA 并把 `/v1` 反代给后端，**同源所以无需配 CORS**）。

**本地一条命令试跑**：

```bash
cp deploy/.env.example .env          # 填 LLM_API_KEY / MUSIC_API_TOKEN
docker compose up -d --build         # http://localhost:8088
MUSIC_STUDIO_MOCK=true docker compose up -d --build   # 没密钥先看界面
```

**服务器部署**（只拉镜像，不在服务器编译）：

```bash
cp deploy/docker-compose.yml deploy/.env.example /opt/music-studio/
cd /opt/music-studio && mv .env.example .env && vim .env   # 改镜像地址与密钥
docker compose pull && docker compose up -d
```

镜像由 [`.github/workflows/build-images.yml`](./.github/workflows/build-images.yml) 在 push `main` / 推 `v*` 标签时构建并推送 **Docker Hub + 阿里云 ACR** 双仓库，推送完成后会用 `MUSIC_STUDIO_MOCK=true` 真跑一遍后端镜像、探测 `/v1/health`，避免把「能构建但不能启动」的镜像发出去。

可选 Secrets（**配了才推，不配自动跳过该仓库**）：Docker Hub 用 `DOCKERHUB_USERNAME` + `DOCKERHUB_PASSWORD`（用户名 + 密码登录；账号开启两步验证时需填访问令牌）；阿里云用 Variable `ALIYUN_REGISTRY` / `ALIYUN_NAMESPACE` + Secret `ALIYUN_USERNAME` / `ALIYUN_PASSWORD`。两个仓库都没配则跳过镜像推送。ARM 服务器加一个 Variable `DOCKER_PLATFORMS=linux/amd64,linux/arm64`。

完整步骤（镜像仓库配置、HTTPS、升级回滚、备份恢复、排障）见 [Docker 部署指南](./docs/deployment.md)。

> 后端**只能单副本**：任务队列的 worker 跑在进程内并对同一批 `queued` 任务去重，多副本会重复消费、重复扣 LLM 额度。这台机器请用 `restart: unless-stopped` 保证重启自动拉起，而不是横向扩容。

## 前端结构

```text
frontend/
├── .env.development        # 开发环境变量
├── .env.production         # 生产环境变量（含 Pages base）
├── index.html
├── vite.config.ts          # 端口、/v1 代理、@ 别名、Pages base
├── tailwind.config.js      # 设计 token，关闭 preflight 以兼容 TDesign
├── postcss.config.js
├── tsconfig.json           # 引用 app / node 两份配置
├── tsconfig.app.json       # src 类型检查（vue-tsc）
├── tsconfig.node.json      # vite.config.ts 类型检查
├── public/
│   └── favicon.svg
└── src/
    ├── main.ts             # 入口：createApp + TDesign 样式
    ├── App.vue             # 应用壳，渲染 StudioPage
    ├── env.d.ts            # Vite 环境变量类型
    ├── style.css           # 主题变量、黑胶/歌词/生成动效/骨架屏动画
    ├── assets/             # （预留）组件内使用的静态资源
    ├── common/
    │   ├── config.ts       # 运行期配置（环境变量集中读取）
    │   ├── constants.ts    # 阶段顺序与中文名、描述字数上限（500）
    │   ├── lyric.ts        # 歌词解析与时间轴（LRC / 按字重估算）
    │   └── utils.ts        # 中文文案、格式化、封面配色与图标
    ├── interface/
    │   └── index.ts        # 与后端对齐的数据结构
    ├── services/
    │   ├── index.ts        # axios 实例 + 请求/响应拦截器
    │   └── api.ts          # 接口地址集中管理 + 请求函数
    ├── pages/
    │   └── StudioPage.vue  # 主页面：状态、生成流程、播放控制、歌词时间轴
    └── components/
        ├── CreatePanel.vue     # 左：简单创作（Enter 提交）
        ├── LibraryPanel.vue    # 中：作品库（生成态动效 / 骨架屏）
        ├── SongDetailPanel.vue # 右：歌曲详情 + 同步歌词
        ├── LyricScroller.vue   # 同步滚动歌词（当前行居中高亮）
        ├── PlaybackBar.vue     # 底：网易云风格播放栏（可隐藏，含当前歌词）
        └── MiniPlayer.vue      # 右下角迷你播放器，恢复播放栏
```

技术栈：Vue 3.5（`<script setup>` 组合式 API）+ TypeScript + Vite 5 + TDesign Vue Next（组件）+ tdesign-icons-vue-next（图标）+ TailwindCSS（布局）+ **axios**（请求）。
保持轻量：单页应用只用一个 `pages/`，未引入 vue-router / pinia；组件间用 props / emits 通信，作品列表用 2 秒定时轮询刷新。

## 请求层

| 文件 | 职责 |
| --- | --- |
| `services/index.ts` | 创建 axios 实例（baseURL / 超时 / JSON 头），请求拦截统一注入鉴权头，响应拦截直接返回 `response.data` 并归一化错误；导出 `get` / `post` / `del` 与 `isConnectionError` |
| `services/api.ts` | `apiUrls` 集中管理路径，业务接口函数（历史 / 创建 / LLM / 音频地址解析），直连真实后端 |

作品列表轮询请求传 `silent: true`，避免后端短暂掉线时全局错误提示刷屏（连接状态由页面状态栏展示）；其余请求正常弹出全局提示，业务错误（4xx）原样抛给调用方。

## 后端结构

```text
backend/app/
├── main.py             # FastAPI 入口：async lifespan 建表/清理、CORS、路由
├── config.py           # 配置（密钥只在被忽略的 .env）
├── constants.py        # 阶段名、产物名、状态码
├── ids.py              # UUIDv7 生成器（时间有序、同毫秒单调递增）
├── deps.py             # FastAPI 依赖层：限流 / 请求体 / 客户端标识 / 路径校验
├── routers/            # 接口层：async 端点 + Depends 注入服务
├── services/           # 业务层（全部 async）
│   ├── runtime.py          # 单例装配 + asyncio.Task / Semaphore 调度
│   ├── song_service.py     # 作品 CRUD、响应映射、幂等去重、并发闸门
│   ├── llm_service.py      # 描述扩写 / 写词 / 起标题
│   ├── workflow_service.py # 五阶段编排、进度落库、断点续跑
│   ├── stage_steps.py      # 五个可复用阶段函数
│   ├── prompt_manager.py   # Jinja2 渲染 prompts/（只读）
│   ├── llm_client.py       # httpx.AsyncClient OpenAI 兼容客户端 + 离线 mock
│   ├── music_provider.py   # ModelScope 提交 / 轮询 / 下载 + 离线 mock
│   ├── rate_limiter.py     # 进程内滑动窗口限流
│   └── cover_theme.py      # 封面配色
├── dal/                # 数据访问层：aiosqlite 长连接 + 产物文件（async）
│   └── database.py / song_dal.py / stage_dal.py / artifact_dal.py
├── models/             # 模型层：表结构 / 实体 / API Schema
│   └── db_schema.py / entities.py / schemas.py
└── prompts/            # 四阶段提示词（业务资产，代码只读取不修改）
```

数据库 `data/music_studio.db`：`songs`（按前端 `GenerationResponse` 字段建模，另含 `input_fingerprint` / `client_key` 做幂等与限流）+ `song_stages`（五阶段进度 + 阶段遥测：每阶段使用的模型、token 用量与耗时，便于排查问题），外键级联删除。旧库启动时自动补列，历史数据不丢。详见 [后端接口清单](./docs/api.md)。

## 环境变量

| 变量 | 开发默认 | 说明 |
| --- | --- | --- |
| `VITE_APP_API_BASE_URL` | `/v1` | 后端 v1 接口前缀。走 Vite 同源代理填 `/v1`；直连后端填完整地址 + `/v1`（如 `http://localhost:8000/v1`），由后端 CORS 放行 |
| `VITE_API_PROXY_TARGET` | `http://127.0.0.1:8788` | Vite dev / preview 代理的后端地址；自己换端口启动后端时同步改这里 |
| `CORS_ORIGINS` | 空 | 后端额外允许的跨域来源，逗号分隔或 JSON 数组；默认已放行全部本机回环来源 |
| `VITE_APP_IS_DEBUG` | `True` / `False` | 是否输出调试日志 |
| `VITE_BASE_PATH` | `/` | Vite `base`；自定义域名保持 `/`，仅 GitHub Pages **项目页**需要 `/<仓库名>/`（或用仓库变量 `PAGES_BASE_PATH` 覆盖） |
| `VITE_APP_BUILD` | — | CI 注入的构建号 |
| `DEBUG` | `false` | 后端日志级别：`true` 记 DEBUG（LLM 请求参数、模型返回摘要等），`false` 记 INFO（每阶段的模型 / token / 耗时）。日志写 `data/logs/music-studio-YYYY-MM-DD.log`，按天轮转保留 7 天 |
| `MUSIC_STUDIO_RESUME` | `true` | 断点续跑：阶段产物存在就跳过。换了 LLM 模型想重跑设为 `false` |

> **跨域排错**：直连后端（如 `5177 → 8000`）是不同源。后端默认放行 `localhost` / `127.0.0.1` / `[::1]` 的**任意端口**，且 500 等错误响应也带 CORS 头。若浏览器仍报 `No 'Access-Control-Allow-Origin'`，先用 `curl -i -H "Origin: http://127.0.0.1:5177" http://localhost:8000/v1/health` 确认响应头；如果实际是纯文本 500，说明是后端异常被浏览器误报成跨域，去看后端日志。生产环境请在 `.env` 里用 `CORS_ORIGINS` 显式列出前端域名。

## 仓库结构

```text
.
├── backend/            # FastAPI：service→dal→model 三层 + SQLite3
│   ├── Dockerfile      # 后端镜像（python:3.12-slim + uv sync --frozen，非 root，数据卷 /app/data）
│   ├── pyproject.toml  # 依赖声明（uv 管理）
│   └── uv.lock         # 锁定的依赖版本，镜像构建用 --frozen 保证一致
├── frontend/           # 中文单页前端（Vue 3）
│   ├── Dockerfile      # 前端镜像（node 构建 -> nginx 托管）
│   ├── nginx.conf      # SPA fallback + /v1 反代 + 静态缓存
│   └── music-studio-security.conf
├── deploy/             # 生产部署：拉镜像的 compose + .env 模板
├── docker-compose.yml  # 本地/源码构建版编排
├── docs/               # 安装指南、接口清单、Docker 部署、截图
├── scripts/            # 各平台安装与启动脚本
├── .github/workflows/  # Pages 部署 + 镜像发布（Docker Hub / 阿里云 ACR）
├── data/               # 运行时数据（SQLite、音频、封面，已忽略）
└── README.md
```

## 配置项（后端）

配置集中在 `backend/app/config.py`，模板为 `backend/app/.env.example`；真实密钥放 `backend/app/.env`（已忽略）。

| 变量 | 用途 |
| --- | --- |
| `OPENAIM_API_KEY` | LLM Key，兼容 `OPENAI_API_KEY` / `LLM_API_KEY` |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_CA_CERT` | OpenAI 兼容端点、模型、可选自签 CA |
| `MUSIC_API_TOKEN` | ModelScope token（**只放 .env**） |
| `MUSIC_API_BASE_URL` / `MUSIC_MODEL` | 生成接口与模型 |
| `MUSIC_STUDIO_MOCK` | `true` 时全流程离线演示，不调用外部 API |
| `ACE_STEP_HOST` / `ACE_STEP_PORT` | 绑定地址 / 端口（启动脚本沿用） |
| `MUSIC_STUDIO_DATA_DIR` | 数据库与产物目录，默认 `<仓库根>/data` |
| `MUSIC_STUDIO_RATE_LIMIT_ENABLED` | 接口防护总开关，默认 `true` |
| `MUSIC_STUDIO_RATE_LIMIT_WINDOW` | 限流滑动窗口长度（秒），默认 `60` |
| `MUSIC_STUDIO_RATE_LIMIT_READ` / `_LLM` / `_CREATE` | 各接口窗口内请求上限，默认 `180` / `20` / `5` |
| `MUSIC_STUDIO_MAX_ACTIVE_JOBS` / `MUSIC_STUDIO_MAX_ACTIVE_PER_CLIENT` | 全局 / 单客户端活跃任务上限，默认 `4` / `2` |
| `MUSIC_STUDIO_DAILY_LIMIT` | 每日生成配额，默认 `50` 首/天，`0` 表示不限制；用完返回 429 + 中文提示 |
| `MUSIC_STUDIO_DAILY_RESET_UTC_OFFSET` | 配额日界时区偏移（小时），默认 `8`（北京时间 00:00 重置） |
| `MUSIC_STUDIO_DEDUPE_WINDOW` | 已完成作品的重复提交窗口（秒），默认 `30` |
| `MUSIC_STUDIO_MAX_REQUEST_BYTES` | 请求体上限（字节），默认 `65536` |
| `MUSIC_STUDIO_TRUST_PROXY` | 是否信任 `X-Forwarded-For`，默认 `false` |

完整默认值与注释见 `backend/app/.env.example`；生产环境请勿关闭防护总开关。

## 仓库

远端为 `git@github.com:liaozhimingandy/music.git`，`main` 为默认分支。

GitHub Pages 的 base 默认取 `.env.production` 的 `/`（自定义域名场景）；只有部署成项目页时才需要在仓库变量里设置 `PAGES_BASE_PATH=/<仓库名>/`。

## 开发备注

- `.gitignore` 中的 `models/`、`data/` 已收窄为「仅仓库根目录」，避免误伤源码目录 `backend/app/models/` 与 `frontend/src/` 下的文件；另外忽略 `.env`、`.venv/`、`*.egg-info/`、`backend/app/outputs/`、`.ipynb_checkpoints/` 等产物。
- 后端已从「本地 ACE-Step 引擎 + SQLAlchemy」整体替换为「云端 LLM + ModelScope + 原生 sqlite3」的 `service → dal → model` 三层；原型脚本 `music.py` 与旧引擎代码已删除，`prompts/` 内容保持原样只读。
- 后端已全量异步化：`asyncio.Task` + `Semaphore` 取代线程池，`aiosqlite` 单长连接 + 事务锁取代逐次开连接，`httpx.AsyncClient` 取代同步客户端；路由服务全部走 `Depends` 注入。作品 ID 改为 UUIDv7（时间有序，旧 UUIDv4 记录仍兼容），生成接口增加幂等去重与限流防护。详见 [后端说明](./backend/README.md) 的「异步与并发」章节。
- 前后端源码已补齐维护注释：每个模块开头有职责说明，关键类 / 方法带 docstring 或 JSDoc，状态机、断点续跑、字段映射等易踩坑处有专门说明。后端的推荐阅读顺序与常见改动落点见 [后端说明](./backend/README.md)。
- `backend/app/prompts/` 下的提示词模板是业务资产，本轮注释工作只读、未修改任何模板内容（8 个 md 文件校验值前后一致）。
- 类型检查用 `vue-tsc --noEmit -p tsconfig.app.json`，不会生成 `vite.config.js` / `*.tsbuildinfo` 等产物污染工作树（Vite 会优先读取 `.js` 配置，曾与 `.ts` 配置冲突）。
- `.env.development` / `.env.production` 是可提交的模板配置（不含密钥）；本地私密变量请放 `.env.local`（已被忽略）。

## 文档

- [安装指南](./docs/installation.md)
- [Docker 部署](./docs/deployment.md) — 镜像仓库配置、云服务器部署、HTTPS、升级回滚、备份与排障
- [后端接口清单](./docs/api.md) — 8 个接口、幂等去重与限流规则、UUIDv7、五阶段流程与 SQLite 表结构
- [后端说明](./backend/README.md) — 分层、运行、离线演示与冒烟测试
