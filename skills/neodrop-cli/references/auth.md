# 登录与凭证（auth）

## 标准流程（有本地浏览器）

```bash
./bin/neodrop login
```

发生了什么：

1. CLI 起一个**只听 127.0.0.1** 的一次性 HTTP server（随机端口）
2. 拼出 `https://neodrop.ai/cli-auth?callback=http://127.0.0.1:<port>/cb&state=<csrf>&name=<hostname>` 并尝试拉起浏览器
3. 用户在浏览器里登录态下点「同意」，授权页 redirect 回 `http://127.0.0.1:<port>/cb?token=...&state=...`
4. CLI 校验 state（防 CSRF）→ 把 PAT 写入 `~/.neodrop/credentials.json`（chmod 0600）
5. 本地 server 关闭

凭证字段：

```jsonc
{
  "webOrigin": "https://neodrop.ai",
  "apiOrigin": "https://api.neodrop.ai",
  "token": "grain_pat_…",          // PAT 明文，仅本机
  "tokenId": "tok_…",              // 用于 logout / 远程撤销
  "name": "<hostname>",            // 在 /settings/cli-tokens 里展示的名字
  "expiresAt": "2026-09-01T…Z",
  "createdAt": "2026-06-04T…Z"
}
```

## 无头环境（agent / SSH / 容器）

### 方案 A：`--no-browser`（同机有浏览器，但拉不起来）

适用：SSH session、`DISPLAY` 没设、`webbrowser.open()` 静默失败的环境，但**这台机器自己**或**同一局域网内**有可访问 `127.0.0.1:<port>` 的浏览器。

```bash
./bin/neodrop login --no-browser
```

CLI 只打印授权 URL + 监听端口，**不尝试**调 `webbrowser.open()`。把 URL 复制到任意能上网的浏览器（手机、另一台笔记本均可）打开，授权后 callback 仍走 `http://127.0.0.1:<port>/cb`——所以浏览器机器必须能访问 CLI 机器的 loopback。

> 如果浏览器和 CLI 不在同一台机器、又不在同一局域网（典型云沙箱），方案 A **不可用**，走方案 B。

### 方案 B：`--import`（凭证从已登录机器搬过来）

适用：完全无浏览器的云沙箱 / CI / 远程 agent。

1. 在本地（有浏览器）机器跑 `./bin/neodrop login` 正常登录
2. 把生成的凭证传到无头机器：

   ```bash
   # 本地
   cat ~/.neodrop/credentials.json | ssh agent-box \
     'cat > ~/.neodrop/credentials.json && chmod 600 ~/.neodrop/credentials.json'
   ```

   或者用 CLI 自带的 import：

   ```bash
   # 无头机器上
   cat creds.json | ./bin/neodrop login --import
   # 或 ssh 一行：
   ssh agent-box './bin/neodrop login --import' < ~/.neodrop/credentials.json
   ```

3. 在无头机器验证：`./bin/neodrop whoami`

**安全提醒**：搬走的 PAT 等价于你的登录身份，传输请走 SSH / 加密通道。无头机器跑完任务可以 `./bin/neodrop logout` 撤销该 token，下次再 import 一份新的。

### 方案 C：device flow（roadmap）

> 状态：未实现。需要后端加 `cliToken.deviceCode` / `cliToken.devicePoll` 两个 procedure +
> 一个 `/device` 网页（用户输验证码 → 同意），CLI 这边轮询拿 token。
> 真正完全无中介的「打开 URL + 输验证码」体验。
> tracker：见主仓 `docs/neodrop-cli.md` roadmap 节。

## 私有部署

如果你跑的是私有 Neodrop 实例：

```bash
# 方式 A：环境变量
NEODROP_SERVER=https://your-neodrop.example.com ./bin/neodrop login

# 方式 B：login flag
./bin/neodrop login --server https://your-neodrop.example.com
```

默认 API 域按 web origin 启发式推断（`neodrop.ai` → `api.neodrop.ai`；`localhost:4001` → `localhost:3001`；其他与 web origin 同域）。若 api 域不同传 `--api <url>` 或设 `NEODROP_API`。

## 撤销与轮换

- 本地 logout：`./bin/neodrop logout`（远程撤销 + 清本地凭证）
- 网页撤销：[neodrop.ai/settings/cli-tokens](https://neodrop.ai/settings/cli-tokens)
- 看自己签发了哪些 PAT：`./bin/neodrop tokens list`
- 撤销别处的 PAT：`./bin/neodrop tokens revoke <id>`
- 默认 PAT 有效期 90 天；过期重新 `login` 即可（或 `--import` 一份新的）

## 安全模型

- PAT 永远是**普通用户身份**——admin procedure 在 backend 自身的 `adminProcedure` 守卫里拦
- callback 只允许 `localhost` / `127.0.0.1` / `[::1]`（防恶意网站构造 callback 偷 token）
- 明文 token 只在最终 redirect URL 中出现一次，写入文件后 chmod 0600
- `cliToken.issue` 拒绝以 PAT 自己签发新 PAT（防 PAT 派生链）
