# 故障排查

CLI 报错时**先看 stderr 上的错误码方括号**，再对照下表处置。

## 鉴权 / 登录

| 现象 | 含义 | 处理 |
|---|---|---|
| `未登录` | 没读到 `~/.neodrop/credentials.json` | 提示用户跑 `npx neodrop login` |
| 浏览器打开授权页报「授权请求不合法 / 缺少有效的 session 参数」 | 打开的 URL 没带合法 `?session=cas_...`。**最常见是 CLI 版本过旧**——旧版走 callback 授权模式、打印的是 `/cli-auth?callback=&state=&name=`，新授权页（session polling）不认；其次才是复制 URL 时漏了 `?session=` 尾部 | 先看 URL 形态：① 是 `?callback=&state=`（或这几个键全空）→ CLI 是旧版，npx 命中了本地缓存的旧版，用 `npx neodrop@latest login` 强制取最新后再登，新链接形如 `?session=cas_...`；② 已是 `?session=` 只是没带全 → 重新 `login`、整行复制完整。链接 10 分钟有效，过期需重发 |
| `[UNAUTHORIZED]` | PAT 失效（过期 / 被撤销 / 用户登出） | 提示用户重新 `login`；若是无头环境见 [auth.md](auth.md) |
| `[FORBIDDEN]` | PAT 没权限调这个 procedure（比如 admin procedure） | 不要重 login——PAT 永远是普通用户身份；换命令或让真实管理员操作 |

## 网络 / 域名

| 现象 | 处理 |
|---|---|
| `连接失败：ECONNREFUSED` | 后端没起 / 端口错。本地 dev：`NEODROP_SERVER=http://localhost:4001 npx neodrop login` 重新登 |
| `连接失败：ENOTFOUND` / `EAI_AGAIN` | DNS 不通；检查 `apiOrigin` 是否写错（看 `whoami` 输出） |
| `连接失败：self-signed certificate` / `unable to verify ... certificate` | 私有部署用了自签证书；当前不支持，建议配 LetsEncrypt 或反代加证书 |
| `连接失败：HeadersTimeoutError` / 卡住 30 秒后报错 | 网络抖动 / 后端无响应（CLI 单次请求 30s 超时，已自动重试一次）；稍后重试 |

## 后端业务

| 现象 | 含义 | 处理 |
|---|---|---|
| `[NOT_FOUND]` | id / slug 不存在 | 先 `channels list` / `grains list` 列一下核对 id |
| `[BAD_REQUEST]` | input schema 不对（用 `--json` 时最常见） | 看 stderr 错误详情；对照 `neodrop <cmd> --help`；复杂 input 推荐先用糖衣命令走通再退到 `--json` |
| `[INTERNAL_SERVER_ERROR]` | 后端炸了 | 重试一次；若持续，开 issue 带上 stderr 全文 |

## 环境

| 现象 | 处理 |
|---|---|
| `npx: command not found` / 报不支持的语法 / `fetch is not defined` | Node 缺失或版本过低；装 Node 18+（`node --version` 自查） |
| `npx` 卡在下载 / 用的是旧版 | npx 会缓存包；用 `npx neodrop@latest <cmd>` 强制取最新，或 `npm i -g neodrop-cli` 全局装一份 |
| stdout 看着是空 / 不是 JSON | 命令真的失败了，看 stderr——CLI 保证只在成功时往 stdout 写 JSON |
