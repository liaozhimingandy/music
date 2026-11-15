# 证书与 HTTPS 信任

本文解决两件事：**其他设备没有装根证书时怎么访问**，以及**前端能不能像后端一样嵌入根证书**。

先说结论：

- 后端的 `root.crt` 解决的是**出方向**（后端访问 LLM 网关），跟浏览器访问本服务是两回事；
- **Web 浏览器不能被网页"嵌入根证书"**——这是浏览器的安全模型，不是配置问题；
- 其他设备的正解是**让 Traefik 用公共 CA（Let's Encrypt）**，这样所有设备零配置。

---

## 一、先分清两个 TLS 方向

这是最容易搞混的地方。本项目里 TLS 出现在两处，证书链完全不同：

| 方向 | 链路 | TLS 在哪终止 | 谁在验证证书 | 用哪张 CA |
|---|---|---|---|---|
| **出方向** | 后端 → `llm.alsoapp.com` | 企业 LLM 网关 | **后端自己**（httpx `verify=`） | `backend/root.crt`（AlsoApp DigiCert Root CA） |
| **入方向** | 浏览器 → Traefik → 后端 | Traefik | **浏览器** | Traefik 手上那张（公共 CA 或另一个自签 CA） |

关键推论：

1. 后端配 `LLM_CA_CERT` **不会**影响浏览器访问后端是否报"证书不受信任"——浏览器不认后端的这份配置。
2. 浏览器报证书错误时，要去查 **Traefik 用的那张证书**，而不是 `backend/root.crt`。
3. 如果 Traefik 的自签证书恰好**也是用这个 AlsoApp Root CA 签发的**，那你在本机装 `root.crt` 就同时覆盖了两个方向——这就是为什么"我本机装了就好了"的体感成立。

---

## 二、`backend/root.crt` 是什么

```
subject = C=CN, ST=Guangdong, L=Shenzhen, O=AlsoApp, OU=PKI, CN=AlsoApp DigiCert Root CA
issuer  = 同上（自签根证书）
有效期  = 2026-10-19 → 3026-02-19
```

它服务的是**后端出方向**：后端访问 `https://llm.alsoapp.com/v1` 时，用它验证网关的自签证书。

**配置方式**（`backend/.env`）：

```bash
LLM_BASE_URL=https://llm.alsoapp.com/v1
LLM_CA_CERT=root.crt
```

**路径解析**：`LLM_CA_CERT` 是相对路径时，依次尝试
`backend/app` → `backend` → 仓库根 → 进程 CWD（`app/config.py:ca_cert_candidates`）。
所以 `root.crt`、`.\root.crt`、绝对路径都能命中；找不到时启动日志会打印
`LLM 请求启用自定义 CA 证书`（成功）或 `指向的证书不存在（已尝试：…）`（失败）。

**镜像内置**：`backend/Dockerfile` 有一行 `COPY root.crt ./root.crt`，落到容器内
`/app/root.crt`（= BACKEND_DIR），正好命中上面的候选顺序，无需在 compose 里挂载。

> **安全提示**：CA 根证书本身是**公开信息**（设计上就是要分发出去的），放进仓库没问题。
> 但**私钥绝不能进仓库**——`.gitignore` 已挡掉 `*.key` / `*.pem` / `*.p12` / `*.pfx`。
> 如果你要签发新证书，私钥请放在仓库外。

---

## 三、其他设备没装根证书，怎么访问

按推荐度排序。

### 方案 A：让 Traefik 用 Let's Encrypt（推荐，所有设备零配置）

`backend/docker-compose.yml` 的标签里已经配好了：

```yaml
- "traefik.http.routers.api-tools.tls=true"
- "traefik.http.routers.api-tools.tls.certresolver=letsencrypt"
```

只要满足下面两条，Traefik 会自动申请并续期证书，浏览器**原生信任**，任何设备都不用装任何东西：

1. 域名（如 `music.openaim.cn`）的 A 记录解析到这台机器的公网 IP；
2. 公网的 80 / 443 端口可达（ACME HTTP-01 校验需要）。

这是唯一能做到"新设备拿来就用"的方案。**如果 Traefik 现在用的是自签证书，优先换到方案 A。**

### 方案 B：继续用自签 CA → 把根证书分发到每台设备

自签 CA 的本质是**每台设备都要单独装一次**，没有别的办法（除了方案 A）。

先拿到根证书：Traefik 用的那张 CA 的根证书（如果就是 AlsoApp Root CA，就是本仓库的
`backend/root.crt`），然后按设备导入：

**Windows**（管理员 PowerShell）

```powershell
certutil -addstore -f "ROOT" D:\path\to\root.crt
# 删除：certutil -delstore "ROOT" <证书指纹>
```

**macOS**

```bash
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain root.crt
```

**Linux（Debian / Ubuntu）**

```bash
sudo cp root.crt /usr/local/share/ca-certificates/alsoapp-root.crt
sudo update-ca-certificates
```

**Linux（RHEL / CentOS / Alma）**

