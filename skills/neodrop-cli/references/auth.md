# 登录与凭证（auth）

## 唯一登录流程：session polling

```bash
./bin/neodrop login
```

无 flag、无模式分支。CLI 做这些事：

1. 调后端 `cliToken.startSession` 创建一次性会话（256bit 随机 sessionId，10 分钟有效），
   同时拿到一个**只下发给本 CLI 进程的私有领取凭据 `pollSecret`**（绝不进 URL）
2. 打印一条 `https://neodrop.ai/cli-auth?session=<sid>` 的 verification URL 到 stderr
   （URL 里只有 sessionId，没有 pollSecret）
3. 每 ~2 秒带 `{sessionId, pollSecret}` 轮询 `cliToken.pollSession` 等用户授权
4. 用户在任意浏览器打开 URL → 登录态下进入授权页 → 看到 CLI 自报的 `clientName`
   → **勾选「这是我刚刚启动的 CLI」** → 点同意
5. CLI 拿到 PAT → 写到 `~/.neodrop/credentials.json` chmod 0600

特性：

- **不自动拉浏览器**——只打印 URL，用户自己复制（同机 / 手机 / 另一台机器都可）
- **不开本地 HTTP server / callback**——CLI 单向轮询后端，浏览器不需要回连 CLI 机器
- **没有 token 进 URL 或浏览器历史**——明文 token 只在 CLI ↔ 后端的 HTTPS API 调用里
- **领取要 pollSecret**——poll 必须回传 startSession 下发的私有 `pollSecret`（后端只存其 hash）；
  URL 里只有 sessionId、没有 pollSecret，所以即使 URL 被截图/转发泄漏，第三方也领不走 token
- **单次领取**——CLI 第一次 poll 拿到 token 时，后端立即抹掉 `plaintextToken` 字段
- **session 10 分钟过期**——过期了就重新跑 `login`

## 凭证文件

```jsonc
~/.neodrop/credentials.json
{
  "webOrigin": "https://neodrop.ai",
  "apiOrigin": "https://api.neodrop.ai",
  "token": "grain_pat_…",          // PAT 明文，仅本机；权限 0600
  "tokenId": "tok_…",              // 用于 logout / 远程撤销
  "name": "Claude Code @ macbook",  // 在 /settings/cli-tokens 上展示的客户端名
  "expiresAt": "2026-09-01T…Z",    // 默认 90 天
  "createdAt": "2026-06-04T…Z"
}
```

## 跨机器：scp 凭证文件

云沙箱 / CI / 远程 agent / 任何无浏览器但能 SSH 的机器：先在本地登一次，然后 scp 过去：

```bash
# 本地（有浏览器，已 login 过）：
scp ~/.neodrop/credentials.json agent-box:~/.neodrop/credentials.json
ssh agent-box 'chmod 600 ~/.neodrop/credentials.json && neodrop whoami'
```

凭证文件本身就是凭证——CLI 没有专门的 `import` 命令，因为 `cp` 就够了。
**搬走的 PAT 等价于你的登录身份**，传输请走 SSH / 加密通道。无头机器跑完任务可以
`neodrop logout` 撤销该 token，下次再 scp 一份新的。

## 安全模型

| 风险 | 防御 |
|---|---|
| 别人发你恶意 cli-auth URL 骗同意 | 授权页强制勾选「这是我刚启动的 CLI」+ 显示 `clientName` 让用户人肉辨认 |
| Session id URL 泄漏后被回放领 token | 浏览器可见的 sessionId 与 CLI 私有的 `pollSecret` 分离——领 token 必须持 pollSecret（后端校验其 hash），URL 里没有它；叠加单次领取 + session 10 分钟过期 |
| PAT 自递归签发新 PAT | `approveSession` 拒 `ctx.sessionId.startsWith('pat:')` 的请求，只放浏览器 session 过 |
| PAT 文件在本地裸奔 | 写入时 chmod 0600；同 ~/.aws/credentials 等惯例 |
| startSession 被刷 | 后端 IP 级限流（10 / min） |
| 跨用户越权 | 后端 procedure 自身的 `where: { userId: ctx.userId }` 守卫 |

授权页本身**不可信地**显示 CLI 自报的 `clientName`（任何脚本都能写 `--name "Claude Code"`
骗你）——这就是为什么强制勾选确认的步骤。**用户必须人肉确认** clientName 对得上自己刚跑的命令。

## 私有部署

```bash
# 方式 A：环境变量
NEODROP_SERVER=https://your-neodrop.example.com ./bin/neodrop login

# 方式 B：login flag
./bin/neodrop login --server https://your-neodrop.example.com
```

默认 API 域按 web origin 启发式推断（`neodrop.ai` → `api.neodrop.ai`；`localhost:4001`
→ `localhost:3001`；其他与 web origin 同域）。若 api 域不同传 `--api <url>` 或设
`NEODROP_API`。

## 撤销与轮换

- 本地 logout：`./bin/neodrop logout`（远程撤销 + 清本地凭证）
- 网页撤销：[neodrop.ai/settings/cli-tokens](https://neodrop.ai/settings/cli-tokens)
- 看自己签发了哪些 PAT：`./bin/neodrop tokens list`
- 撤销别处的 PAT：`./bin/neodrop tokens revoke <id>`
- 默认 PAT 有效期 90 天；过期重新 `login` 即可
