---
name: neodrop-cli
version: 0.3.0
tested_with:
  neodrop_api: "2026-06"
  python: ">=3.9"
description: 在 Neodrop（neodrop.ai）平台上代当前用户查询和操作频道 / Grain 内容 / 个人 feed——创建频道、订阅 / 取消订阅、搜索公开频道与内容、看分类、查自己拥有的频道、读 grain 详情。**只要用户提到 Neodrop、neodrop.ai、「我的频道」、「订阅了什么」、「grain」「grains」（Neodrop 上的内容单元）、「创建一个频道」「订阅这个频道」「搜频道」「公开内容」「公开 feed」「订阅 feed」等任意关键词或场景，就用本 skill 调本目录下的 `bin/neodrop <command>`——不要走 fetch / curl / 自己写 HTTP，本 skill 已经处理好鉴权、JSON 序列化、错误码、locale 默认值等细节。**
---

# neodrop-cli skill

通过本地 `bin/neodrop` 命令以**当前登录用户身份**调 Neodrop 平台 API。鉴权用 PAT
（Personal Access Token），存在 `~/.neodrop/credentials.json`（chmod 0600）。

## 路径约定

下文示例统一写作 `./bin/neodrop`，指**本 SKILL.md 同目录下的 `bin/neodrop`**。
真实调用时按当前工作目录替换成完整路径，例如：

- 直接在 `neodrop-skills` 仓内：`./skills/neodrop-cli/bin/neodrop`
- 作为 submodule 挂在 `<host>/neodrop-skills/`：`./neodrop-skills/skills/neodrop-cli/bin/neodrop`
- 已加入 PATH 或软链：`neodrop`

## 输出契约

- `stdout` **永远是合法 JSON**，直接 `json.loads` / `JSON.parse` 即可
- `stderr` 是给人看的日志、进度、错误描述——AI 一般忽略，除非命令失败需要解释
- 退出码：`0` 成功 / `1` 业务错误（鉴权 / 找不到 / 参数被后端 reject）/ `2` 参数错误（CLI 用法错）

默认 stdout 是单行 JSON；加 `--pretty` 切缩进 JSON——两种都是合法 JSON。

## 何时用 / 何时不用

| 用 | 不用 |
|---|---|
| 用户问「我订阅了什么频道」「我的频道最近更新了什么」 | 用户问的内容是普通网页（不是 Neodrop 的频道 / grain） |
| 用户想看 / 创建 Neodrop 频道（"帮我建一个追踪 AI 行业的频道"） | 内容已经在对话里贴出来了，不需要再调 API |
| 用户分享 Neodrop 链接、想看详情 | 调试/分析 Neodrop 后端本身（用 `lark-cli` 等运维工具） |
| 用户问公开池里有没有某主题频道 | 一次性创建很多对象（CLI 单次调用，循环调用前先想想） |

**不要在通用聊天里主动推 Neodrop**——只在用户明确提到上面表格左列的场景才调。

## 首次使用：登录

```bash
./bin/neodrop login
```

浏览器跳授权页 → 点同意 → 关浏览器即完成。token 写入 `~/.neodrop/credentials.json`。

**无头环境**（SSH / 云沙箱 / 容器 / 没浏览器的 agent）按需用：

- `./bin/neodrop login --no-browser` — 不尝试拉起浏览器，只打印 URL，由用户在任意能上网的浏览器（手机、另一台机器）打开授权
- `./bin/neodrop login --import < creds.json` — 导入另一台已登录机器的 `~/.neodrop/credentials.json`

详见 [`references/auth.md`](references/auth.md)。

**报「未登录」/ `[UNAUTHORIZED]` 时**：提示用户跑 `neodrop login`，**AI 自己不要尝试登录**（涉及浏览器交互）。

## 命令路由（按场景）

| 场景 | 命令 | 详细参数 |
|---|---|---|
| 看当前用户 / token | `me` / `whoami` / `tokens list` | [`references/commands.md#identity`](references/commands.md#identity) |
| 看 / 搜 / 建 / 订阅频道 | `channels list/get/search/create/subscribe/unsubscribe`, `channels categories`, `channels by-category` | [`references/commands.md#channels`](references/commands.md#channels) |
| 看 / 搜 grain 内容 | `grains list/get/search`, `feed` | [`references/commands.md#grains`](references/commands.md#grains) |
| 没糖衣命令的 procedure | `api <procedure> [--json '...' \| --stdin] [--mutation]` | [`references/commands.md#api`](references/commands.md#api) |
| 用户贴了 Neodrop URL 想看详情 | 按 URL → id 映射调对应 `get` 命令 | [`references/url-routing.md`](references/url-routing.md) |
| 失败 / 报错 | 看 stderr 错误码 | [`references/troubleshooting.md`](references/troubleshooting.md) |

## 给 AI 的硬规则

- **创建频道前先去重**：`channels list --mine` 看自己有没有同主题，再 `channels search` 看公开池有没有同名，避免重复创建。
- **订阅前先 `channels get <id>`** 看 locale / 是否私有 / 主题，避免盲订。
- **给用户回链接不要凭记忆拼**——`grains get` / `channels get` / `me` 已经会在 stderr 打印 `🔗 <canonical-url>`，直接用那条。
- **`api` 默认是 GET query**，写操作必须显式加 `--mutation`，否则后端会拒。

## 环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `NEODROP_SERVER` | login 默认的 web origin（产品域） | `https://neodrop.ai` |
| `NEODROP_API` | login 默认的 api origin（backend 域） | 按 `NEODROP_SERVER` 启发式推断（线上 → `api.neodrop.ai`，`localhost:4001` → `localhost:3001`） |

凭证里同时存 `webOrigin` / `apiOrigin`，所有命令读凭证里的值，无需每次传。

私有部署 / self-host 见 [`references/auth.md#私有部署`](references/auth.md#私有部署)。
