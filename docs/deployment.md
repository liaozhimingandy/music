# Docker 部署指南

面向「把 Music Studio 部署到一台云服务器」的场景。全程只需要 Docker，服务器上不装 Python / Node。

## 架构

```text
浏览器
  │  GitHub Pages / Cloudflare Pages（纯静态托管前端，可绑自定义域名）
  │  前端直接调用后端 /v1（不同源，依赖后端 CORS_ORIGINS 放行）
  ▼
backend 容器（fastapi run / uvicorn）  :8000
  ├── FastAPI /v1 接口
  ├── SQLite（data/music_studio.db）
  └── outputs/（生成的音频、阶段 JSON）
```

前端 **不打包成容器**：构建产物由 GitHub Actions 推到 GitHub Pages（或自行传到 Cloudflare Pages / 任意静态托管），后端 `CORS_ORIGINS` 里放行前端域名即可。这样可以走 Cloudflare 的全球加速，也免去维护前端镜像。

一个镜像、一条 compose：

| 镜像 | 内容 | 端口 |
| --- | --- | --- |
| `music-studio-backend` | Python 3.12 + FastAPI + SQLite，非 root 运行，数据在 `/app/data` | 容器内固定 8000，对外由宿主机端口映射 + 反代做 HTTPS |

### 容器启动方式（FastAPI 官方写法）

