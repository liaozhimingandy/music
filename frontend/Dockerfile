# Music Studio 前端镜像：Node 构建静态产物 -> nginx 托管
#
# 构建（在仓库根执行）：
#   docker build -t music-studio-frontend:latest ./frontend
# 自定义接口地址（默认同源 /v1，由同镜像内的 nginx 反代到 backend）：
#   docker build --build-arg VITE_APP_API_BASE_URL=https://api.example.com/v1 ./frontend
#
# 为什么不把 dist 塞进后端镜像：后端只提供 /v1 API，静态资源交给 nginx 更合适
# （gzip/强缓存/SPA fallback 都是它擅长的），升级前端也不用重启后端进程、
# 不会打断正在跑的音乐任务。两者通过 compose 的服务名 backend 互连。

# ---------- 构建阶段 ----------
FROM node:20-alpine AS build

WORKDIR /app

# 先只拷依赖清单：package.json 没变时 npm ci 这层走缓存，改业务代码不重装依赖。
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# 这三个变量在 vite build 时被静态注入到产物里，属于「构建期配置」，
# 改了必须重新构建镜像（运行时改环境变量无效）。
# VITE_APP_API_BASE_URL=/v1 表示同源请求，由 nginx 的 location /v1/ 反代给后端，
# 这样浏览器不发跨域请求，后端 CORS 配不配都无所谓。
ARG VITE_APP_API_BASE_URL=/v1
ARG VITE_BASE_PATH=/
ARG VITE_APP_IS_DEBUG=False
ENV VITE_APP_API_BASE_URL=${VITE_APP_API_BASE_URL} \
    VITE_BASE_PATH=${VITE_BASE_PATH} \
    VITE_APP_IS_DEBUG=${VITE_APP_IS_DEBUG}

# npm run build 内含 vue-tsc --noEmit 类型检查：类型错了就让镜像构建失败，
# 不要把一个「能打包但运行会白屏」的产物推到生产。
RUN npm run build

# ---------- 运行阶段 ----------
FROM nginx:1.27-alpine

# 去掉默认站点，换成我们的配置（SPA fallback + /v1 反代 + 静态强缓存）。
# 安全头单独放 snippets 里，因为 nginx 的 add_header 在子 location 中会整体
# 覆盖父级，抽出来 include 才能保证每个 location 都带上。
RUN rm -f /etc/nginx/conf.d/default.conf && mkdir -p /etc/nginx/snippets
COPY nginx.conf /etc/nginx/conf.d/music-studio.conf
COPY music-studio-security.conf /etc/nginx/snippets/music-studio-security.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# busybox wget 自带在 alpine 里，不用额外装 curl；/healthz 是 nginx 直接返回的
# 静态 200，不经过后端，因此能区分「前端容器挂了」和「后端挂了」。
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1

# 官方镜像的 entrypoint 会处理信号转发，nginx 保持前台运行即可。
CMD ["nginx", "-g", "daemon off;"]