```bash
sudo cp root.crt /etc/pki/ca-trust/source/anchors/alsoapp-root.crt
sudo update-ca-trust extract
```

**Android**：设置 → 安全 → 加密与凭据 → 安装证书 → CA 证书 → 选中文件。

**iOS**：把 `.crt` 存进"文件"App → 设置 → 通用 → VPN 与设备管理 → 安装描述文件 →
再到 设置 → 通用 → 关于本机 → 证书信任设置 里**开启完全信任**（这步最容易漏）。

**Firefox**：有独立的证书库，不读操作系统信任库。
设置 → 隐私与安全 → 证书 → 查看证书 →  Authorities → 导入 → 勾选"信任此 CA"。

> Chrome / Edge 在 Windows / macOS / Linux 上都用**操作系统**的信任库，导入系统证书即可生效；
> 导入后需要**重启浏览器**。

### 方案 C：本地开发就别用 TLS（最省事）

前端 dev server 走 `http://localhost:5175`，后端走 `http://localhost:8000`，全程明文 HTTP，
根本不涉及证书。本地开发**不需要**装任何根证书。

### 方案 D：内网 / 固定几台设备 → mkcert

`mkcert` 会生成一张带正确 SAN（含 `localhost`、内网 IP）的证书，并提供根证书安装命令：

```bash
mkcert -install          # 把本地 CA 装进系统信任库
mkcert localhost 192.168.0.105
```

Traefik 可以直接用 mkcert 签出的证书。适合"就这几台开发机"的场景，
省去手工签发和维护自签 CA 的麻烦。

---

## 四、前端能不能像后端一样嵌入根证书

**Web 浏览器：不行。** 这是浏览器的安全模型，不是配置问题：

1. 网页 JS **无法访问**浏览器的证书信任库，也没有任何 API 能往里添加 CA；
2. 网页 JS **读不到**服务端证书——`fetch` / `XMLHttpRequest` 不暴露 TLS 握手细节，
   所以连"自己做证书 pinning 校验"都做不到（想校验只能靠服务端配合）；
3. 浏览器只信任「操作系统证书库」+「浏览器自带证书库」（Chrome/Edge 用前者，Firefox 用后者）。

所以在前端项目里放一份 `root.crt`，**对浏览器信任没有任何作用**。

**唯一能"像后端一样嵌入根证书"的形态是 Electron / Node 客户端**（它们不走浏览器证书库）：

```bash
# Node / CLI / 打包后的工具：直接把 CA 喂给 TLS 栈
NODE_EXTRA_CA_CERTS=/path/to/root.crt node app.js
```

```js
// Electron：拦截证书错误，按指纹白名单放行
const { app } = require('electron')
const crypto = require('node:crypto')

app.on('certificate-error', (event, _webContents, _url, _error, certificate, callback) => {
  const fp = crypto.createHash('sha256').update(certificate.data).digest('hex')
  if (trustedFingerprints.has(fp)) {
    event.preventDefault()
    callback(true)
  } else {
    callback(false)
  }
})
```

**纯 Web 前端可行的折中**：把根证书作为静态文件挂在站点上（例如 `/root.crt`），
在页面里给一个"下载并信任根证书"的按钮，用户点一次、自己导入系统——
把"分发证书"这一步的路径给出来，但**导入动作仍必须由用户手动完成**，无法自动化。

---

## 五、自检清单

**出方向（后端 → LLM 网关）**

```bash
# 从仓库根执行；--cacert 就是后端等价物
curl --cacert backend/root.crt -s -o /dev/null -w "%{http_code}\n" https://llm.alsoapp.com/v1/models
```

后端侧看启动日志有没有 `LLM 请求启用自定义 CA 证书：<路径>`。

**入方向（浏览器 → Traefik）**

```bash
# 看服务端实际发了哪张证书、链到哪个根
openssl s_client -connect music.openaim.cn:443 -servername music.openaim.cn </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
```

浏览器里打开站点 → 证书查看器，确认链的根是不是设备已信任的那张。

**排障对照表**

| 现象 | 原因 | 处理 |
|---|---|---|
| 后端日志「LLM 请求启用自定义 CA 证书」 | 出方向正常 | — |
| 后端日志「LLM_CA_CERT 指向的证书不存在」 | 路径没命中 | 把 `root.crt` 放 `backend/`，`.env` 写 `LLM_CA_CERT=root.crt` |
| 后端 SSL 校验失败、但 curl 带 `--cacert` 也失败 | 根证书过期 / 不是这张 CA / 中间还缺中间证书 | 核对 `openssl x509 -noout -subject -issuer -dates` |
| 浏览器「你的连接不是私密连接」 | **入方向**：Traefik 那张证书的根没被信任 | 走方案 A（Let's Encrypt）或方案 B（导入根证书） |
| 浏览器提示 NET::ERR_CERT_AUTHORITY_INVALID | 同上，且是本机已装过的情况 | 根证书过期或被替换，重新导入新的根证书 |
| 浏览器提示 ERR_CERT_DATE_INVALID | Traefik 证书过期 | 配 certresolver 自动续期，或重新签发 |
