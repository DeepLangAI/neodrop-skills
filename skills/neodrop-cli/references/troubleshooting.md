# 故障排查

CLI 报错时**先看 stderr 上的错误码方括号**，再对照下表处置。

## 鉴权 / 登录

| 现象 | 含义 | 处理 |
|---|---|---|
| `未登录` | 没读到 `~/.neodrop/credentials.json` | 提示用户跑 `./bin/neodrop login` |
| `[UNAUTHORIZED]` | PAT 失效（过期 / 被撤销 / 用户登出） | 提示用户重新 `login`；若是无头环境见 [auth.md](auth.md) |
| `[FORBIDDEN]` | PAT 没权限调这个 procedure（比如 admin procedure） | 不要重 login——PAT 永远是普通用户身份；换命令或让真实管理员操作 |

## 网络 / 域名

| 现象 | 处理 |
|---|---|
| `连接失败：[Errno 61] Connection refused` | 用户在本地 dev：`NEODROP_SERVER=http://localhost:4001 neodrop login` 重新登 |
| `连接失败：[Errno 8] nodename nor servname` | DNS 不通；检查 `apiOrigin` 是否写错（看 `whoami` 输出） |
| `SSL: CERTIFICATE_VERIFY_FAILED` | 私有部署用了自签证书；当前不支持，建议配 LetsEncrypt 或反代加证书 |

## 后端业务

| 现象 | 含义 | 处理 |
|---|---|---|
| `[NOT_FOUND]` | id / slug 不存在 | 先 `channels list` / `grains list` 列一下核对 id |
| `[BAD_REQUEST]` | input schema 不对（用 `--json` 时最常见） | 看 stderr 错误详情；对照 `neodrop <cmd> --help`；复杂 input 推荐先用糖衣命令走通再退到 `--json` |
| `[INTERNAL_SERVER_ERROR]` | 后端炸了 | 重试一次；若持续，开 issue 带上 stderr 全文 |

## 环境

| 现象 | 处理 |
|---|---|
| `未找到 python3` | 装 Python 3.9+；macOS 12+ 自带，Linux 装 `python3` 包 |
| `webbrowser.open() returned False` / 浏览器没起来 | 见 [auth.md 无头环境](auth.md#无头环境agent--ssh--容器) |
| stdout 看着是空 / 不是 JSON | 命令真的失败了，看 stderr——CLI 保证只在成功时往 stdout 写 JSON |
