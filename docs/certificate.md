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

## 三、当前线上部署的实测现状

先说清楚现状，才能对症下药。以下是实测结果（2026-11-15）：

| 对象 | DNS | 证书签发者 | 根 CA | 手机能否信任 |
|---|---|---|---|---|
| `music.openaim.cn`（前端页面） | 104.21.43.12（**Cloudflare**） | Google Trust Services `WE1` | 公共 CA | ✅ 原生信任 |
| `api-music.alsoapp.com`（后端 API） | 8.134.58.183（直连源站） | `Rhapsody Engine DigiCert` | `AlsoApp DigiCert Root CA` | ❌ **不信任** |
| `llm.alsoapp.com`（后端出方向） | — | 同上 | 同上 | 仅后端需要 |

**后端侧一切正常**（实测）：

- `https://api-music.alsoapp.com/v1/health` → **HTTP 200**；
- CORS 精确放行：`Origin: https://music.openaim.cn` → `access-control-allow-origin: https://music.openaim.cn`；
- 证书链**完整**：服务端同时下发了叶子证书和中间 CA
  （`Rhapsody Engine DigiCert` ← `AlsoApp Development Dept CA 2026` ← `AlsoApp DigiCert Root CA`），
  所以手机上只需信任**根 CA**即可，不用管中间证书。

**结论：手机端访问失败的唯一原因是「不信任 `AlsoApp DigiCert Root CA`」**，
前端页面能打开（走 Cloudflare，公共证书），但页面里的 API 请求被浏览器在 TLS 层拦掉，
表现为「连不上后端」。

---

## 四、手机端怎么访问

### 方案 A：让 API 域名也走 Cloudflare（推荐，5 分钟，不用动服务器）

前端 `music.openaim.cn` 已经在 Cloudflare 后面，证书是公共 CA。把 API 域名也挂上去，
Cloudflare 边缘会用公共证书终止 TLS，**手机零配置立刻可用**。

**Cloudflare 控制台操作**：

1. DNS → 添加记录
   - 类型 `CNAME`，名称 `api-music`，目标 `8.134.58.183`
   - **代理状态：已代理（橙色云朵）** ← 关键，灰色云朵等于没走 Cloudflare
2. SSL/TLS → 边缘证书 → 加密模式设 **完全（Full）**
   - Full 模式不回源校验源站证书，所以源站继续用自签证书也没问题；
   - 不要选 Flexible（会回源走 HTTP，且 Cloudflare 会一直告警）。
3. 等 DNS 生效（一般几分钟），手机直接访问 `https://api-music.alsoapp.com` 即可。

**注意事项**：

- 建议加一条 Cache Rule 排除 `/v1/*`，避免 `/v1/history` 这类 GET 被边缘缓存；
- Cloudflare 免费版有 100 秒源站响应上限——本项目的音乐生成是**异步**的
  （POST 立即返回 `queued`，前端轮询进度），不受影响。

### 方案 B：让 Traefik 用 Let's Encrypt（服务器侧根治）

同样能实现「手机零配置」，且不依赖 Cloudflare。`backend/docker-compose.yml` 的标签里
`certresolver=letsencrypt` 已经写好，还差 Traefik 侧的 resolver 配置：

```yaml
# traefik.yml / traefik 静态配置
certificatesResolvers:
  letsencrypt:
    acme:
      email: your-email@example.com
      storage: /letsencrypt/acme.json
      tlsChallenge: true        # 或 httpChallenge（需 80 端口可达）
```

前置条件实测**都已满足**：`api-music.alsoapp.com` 已解析到 `8.134.58.183`、
公网 80 端口开放（Traefik 返回 301 跳转）、compose 标签已引用 `letsencrypt`。
注意 `acme.json` 权限要 `600`。

### 方案 C：手机装根证书（临时 / 少量设备）

**下载**：根证书已随前端一起发布，地址

```
https://music.openaim.cn/root.crt
```

（文件在仓库的 `frontend/public/root.crt`，`public/` 下的文件会被 Vite 原样复制到
`dist/`，Pages 部署后即可通过站点下载。仓库里的原件是 `backend/root.crt`。）

**iOS（可行，两步）**：

1. Safari 打开上面的地址下载 → 弹出「已下载描述文件」→ 点右上角「已下载」
   → **设置** → 通用 → VPN 与设备管理 → 点已安装的描述文件 → 安装；
2. **必须再做一步**：**设置** → 通用 → 关于本机 → 证书信任设置 →
   打开 `AlsoApp DigiCert Root CA` 的**完全信任**开关。
   ⚠️ 不开这一步，iOS 会照样报证书无效——这是最常见的踩坑点。

**Android（7 及以上基本无解）**：

