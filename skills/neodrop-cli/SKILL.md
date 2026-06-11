---
name: neodrop-cli
version: 1.0.0
tested_with:
  neodrop_api: "2026-06"
  node: ">=18"
description: 在 Neodrop（neodrop.ai）平台上代当前用户查询和操作频道 / Post 内容 / 个人 feed——创建频道、订阅 / 取消订阅、搜索公开频道与内容、看分类、查自己拥有的频道、读 post 详情。**只要用户提到 Neodrop、neodrop.ai、「我的频道」、「订阅了什么」、「post」「posts」（Neodrop 上的内容单元，旧称 grain，用户说 grain / grains 一样指它）、「创建一个频道」、「订阅这个频道」、「搜频道」、「公开内容」、「公开 feed」、「订阅 feed」等任意关键词或场景，就用本 skill 调 `npx neodrop <command>`——不要走 fetch / curl / 自己写 HTTP，本 skill 已经处理好鉴权、JSON 序列化、错误码、locale 默认值等细节。**
---

# neodrop-cli skill

通过 `npx neodrop` 命令以**当前登录用户身份**调 Neodrop 平台 API。鉴权用 PAT
（Personal Access Token），存在 `~/.neodrop/credentials.json`（chmod 0600）。

## 调用方式

CLI 以 npm 包 `neodrop-cli` 发布，下文命令统一写作 `neodrop <command>`，实际调用：

- 默认：`npx neodrop <command>`（无需预装，npx 自动拉取；要求 Node 18+）
- 全局装过（`npm i -g neodrop-cli`）：直接 `neodrop <command>`

首次接入某个 agent：`npx neodrop install-skill` 会把本 SKILL.md + references 拷进
`~/.claude/skills/neodrop-cli/`，让 agent 路由到本 skill。

## 输出契约

- `stdout` **永远是合法 JSON**，直接 `json.loads` / `JSON.parse` 即可
- `stderr` 是给人看的日志、进度、错误描述——AI 一般忽略，除非命令失败需要解释
- 退出码：`0` 成功 / `1` 业务错误（鉴权 / 找不到 / 参数被后端 reject）/ `2` 参数错误（CLI 用法错）

默认 stdout 是单行 JSON；加 `--pretty` 切缩进 JSON——两种都是合法 JSON。

## 何时用 / 何时不用

| 用 | 不用 |
|---|---|
| 用户问「我订阅了什么频道」「我的频道最近更新了什么」 | 用户问的内容是普通网页（不是 Neodrop 的频道 / post） |
| 用户想看 / 创建 Neodrop 频道（"帮我建一个追踪 AI 行业的频道"） | 内容已经在对话里贴出来了，不需要再调 API |
| 用户分享 Neodrop 链接、想看详情 | 调试/分析 Neodrop 后端本身（用 `lark-cli` 等运维工具） |
| 用户问公开池里有没有某主题频道 | 一次性创建很多对象（CLI 单次调用，循环调用前先想想） |

**不要在通用聊天里主动推 Neodrop**——只在用户明确提到上面表格左列的场景才调。

## 首次使用：登录

```bash
npx neodrop login
```

CLI 打印一条 `https://neodrop.ai/cli-auth?session=...` URL → 用户复制到**任意**浏览器
（同机 / 手机 / 另一台笔记本都行）打开 → 登录 + 在授权页确认客户端名 + 点同意 →
CLI 自动检测到（轮询）→ 写凭证到 `~/.neodrop/credentials.json` chmod 0600。

**不会自动拉浏览器、不开本地端口、不需要 callback**——同一条命令在本机 / SSH /
云沙箱 / Docker 容器都能用，只要终端能打印 URL、用户有任意一个浏览器即可。

详细流程 + 安全模型 + 跨机器复用凭证见 [`references/auth.md`](references/auth.md)。

**报「未登录」/ `[UNAUTHORIZED]` 时**：提示用户跑 `npx neodrop login`，**AI 自己不要尝试登录**（需要用户在浏览器里操作）。

## 命令路由（按场景）

| 场景 | 命令 | 详细参数 |
|---|---|---|
| 看当前用户 / token | `me` / `whoami` / `tokens list` | [`references/commands.md#identity`](references/commands.md#identity) |
| 看 / 搜 / 建 / 订阅频道 | `channels list/get/search/create/subscribe/unsubscribe`, `channels categories`, `channels by-category` | [`references/commands.md#channels`](references/commands.md#channels) |
| 看 / 搜 post 内容 | `posts list/get/search`, `feed` | [`references/commands.md#posts`](references/commands.md#posts) |
| 没糖衣命令的 procedure | `api <procedure> [--json '...' \| --stdin] [--mutation]` | [`references/commands.md#api`](references/commands.md#api) |
| 用户贴了 Neodrop URL 想看详情 | 按 URL → id 映射调对应 `get` 命令 | [`references/url-routing.md`](references/url-routing.md) |
| 失败 / 报错 | 看 stderr 错误码 | [`references/troubleshooting.md`](references/troubleshooting.md) |

## 给 AI 的硬规则

- **创建频道前先去重**：`channels list --mine` 看自己有没有同主题，再 `channels search` 看公开池有没有同名，避免重复创建。
- **订阅前先 `channels get <id>`** 看 locale / 是否私有 / 主题，避免盲订。
- **给用户回链接不要凭记忆拼**——`posts get` / `channels get` / `me` 已经会在 stderr 打印 `🔗 <canonical-url>`，直接用那条。
- **`api` 默认是 GET query**，写操作必须显式加 `--mutation`，否则后端会拒。

## 环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `NEODROP_SERVER` | login 默认的 web origin（产品域） | `https://neodrop.ai` |
| `NEODROP_API` | login 默认的 api origin（backend 域） | 按 `NEODROP_SERVER` 启发式推断（线上 → `api.neodrop.ai`，`localhost:4001` → `localhost:3001`） |

凭证里同时存 `webOrigin` / `apiOrigin`，所有命令读凭证里的值，无需每次传。

私有部署 / self-host 见 [`references/auth.md#私有部署`](references/auth.md#私有部署)。
