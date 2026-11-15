# Music Studio 后端镜像（FastAPI + SQLite + 内置任务队列）
#
# 构建（在仓库根执行）：
#   docker build -t music-studio-backend:latest ./backend
# 运行：
#   docker run -d -p 8788:8788 -v music-data:/app/data \
#     -e LLM_API_KEY=xxx -e MUSIC_API_TOKEN=xxx music-studio-backend:latest
#
# 四个容易踩的坑，改这个文件前先读：
# 1. 只能单进程。任务队列的 worker 跑在应用 lifespan 里（services/runtime.py），
#    靠内存里的一张 _running 表去重；开 --workers 2 会让两个进程抢同一批
#    queued 任务、重复调 LLM 烧额度。要横向扩容得先把「认领任务」做成数据库
#    原子操作，届时再改 CMD。
# 2. 数据必须落在 /app/data（SQLite 库 + outputs 音频 + 阶段 JSON）。不挂卷
#    的话容器一重建作品全丢，且 nginx 的音频请求会 404。
# 3. 配置全部走环境变量（优先级高于 .env 文件）。镜像里刻意不带 .env，
#    避免把本地密钥烤进镜像；需要 .env 的部署方式见 docs/deployment.md。
# 4. 依赖用 uv 装（pyproject + uv.lock），不再有 requirements.txt。锁文件
#    必须一起提交：`uv sync --frozen` 发现它和 pyproject 不一致会直接失败，
#    这是有意为之——镜像里装的版本必须和 CI / 本地完全一致。

FROM python:3.12-slim

# uv 官方镜像只借二进制，最终运行镜像仍是 python:3.12-slim。
# 升级 uv：改这里的版本号 → 重新 `uv lock` → 一起提交。
COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /uvx /bin/

# PYTHONDONTWRITEBYTECODE：容器里不生成 __pycache__，镜像层更干净
# PYTHONUNBUFFERED：日志直接进 docker logs，不做行缓冲
# UV_COMPILE_BYTECODE：安装时预编译 pyc，冷启动快一点
# UV_LINK_MODE=copy：不跨层硬链接（overlayfs 上硬链会失效）
# UV_PROJECT_ENVIRONMENT：把虚拟环境放到 /opt/venv，代码目录保持干净
# UV_PYTHON_DOWNLOADS=never：禁止 uv 另下解释器，只用基础镜像自带的 3.12
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    MUSIC_STUDIO_DATA_DIR=/app/data \
    MUSIC_STUDIO_PORT=8788 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never

# curl 用于 HEALTHCHECK（slim 镜像默认没有）；tzdata 让日志时间与国内一致。
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依赖单独一层：只要 pyproject.toml / uv.lock 没变，改业务代码就能命中缓存。
# --no-dev 跳过 [dependency-groups] 里的 pytest / ruff。
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 只拷 app 包：tests / .venv / .env 由 .dockerignore 挡在构建上下文外。
# prompts/ 与 root.crt 都在 app 目录里，跟着一起进镜像（提示词是运行必需）。
COPY app ./app

# 非 root 运行：先建用户，再把数据目录给它（命名卷首次创建时会继承该属主）。
# 若改用 bind mount（./data:/app/data），宿主机目录必须先
# chown -R 10001:10001 ./data，否则容器内写库会 permission denied。
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app /opt/venv
USER appuser

# uv 装的依赖在 /opt/venv，把它的 bin 放进 PATH，CMD 里就能直接写 uvicorn。
ENV PATH="/opt/venv/bin:$PATH"

EXPOSE 8788

# 探针打 /v1/health：不挂限流、不碰 LLM，能真实反映「进程活着且库能打开」。
# start-period 给 20 秒，首次建表 + 补列留足时间。
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${MUSIC_STUDIO_PORT}/v1/health" || exit 1

# exec 让 uvicorn 接管 PID 1，docker stop 的 SIGTERM 能直接到达应用，
# 从而走 lifespan 的优雅关闭（停巡检、cancel 任务、关数据库）。
# 端口读环境变量，保持与 backend/app/config.py 的 MUSIC_STUDIO_PORT 一致。
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"${MUSIC_STUDIO_PORT:-8788}\" --no-server-header"]