从 Android 7.0 开始，系统**默认不信任用户安装的 CA**，Chrome 等浏览器又使用
系统信任库，所以「装个证书就行」在 Android 上不成立。除非：

- 设备已 root，用 Magisk 模块把证书放进系统 CA 目录；或
- 应用自己声明 `network_security_config` 信任用户 CA（浏览器不适用）。

**结论：Android 设备请走方案 A 或 B，别指望装证书。**

### 方案 D：局域网内临时用 HTTP（仅限纯内网调试）

手机在同一局域网时，可以直接访问源站的明文端口：

```
http://8.134.58.183:8000/v1/health
```

但**前端页面是 HTTPS**，从 HTTPS 页面请求 HTTP 会被浏览器按
Mixed Content 直接拦截，所以这条路只适合用 Postman / curl 之类原生客户端验证，
不能用于浏览器里的完整功能。

---

## 五、其他设备（桌面）没装根证书，怎么访问

手机上怎么解决见上一章；桌面端的选择更多。**同样优先推荐「让服务器用公共 CA」**——
Cloudflare 代理（见第四章方案 A）或 Traefik + Let's Encrypt（见第四章方案 B），
两者都能做到「新设备拿来就用」，不需要任何手动导入。

如果因为某些原因必须继续用自签 CA，那就只能把根证书分发到每台设备：

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

## 六、前端能不能像后端一样嵌入根证书

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

## 七、自检清单

**出方向（后端 → LLM 网关）**

```bash
# 从仓库根执行；--cacert 就是后端等价物
curl --cacert backend/root.crt -s -o /dev/null -w "%{http_code}\n" https://llm.alsoapp.com/v1/models
```

后端侧看启动日志有没有 `LLM 请求启用自定义 CA 证书：<路径>`。

**入方向（浏览器 → Traefik）**

```bash
# 看服务端实际发了哪张证书、链到哪个根
openssl s_client -connect api-music.alsoapp.com:443 -servername api-music.alsoapp.com </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
```

浏览器里打开站点 → 证书查看器，确认链的根是不是设备已信任的那张。

**模拟手机视角（完整跑一遍「浏览器会做的事」）**

```bash
# 1) 手机装的根证书能不能验证服务端证书链
openssl s_client -connect api-music.alsoapp.com:443 -servername api-music.alsoapp.com \
  -CAfile backend/root.crt </dev/null 2>&1 | grep "Verify return code"
#    期望：Verify return code: 0 (ok)
#    若仍是 20，说明发的链不完整（缺中间 CA），后端侧要补中间证书

# 2) 带手机页面的 Origin 跑一次真实 API 调用，验 CORS 与业务
curl --cacert backend/root.crt -i https://api-music.alsoapp.com/v1/health \
  -H "Origin: https://music.openaim.cn"
#    期望：HTTP 200 + access-control-allow-origin: https://music.openaim.cn
```

> Windows 的 `curl` 走 schannel，对企业自签链经常握手失败（`exit 35`），
> 这**不代表**服务端有问题——用 `openssl s_client` 或 Python 的 `httpx` 验证更准。

**排障对照表**

| 现象 | 原因 | 处理 |
|---|---|---|
| 后端日志「LLM 请求启用自定义 CA 证书」 | 出方向正常 | — |
| 后端日志「LLM_CA_CERT 指向的证书不存在」 | 路径没命中 | 把 `root.crt` 放 `backend/`，`.env` 写 `LLM_CA_CERT=root.crt` |
| 后端 SSL 校验失败、但 curl 带 `--cacert` 也失败 | 根证书过期 / 不是这张 CA / 中间还缺中间证书 | 核对 `openssl x509 -noout -subject -issuer -dates` |
| 浏览器「你的连接不是私密连接」 | **入方向**：Traefik 那张证书的根没被信任 | 走方案 A（Cloudflare 代理）或方案 B（Let's Encrypt） |
| **手机能打开前端页面，但页面报「连不上后端」** | 前端页面走 Cloudflare（公共证书，能开），但 API 域名是自签链，手机不信任 | 手机端见第四章；根治是让 API 域名也走 Cloudflare 或 Let's Encrypt |
| 手机装完证书仍报错（iOS） | 忘了开「完全信任」 | 设置 → 通用 → 关于本机 → 证书信任设置 → 打开完全信任 |
| 手机装完证书仍报错（Android 7+） | 系统不信任用户 CA | 只能走 Cloudflare / Let's Encrypt，或 root + Magisk |
| 浏览器提示 NET::ERR_CERT_AUTHORITY_INVALID | 同上，且是本机已装过的情况 | 根证书过期或被替换，重新导入新的根证书 |
| 浏览器提示 ERR_CERT_DATE_INVALID | Traefik 证书过期 | 配 certresolver 自动续期，或重新签发 |
