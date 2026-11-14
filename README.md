# Music Studio · 音乐创作台

> 项目名称：**Music Studio**（原名 ACE-Step Studio）。ACE-Step 1.5 仅作为底层生成引擎依赖保留，不再是项目名。

本地优先的 AI 音乐创作工作台。后端基于 FastAPI 编排 ACE-Step 1.5 的生成任务，前端是一个中文 Vue 3 单页应用，提供「描述想法 → 生成歌曲 → 作品库 → 播放」的完整流程，数据（SQLite + 音频/封面文件）都留在本机。

## 当前状态

- 前端全中文，UI 使用 **Vue 3 单文件组件（`.vue`）+ TDesign Vue Next + TailwindCSS**，界面精简为「简单创作 / 作品库 / 歌曲详情 / 播放栏」四块。
- 播放器借鉴网易云音乐：黑胶唱片随播放旋转、红色播放按钮、进度/音量滑杆、上一首/下一首；可通过「收起播放器」隐藏，隐藏后右下角出现迷你播放器悬浮恢复，显隐状态存 localStorage。
- 歌词 / 歌曲描述输入框限制 **最多 500 字**，实时显示字数与接近上限提示，`maxlength` + 提交/扩写时双重截断，防止粘贴或程序赋值绕过。
- 文件布局参考 `vue_tuiwen`：`common/ interface/ services/ pages/ components/` 分层，`@` 路径别名，`.env.*` 环境变量，三份 tsconfig。
- 请求层统一走 **axios 封装**（`src/services/index.ts`），带请求/响应拦截器与错误归一化；后端离线时自动降级到演示数据。
- **GitHub Pages 部署自动化**：push 到 `main` 即自动构建并发布前端静态站。
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
npm run preview        # http://localhost:5175，同样代理 /api 到 8788
```

启动后端（需要 Python 3.11/3.12、ACE-Step 仓库与模型权重）：

```bash
./scripts/install_windows.ps1     # Windows，一次性安装
./scripts/start.bat               # Windows，启动
./scripts/install_mac.sh          # macOS，一次性安装
./scripts/start.sh                # macOS，启动
```

完整步骤见 [安装指南](./docs/installation.md)，接口说明见 [后端接口清单](./docs/api.md)。

## GitHub Pages 部署

工作流：[`.github/workflows/deploy-pages.yml`](./.github/workflows/deploy-pages.yml)。

**一次性设置**：仓库 `Settings → Pages → Build and deployment → Source` 选择 **GitHub Actions**。不需要配置任何 token 或 secret（使用官方 Pages Actions 的 OIDC 权限）。

**触发方式**：

- push 到 `main` 且 `frontend/**` 或工作流文件发生变化；
- 或在 Actions 页面手动运行 `Deploy Frontend to GitHub Pages`。

流程：`npm ci` → `npm run build`（含类型检查）→ 校验产物与 base 路径 → 生成 `404.html` / `.nojekyll` → 上传并部署。发布地址为 `https://<owner>.github.io/<repo>/`。

**base 路径**：项目页必须带仓库名前缀，CI 会注入 `VITE_BASE_PATH=/${{ github.event.repository.name }}/` 覆盖（环境变量优先于 `.env.production`）。本地构建默认 `/`，保证 `npm run preview` 可直接查看；fork 仓库无需改配置。若绑定自定义域名，把 `VITE_BASE_PATH` 设为 `/` 即可。

> Pages 是纯静态托管，没有 FastAPI 后端，因此线上页面会**自动进入演示模式**（用本地示例数据展示完整生成流程）。若要连真实后端，把 `.env.production` 的 `VITE_APP_API_BASE_URL` 改成完整地址（如 `https://api.example.com/api`），并确保后端开启 CORS。

## 前端结构

```text
frontend/
├── .env.development        # 开发环境变量
├── .env.production         # 生产环境变量（含 Pages base）
├── index.html
├── vite.config.ts          # 端口、/api 代理、@ 别名、Pages base
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
    ├── style.css           # 主题变量与少量全局样式
    ├── assets/             # （预留）组件内使用的静态资源
    ├── common/
    │   ├── config.ts       # 运行期配置（环境变量集中读取）
    │   ├── constants.ts    # 提示词模板、阶段文案、描述字数上限（500）
    │   └── utils.ts        # 中文文案、格式化、封面配色与图标
    ├── interface/
    │   └── index.ts        # 与后端对齐的数据结构
    ├── services/
    │   ├── index.ts        # axios 实例 + 请求/响应拦截器
    │   ├── api.ts          # 接口地址集中管理 + 请求函数 + 演示兜底
    │   └── demo.ts         # 后端离线时的本地示例数据
    ├── pages/
    │   └── StudioPage.vue  # 主页面：状态、生成流程、播放控制
    └── components/
        ├── CreatePanel.vue     # 左：简单创作
        ├── LibraryPanel.vue    # 中：作品库
        ├── SongDetailPanel.vue # 右：歌曲详情
        ├── PlaybackBar.vue     # 底：网易云风格播放栏（可隐藏）
        └── MiniPlayer.vue      # 右下角迷你播放器，恢复播放栏
```

技术栈：Vue 3.5（`<script setup>` 组合式 API）+ TypeScript + Vite 5 + TDesign Vue Next（组件）+ tdesign-icons-vue-next（图标）+ TailwindCSS（布局）+ **axios**（请求）。
保持轻量：单页应用只用一个 `pages/`，未引入 vue-router / pinia；组件间用 props / emits 通信，作品列表用 2 秒定时轮询刷新。

## 请求层

| 文件 | 职责 |
| --- | --- |
| `services/index.ts` | 创建 axios 实例（baseURL / 超时 / JSON 头），请求拦截统一注入鉴权头，响应拦截直接返回 `response.data` 并归一化错误；导出 `get` / `post` / `del` 与 `isConnectionError` |
| `services/api.ts` | `apiUrls` 集中管理路径，业务接口函数；启动时探测 `GET /api/history`，返回数组才判定后端在线，否则静默降级到 `demo.ts` |
| `services/demo.ts` | 内存示例数据与生成流程模拟 |

所有业务请求传 `silent: true`，避免后端离线（本地预览 / Pages）时全局错误提示不断弹出；业务错误（4xx）仍会原样抛给调用方。

## 环境变量

| 变量 | 开发默认 | 说明 |
| --- | --- | --- |
| `VITE_APP_API_BASE_URL` | `/api` | 后端接口前缀，开发由 Vite 代理到 8788 |
| `VITE_APP_IS_DEBUG` | `True` / `False` | 是否输出调试日志 |
| `VITE_BASE_PATH` | `/` | Vite `base`，GitHub Pages 项目页需 `/<仓库名>/` |
| `VITE_APP_BUILD` | — | CI 注入的构建号 |

## 仓库结构

```text
.
├── backend/            # FastAPI：生成编排、配置、LM 服务、SQLite
├── frontend/           # 中文单页前端（Vue 3）
├── docs/               # 安装指南、接口清单、截图
├── scripts/            # 各平台安装与启动脚本
├── .github/workflows/  # GitHub Pages 部署工作流
├── data/               # 运行时数据（SQLite、音频、封面，已忽略）
└── README.md
```

## 配置项（后端）

| 变量 | 用途 |
| --- | --- |
| `ACE_STEP_HOST` | 绑定地址（可设为 Tailscale IP 限制访问） |
| `ACE_STEP_PORT` / `ACE_STEP_UI_PORT` | 后端 / 前端端口 |
| `ACE_STEP_ACE_REPO_PATH` / `ACE_STEP_CHECKPOINTS_PATH` | ACE 仓库与权重位置 |
| `ACE_STEP_OPENAI_ENABLED` + `ACE_STEP_OPENAI_ENDPOINT` | OpenAI 兼容端点，用于描述扩写、写词、起标题 |

## 仓库

本仓库已重新初始化，历史从 `chore: 初始化 Music Studio` 开始，**没有 origin 远程**。推送到自己的远端前先执行：

```bash
git remote add origin <你的仓库地址>
git push -u origin main
```

GitHub Pages 的 base 路径由 CI 按 `github.event.repository.name` 自动注入，因此远端仓库改名为 `music-studio` 等任意名称都无需改配置。

## 开发备注

- `.gitignore` 中的 `models/`、`data/` 已收窄为「仅仓库根目录」，避免误伤源码目录 `backend/app/models/` 与 `frontend/src/` 下的文件；另外忽略 `*.egg-info/` 生成物与 `.env.local` 私有变量。
- `backend/app/models/`（SQLAlchemy `Generation` ORM）曾因上述忽略规则未被提交，已按 `egg-info/SOURCES.txt` 记录的结构补回，否则后端启动会 ImportError。
- 类型检查用 `vue-tsc --noEmit -p tsconfig.app.json`，不会生成 `vite.config.js` / `*.tsbuildinfo` 等产物污染工作树（Vite 会优先读取 `.js` 配置，曾与 `.ts` 配置冲突）。
- `.env.development` / `.env.production` 是可提交的模板配置（不含密钥）；本地私密变量请放 `.env.local`（已被忽略）。

## 文档

- [安装指南](./docs/installation.md)
- [后端接口清单](./docs/api.md) — 已实现 17 个、前端当前调用 6 个、建议补充 7 个