`backend/Dockerfile` 对齐 [FastAPI 官方容器化文档](https://fastapi.tiangolo.com/zh/deployment/docker/)：依赖单独一层、代码放最后、`CMD` 用 **exec 形式**。

```dockerfile
CMD ["fastapi", "run", "app/main.py", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1,::1,172.16.0.0/12"]
```

三个要点：

| 参数 | 作用 | 不加会怎样 |
| --- | --- | --- |
| `fastapi run app/main.py` | 官方推荐；CLI 会自动把路径翻译成 `app.main:app`，无需自己拼 import string | —— |
| exec 形式（JSON 数组） | uvicorn 成为 PID 1，`docker stop` 的 SIGTERM 直达应用，走 lifespan 优雅关闭 | shell 形式会吞掉信号，容器要等超时才被杀 |
| `--proxy-headers` | 官方文档明确要求：**跑在 Traefik / Nginx 这类 TLS 终止代理后面时必须加**，让后端信任 `X-Forwarded-Proto` | 后端看到的 scheme 永远是 http，生成的重定向 / 绝对 URL 会指向 `http://` |
| `--forwarded-allow-ips` | 声明哪些来源算可信代理 | uvicorn 默认只信 `127.0.0.1`；Traefik 在 Docker 网络里源 IP 是网段地址，不加白名单则上面的 `--proxy-headers` 形同虚设 |

> 本项目额外在 `backend/app/main.py` 的 lifespan 里补装了一次日志转发桥接：`fastapi run` 会先用 `dictConfig` 覆盖 uvicorn 的 logger，不补这一步的话 uvicorn 自己的启动 / access 日志不会落进 loguru 文件。详见 `backend/app/logging_setup.py` 的 `ensure_loguru_bridge`。

健康检查**不放进 Dockerfile**（探针路径 / 超时 / 间隔属于部署策略），统一写在 `backend/docker-compose.yml` 的 `healthcheck` 里。

## 一、先在本机试跑（可选）

```bash
cd <仓库根>
cp backend/.env.example .env      # 填 LLM_API_KEY / MUSIC_API_TOKEN，其余可先留空
docker compose up -d --build
```

后端起来后宿主机监听 8788（容器内固定 8000）；前端本地预览用 `cd frontend && npm run dev`（5175）。没配密钥想先看界面和动效：

```bash
MUSIC_STUDIO_MOCK=true docker compose up -d --build   # 离线演示，不联网不花钱
```

`docker compose ps` 里 `backend` 应是 `healthy`；后端日志用 `docker logs -f $(docker compose ps -q backend)` 查看。

## 二、配置镜像仓库

镜像由 [`.github/workflows/build-images.yml`](../.github/workflows/build-images.yml) 构建，push 到 `main`（或推 `v*` 标签、手动触发）即自动推送 **后端镜像**。

### 1. Docker Hub（可选）

1. 在 Docker Hub 创建一个 **Public** 仓库：`music-studio-backend`（私有仓库也能用，服务器拉取前要 `docker login`）。
2. 记下你登录 Docker Hub 的**账户密码**（下方 Secrets 填 `DOCKERHUB_PASSWORD`）。若账号开启了两步验证，密码登录会被拒，需改用 **Account settings → Personal access tokens → New access token**（权限 Read & Write）作为密码值。
3. 仓库 `Settings → Secrets and variables → Actions` 添加：

| 类型 | 名称 | 值 |
| --- | --- | --- |
| Secret | `DOCKERHUB_USERNAME` | Docker Hub 用户名 |
| Secret | `DOCKERHUB_PASSWORD` | 登录密码（用户名 + 密码方式登录；开启两步验证时填访问令牌） |
| Variable（可选） | `DOCKERHUB_NAMESPACE` | 命名空间，不填则用 GitHub 仓库 owner |

> 不配 `DOCKERHUB_USERNAME` 时，整个 Docker Hub 发布步骤会自动跳过（不会因缺密码报错）。

### 2. 阿里云 ACR（国内服务器强烈建议）

国内机器直连 Docker Hub 经常几十 KB/s 甚至超时，走 ACR 会快一个量级。

1. 阿里云控制台 → **容器镜像服务 ACR** → 个人实例（免费）→ 创建命名空间，例如 `liao-music`。
2. 在「访问凭证」里设置固定密码（用户名通常是阿里云账号全名或 RAM 子账号）。
3. 记下实例的**公网地址**，例如 `registry.cn-hangzhou.aliyuncs.com`（区域按实例实际所在地）。
4. 继续添加：

| 类型 | 名称 | 值 |
| --- | --- | --- |
| Variable **或** Secret | `ALIYUN_REGISTRY` | `registry.cn-hangzhou.aliyuncs.com` |
| Variable **或** Secret | `ALIYUN_NAMESPACE` | 命名空间，如 `liao-music` |
| Variable **或** Secret | `ALIYUN_USERNAME` | ACR 用户名 |
| Variable **或** Secret | `ALIYUN_PASSWORD` | ACR 固定密码 |

> `ALIYUN_*` 四个值**放 Variables 或 Secrets 都行**（工作流会同时读两者，secret 优先）。常见坑：只配了 `ALIYUN_USERNAME`/`ALIYUN_PASSWORD` 却漏了 `ALIYUN_REGISTRY`/`ALIYUN_NAMESPACE`，或把 registry/namespace 写成了别的名字，都会导致「判定为未配置、整段跳过」。工作流第一步 `Resolve registry flags` 会打印每个变量是 `set` 还是 `EMPTY`，照着补即可。
>
> 阿里云这四个变量是可选的：**不配就只推 Docker Hub**，工作流会自动跳过 ACR 登录和推送，不会报错。

### 3. 架构

默认只构建 `linux/amd64`。服务器是 ARM（如阿里云倚天实例、华为鲲鹏）时，加一个 Variable：

```text
DOCKER_PLATFORMS = linux/amd64,linux/arm64
```

ARM 构建走 QEMU 模拟，首次会慢一些；构建完成后工作流会用 amd64 镜像跑一次冒烟测试（纯 arm64 时该 job 自动跳过）。

### 4. 产物标签

| 触发 | 生成的 tag |
| --- | --- |
| push `main` | `latest`、`sha-<7位>` |
| push `v1.2.3` | `v1.2.3`、`1.2.3`、`1.2`、`sha-<7位>` |
| 手动 Run workflow | 同 `main` |

生产建议固定 `sha-xxxxxxxx` 或版本号，`latest` 只用于尝鲜 —— 回滚时改回旧 tag 即可。

## 三、部署到云服务器

### 1. 安装 Docker

```bash
# Ubuntu / Debian / CentOS 通用
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker
docker compose version      # 需要 Compose v2
```

> 不想用一键脚本：按官方文档装 `docker-ce` + `docker-compose-plugin` 即可。**不要**用系统源里的旧 `docker-compose` v1，本仓库的 compose 文件用了 v2 语法（`name`、`env_file.required`）。

### 2. 上传编排文件

服务器上建目录（路径随意），只需要两个文件：

```bash
sudo mkdir -p /opt/music-studio && cd /opt/music-studio
# 从仓库取 backend/docker-compose.yml 与 backend/.env.example
# 可以用 scp、也可以用 git clone 后只拷这两个文件
cp .env.example .env
vim .env
```

`.env` 里至少要改：

```ini
# 二选一：Docker Hub 或阿里云 ACR
BACKEND_IMAGE=registry.cn-hangzhou.aliyuncs.com/liao-music/music-studio-backend:sha-1a2b3c4

# 后端对外端口（由 Cloudflare / 宿主 Nginx / 云 LB 做 HTTPS 终止）
BACKEND_PORT=8788

# 前端（GitHub Pages / Cloudflare）与后端不同源，必须放行，逗号分隔：
CORS_ORIGINS=https://music.openaim.cn,https://yourname.github.io

LLM_API_KEY=sk-xxxx
MUSIC_API_TOKEN=xxxx
```

### 3. 拉取并启动

```bash
# 用 ACR 时先登录（Docker Hub 公共仓库可跳过）
echo "<ACR密码>" | docker login registry.cn-hangzhou.aliyuncs.com -u "<ACR用户名>" --password-stdin

docker compose pull
docker compose up -d
docker compose ps              # backend healthy 才算成功
```

验证：

```bash
curl http://127.0.0.1:8788/v1/health    # 后端：{"status":"ok",...}
```

`/v1/health` 返回里有 `database` 和 `music_model`，可以确认容器连的是哪个库、用的哪个模型。

### 4. 只在回环暴露端口（推荐，配合宿主反代）

`backend/docker-compose.yml` 里把端口改成只绑本机，再由 Cloudflare Tunnel / 宿主 Nginx / Caddy 做 HTTPS 终止：

```yaml
ports:
  - "127.0.0.1:${BACKEND_PORT:-8788}:8000"
```

反过来，如果不打算在宿主机上再套一层反代，就用默认的 `0.0.0.0:8788` 并放行该端口（公网直连时务必用 HTTPS，见下）。

## 四、HTTPS

> 强烈建议不要直接把 8788 暴露在公网。下面任选一种把后端藏在 HTTPS 之后。

### 方案 A：Cloudflare Tunnel（最省事，无需开放端口）

在 Cloudflare 里把前端域名（如 `music.openaim.cn`）指向 GitHub Pages，再开一个 Tunnel 把 `https://api.music.openaim.cn` 转到服务器 `127.0.0.1:8788`。服务器安全组可完全不开放 8788。

### 方案 B：Caddy（自动申请续期证书）

```bash
sudo apt install -y caddy   # 或参考 caddy 官方源安装
```

`/etc/caddy/Caddyfile`：

```caddyfile
api.music.example.com {
    encode gzip
    reverse_proxy 127.0.0.1:8788
}
```

```bash
sudo systemctl reload caddy
```

前端 `VITE_APP_API_BASE_URL` 对应填 `https://api.music.example.com/v1`。

### 方案 C：宿主机 Nginx + certbot

```nginx
server {
    listen 80;
    server_name api.music.example.com;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://$host$request_uri; }
}
server {
    listen 443 ssl http2;
    server_name api.music.example.com;
    ssl_certificate     /etc/letsencrypt/live/api.music.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.music.example.com/privkey.pem;

    client_max_body_size 2m;

    location / {
        proxy_pass http://127.0.0.1:8788;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
    }
}
```

> 前端是静态托管、与后端不同源，所以后端的 `CORS_ORIGINS` 必须包含前端域名（多个用逗号分隔）。
> 改 `CORS_ORIGINS` 后 `docker compose up -d` 重启即可生效，无需重新构建镜像。

## 五、更新与回滚

```bash
# 更新到某个版本
sed -i 's#music-studio-backend:.*#music-studio-backend:sha-newtag#' .env
docker compose pull && docker compose up -d

# 回滚：把 .env 里的 tag 改回旧值，再执行同一条命令
```

后端重启时队列不会丢：未完成的任务留在 SQLite 里，新进程启动会立刻扫描并续跑（已落盘的阶段产物直接跳过）。所以**跑任务时更新镜像也不会丢作品**，但正在执行的那一轮会从头重跑该阶段。

## 六、可选：push 之后自动部署

手动 `ssh` 上服务器拉镜像嫌麻烦，可以在发布工作流末尾加一个 `deploy` job。前置条件：

1. 生成一对专用密钥：`ssh-keygen -t ed25519 -f deploy_key -N ""`，公钥追加到服务器的 `~/.ssh/authorized_keys`。
2. 添加 Secrets：`SSH_HOST`、`SSH_USER`、`SSH_KEY`（`deploy_key` 的**私钥全文**）、`SSH_PORT`（可选，默认 22）。
3. 在 `.github/workflows/build-images.yml` 末尾追加：

```yaml
  deploy:
    needs: [build]
    runs-on: ubuntu-latest
    # 只部署 main 分支，发布 tag 不自动上线
    if: github.ref == 'refs/heads/main'
    steps:
      # metadata-action 打的是 7 位短 sha，这里算出一致的标签再传给远端
      - name: Resolve image tag
        id: tag
        run: echo "short=sha-${GITHUB_SHA:0:7}" >> "$GITHUB_OUTPUT"

      - name: Pull and restart on server
        uses: appleboy/ssh-action@v1
        env:
          IMAGE_TAG: ${{ steps.tag.outputs.short }}
        with:
          host: ${{ secrets.SSH_HOST }}
          username: ${{ secrets.SSH_USER }}
          key: ${{ secrets.SSH_KEY }}
          port: ${{ secrets.SSH_PORT || 22 }}
          # 必须声明才能把 IMAGE_TAG 带进远端脚本
          envs: IMAGE_TAG
          script: |
            cd /opt/music-studio
            # 只替换 tag，保留 Docker Hub / ACR 的前缀
            sed -i -E "s#(music-studio-backend:).*#\1${IMAGE_TAG}#" .env
            grep -E '^BACKEND_IMAGE=' .env
            docker compose pull
            docker compose up -d
            docker image prune -f
```

> 自动部署意味着「每次合并到 main 就上线」。想更稳的话，把 `if` 改成只在发布标签时触发：
> `if: startsWith(github.ref, 'refs/tags/v')`，此时把 `IMAGE_TAG` 换成 `${{ github.ref_name }}`。

## 七、前端发布到 GitHub Pages / Cloudflare

前端走静态托管，不走容器：

- **GitHub Pages**：推送 `frontend/**` 即触发 [`.github/workflows/deploy-github-pages.yml`](../.github/workflows/deploy-github-pages.yml)，自动构建并发布。接口地址在 `frontend/.env.production` 的 `VITE_APP_API_BASE_URL`（已设为后端域名），**改这里后重新跑 Pages 工作流即可，无需动后端**。
- **Cloudflare Pages / 其它**：本地 `cd frontend && npm run build`，把 `dist/` 上传即可；CI 用 `npm ci && npm run build` 同样产出 `dist/`。

自定义域名在托管平台绑定（GitHub Pages 用 `PAGES_CUSTOM_DOMAIN` 写 CNAME；Cloudflare 直接在 DNS 面板接上）。

### 7.1 前端路由与 404 回退（重要）

前端现在用的是 **history 模式路由**（`createWebHistory`）：`/` 是工作台，任何匹配不到的路径（如 `/foo`、`/studio/x`）都渲染 404 页面。history 模式要求托管端对「不存在的路径」返回 `index.html`（SPA 回退），否则直接刷新子路由会得到 404。

构建时已**自动把 `dist/index.html` 复制成 `dist/404.html`**（见 `frontend/vite.config.ts` 的 `spa404` 插件），这个副本带正确的 hash 资源引用。各平台用法：

- **GitHub Pages**：由 `deploy-github-pages.yml` 在部署前 `cp dist/index.html dist/404.html`（已含），未知路径自动回退到 SPA 壳，再由前端路由渲染 404 页。无需额外配置。
- **Cloudflare Pages**：默认就会对缺失路径返回 `404.html`，同样能正确回退；若想要深链返回 200，可在项目根放一个 `_redirects`：
  ```text
  /* /index.html 200
  ```
  ⚠️ **`/* /index.html 200` 的副作用**：它对所有「不存在的文件」都返回 `index.html`，包括**哈希已失效的旧资源**。举例：旧部署的 `index.html` 引用了 `/assets/index-ABC.js`，重部署后哈希变成 `index-XYZ.js`，若浏览器/用户手输访问到旧的 `/assets/index-ABC.js`（文件已不存在），Cloudflare 会回退成 `index.html` → 浏览器「打开成了首页」。**这正是你看到的 `https://music.openaim.cn/assets/index-CJtEQ1Z8.js 打开是首页` 的根因**。

排查与规避：

1. **确认每次部署都整包上传 `dist/`**（index.html + assets/ 一起），不要让 CDN/浏览器缓存旧的 `index.html`。正常情况下 `index.html` 引用的哈希一定存在，不会触发回退。
2. 若想让「确实不存在的静态资源」老老实实返回 404（而不是 SPA 壳），把重写规则收窄到「非文件型路由」：用 Cloudflare 的 **Transform Rules / 一个 Worker** 只在路径不含 `.`（即不是带扩展名的文件）时才回退到 `index.html`，带扩展名且缺失的就返回 404。或者干脆改用 **hash 模式路由**（`createWebHashHistory`，URL 形如 `/#/studio`）——完全不需要服务器回退，最省心，代价是链接带 `#`。
3. 访问一个**确实不存在的普通路由**（如 `https://music.openaim.cn/nope`）时，看到的是我们自己的「页面走丢了」404 页，而不是裸的首页——说明路由与回退都正常工作。

> 体检：想确认回退配置对不对，浏览器开 DevTools 访问一个不存在的路径（如 `/zzz`），Network 里应看到返回的是 `index.html`（200 或 404 均可），页面应显示「页面走丢了」而非工作台。

## 八、数据备份与恢复

所有持久化数据都在命名卷 `music-studio-data`（容器内 `/app/data`）：

- `music_studio.db` — 作品与阶段记录
- `outputs/<song_id>/` — 音频、封面、阶段 JSON
- `logs/music-studio-YYYY-MM-DD.log` — 运行日志（按天轮转，保留 7 天，随卷一起备份）

```bash
# 备份到当前目录
docker run --rm -v music-studio-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/music-data-$(date +%F).tar.gz -C /data .

# 恢复（先停服务）
docker compose stop backend
docker run --rm -v music-studio-data:/data -v "$PWD":/backup alpine \
  sh -c "rm -rf /data/* && tar xzf /backup/music-data-2026-11-15.tar.gz -C /data"
docker compose start backend
```

> 别用 `docker compose down -v`：`-v` 会连卷一起删，作品全部丢失。
> 想定期备份就加一条 crontab，把压缩包同步到对象存储（OSS/S3）。

## 九、排障

| 现象 | 原因与处理 |
| --- | --- |
| `docker compose up` 报 `BACKEND_IMAGE 请在 .env 里设置` | `.env` 没放到 compose 同目录，或变量名拼错。`docker compose config` 可打印解析结果 |
| 容器启动即退出，日志 `permission denied: '/app/data/...'` | 用了 bind mount 且宿主机目录属主不是 `10001`。改回命名卷，或 `sudo chown -R 10001:10001 ./data` |
| 页面能打开但顶栏「后端未连接」 | 先 `curl http://127.0.0.1:8788/v1/health` 确认后端活着；活着但前端仍连不上，多半是 **CORS**：检查 `.env` 的 `CORS_ORIGINS` 是否包含前端域名、`VITE_APP_API_BASE_URL` 是否指向正确后端地址 |
| `docker pull` 卡住或超时 | 国内机器改用阿里云 ACR 地址；或给 daemon 配镜像加速器 |
| 生成任务一直 `queued` | backend 容器重启过？看日志里「恢复队列」是否有记录；也可能是 `MUSIC_STUDIO_MAX_ACTIVE_JOBS` 满了（默认 4） |
| 用户反馈「今日生成额度已用完」 | 这是成本闸门生效：`MUSIC_STUDIO_DAILY_LIMIT`（默认 50 首/天）已用满，日志里有「每日生成配额已用完：50/50」。临时放开调大该值并 `docker compose up -d`，或直接设 0 关闭配额（不建议公网这么干）。当前用量可看 `/v1/health` 的 `daily_used` / `daily_remaining` |
| 任务全部失败且日志有 `证书` / SSL 报错 | 企业代理自签证书：把 `root.crt` 放回 `backend/app/`（镜像已内置）并在 `.env` 设 `LLM_CA_CERT=root.crt`，启动日志会打印实际使用的证书路径 |
| 想查某次生成的详细过程 | 应用日志在数据卷里：`docker exec $(docker compose ps -q backend) tail -n 100 /app/data/logs/$(date +%F).log`。默认 INFO 就含「每个阶段用了哪个模型 / 多少 token / 耗时」；排查疑难再在 `.env` 里设 `DEBUG=true` 重启（会记录 LLM 请求参数与返回内容摘要，注意日志量增大，别长期开） |
| 换了 LLM 模型但数据库里 `song_stages.model` 没变 | 断点续跑的阶段会跳过执行复用旧产物，日志里是「复用已落盘产物…跳过执行（模型沿用上次记录的 X）」。想用新模型重跑把 `MUSIC_STUDIO_RESUME=false` 写进 `.env` 再 `docker compose up -d` |
| 磁盘涨得快 | `docker system df` 看镜像与日志；本仓库已给容器配了 json-file 轮转（10MB×3），应用日志另有 7 天保留（`MUSIC_STUDIO_LOG_RETENTION_DAYS`）。旧作品需要定期清理 `outputs/` |
| 想开多个 backend 副本 | **不行**。任务队列的 worker 在进程内，靠内存表去重；多副本会重复消费同一批任务、重复消耗 LLM 额度。要横向扩容得先把任务认领做成数据库原子操作 |

## 十、本地开发仍用源码

容器化只影响部署，开发照旧：

```bash
cd frontend && npm run dev            # 5175
uv run --project backend uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8788
```

改完代码想验证镜像：

```bash
docker compose build && docker compose up -d
```
