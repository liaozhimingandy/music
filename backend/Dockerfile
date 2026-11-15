# Music Studio 后端镜像（FastAPI + SQLite + 内置任务队列）
#
# 构建（在仓库根执行）：
#   docker build -t music-studio-backend:latest ./backend
# 运行：
#   docker run -d -p 8000:8000 -v music-data:/app/data \
#     -e LLM_API_KEY=xxx -e MUSIC_API_TOKEN=xxx music-studio-backend:latest
#
# 写法对齐 FastAPI 官方容器化文档（https://fastapi.tiangolo.com/zh/deployment/docker/）：
#   FROM python:<版本> / WORKDIR / COPY 依赖 → RUN 装依赖 / COPY 代码 / CMD ["fastapi", "run", ...]
#   * 依赖单独一层，代码放最后 —— 改业务代码时依赖层命中缓存，不用重装。
#   * CMD 必须用 **exec 形式**（JSON 数组），这样 uvicorn 拿到 SIGTERM 能走
#     lifespan 的优雅关闭（停巡检、cancel 任务、关数据库）；官方文档明确
#     警告过不要用 shell 形式。
#
# 五个容易踩的坑，改这个文件前先读：
# 1. 只能单进程。任务队列的 worker 跑在应用 lifespan 里（services/runtime.py），
#    靠内存里的一张 _running 表去重；开 --workers 2 会让两个进程抢同一批
#    queued 任务、重复调 LLM 烧额度。要横向扩容得先把「认领任务」做成数据库
#    原子操作，届时再改 CMD。
# 2. 数据必须落在 /app/data（SQLite 库 + outputs 音频 + 阶段 JSON）。不挂卷
#    的话容器一重建作品全丢，且反代拉音频会 404。
# 3. 配置全部走环境变量（优先级高于 .env 文件）。镜像里刻意不带 .env，
#    避免把本地密钥烤进镜像；需要 .env 的部署方式见 docs/deployment.md。
# 4. 依赖用 uv 装（pyproject + uv.lock），不再有 requirements.txt。锁文件
#    必须一起提交：`uv sync --frozen` 发现它和 pyproject 不一致会直接失败，
#    这是有意为之——镜像里装的版本必须和 CI / 本地完全一致。
# 5. 容器内监听端口固定 8000（FastAPI 官方默认），不随环境变量变化：
#    改端口会让反代 / 健康检查 / 端口映射三处全部要跟着改，容易漏。
#    本机原生启动（不走容器）的端口由 .env 的 HOST / PORT 决定。

FROM python:3.14.6-slim

# uv 官方镜像只借二进制，最终运行镜像仍是上面的 python 基础镜像。
# 升级 uv：改这里的版本号 → 重新 `uv lock` → 一起提交。
COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /uvx /bin/

# PYTHONDONTWRITEBYTECODE：容器里不生成 __pycache__，镜像层更干净
# PYTHONUNBUFFERED：日志直接进 docker logs，不做行缓冲
# UV_COMPILE_BYTECODE：安装时预编译 pyc，冷启动快一点
# UV_LINK_MODE=copy：不跨层硬链接（overlayfs 上硬链会失效）
# UV_PROJECT_ENVIRONMENT：把虚拟环境放到 /opt/venv，代码目录保持干净
# UV_PYTHON_DOWNLOADS=never：禁止 uv 另下解释器，只用基础镜像自带的版本
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    MUSIC_STUDIO_DATA_DIR=/app/data \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never

# curl 供 compose 的 healthcheck 探针使用（slim 镜像默认没有）；
# tzdata 让日志时间与国内一致。
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依赖单独一层：只要 pyproject.toml / uv.lock 没变，改业务代码就能命中缓存。
# --no-dev 跳过 [dependency-groups] 里的 pytest / ruff。
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 只拷 app 包（tests / .venv / .env 由 .dockerignore 挡在构建上下文外）。
# prompts/ 在 app 目录里，跟着一起进镜像（提示词是运行必需）。
COPY app ./app

# 企业 CA 根证书（AlsoApp DigiCert Root CA）：后端出方向访问 llm.alsoapp.com
# 时用它验证网关的自签证书（LLM_CA_CERT=root.crt）。
# 放在 backend/ 而非 app/ 里 —— 它是后端的运行时配置，不属于 app 包。
# 容器内落到 /app/root.crt，正好命中 config.ca_cert_candidates 的
# BACKEND_DIR 候选（app 目录 → backend 目录 → 仓库根 → 进程 CWD）。
COPY root.crt ./root.crt

# 非 root 运行：先建用户，再把数据目录给它（命名卷首次创建时会继承该属主）。
# 若改用 bind mount（./data:/app/data），宿主机目录必须先
# chown -R 10001:10001 ./data，否则容器内写库会 permission denied。
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app /opt/venv
USER appuser

# uv 装的依赖在 /opt/venv，把它的 bin 放进 PATH，CMD 里就能直接写 fastapi。
ENV PATH="/opt/venv/bin:$PATH"

EXPOSE 8000

# FastAPI 官方推荐的容器启动写法（exec 形式，fastapi run 即 PID 1，
# docker stop 的 SIGTERM 能直达应用，从而走 lifespan 的优雅关闭）。
#
# `fastapi run app/main.py` 会自动识别 app/ 是包、把路径翻译成
# `app.main:app`（实测任意 cwd + 绝对路径均正确），所以不用自己拼
# import string，也不需要额外的 --app-dir。
#
# --proxy-headers：官方文档明确要求「跑在 Nginx / Traefik 这类 TLS 终止
# 代理后面时必须加」，否则后端看到的 scheme 永远是 http，生成的重定向
# / 绝对 URL 会指向 http://。
# --forwarded-allow-ips：白名单里放 Docker 网段，Traefik 的容器 IP 才会
# 被认作可信代理（uvicorn 默认只信 127.0.0.1，代理在 docker 网络里就
# 会失效）。用 172.16.0.0/12 覆盖默认的 172.17~172.31 网段。
# 端口固定 8000；要改端口请同时改 compose 的端口映射与 healthcheck。
CMD ["fastapi", "run", "app/main.py", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1,::1,172.16.0.0/12"]
