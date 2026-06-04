---
name: neodrop
description: 在 Neodrop（neodrop.ai）平台上代当前用户查询和操作频道 / Grain 内容 / 个人 feed——创建频道、订阅 / 取消订阅、搜索公开频道与内容、看分类、查自己拥有的频道、读 grain 详情。**只要用户提到 Neodrop、neodrop.ai、「我的频道」、「订阅了什么」、「grain」「grains」（Neodrop 上的内容单元）、「创建一个频道」「订阅这个频道」「搜频道」「公开内容」「公开 feed」「订阅 feed」等任意关键词或场景，就用本 skill 调 `./skills/neodrop-cli/bin/neodrop <command>`——不要走 fetch / curl / 自己写 HTTP，本 skill 已经处理好鉴权、JSON 序列化、错误码、locale 默认值等细节。**
---

# Neodrop skill

通过本地 `bin/neodrop` 命令以**当前登录用户身份**调 Neodrop 平台 API。鉴权用 PAT
（Personal Access Token），存在 `~/.neodrop/credentials.json`（chmod 0600）。

**核心契约**：

- `stdout` **永远是合法 JSON**，直接 `json.loads` / `JSON.parse` 即可
- `stderr` 是给人看的日志、进度、错误描述——AI 一般忽略，除非命令失败需要解释
- 退出码：`0` 成功 / `1` 业务错误（鉴权 / 找不到 / 参数被后端 reject）/ `2` 参数错误（CLI 用法错）

## 何时用 / 何时不用

| 用 | 不用 |
|---|---|
| 用户问「我订阅了什么频道」「我的频道最近更新了什么」 | 用户问的内容是普通网页（不是 Neodrop 的频道 / grain） |
| 用户想看 / 创建 Neodrop 频道（"帮我建一个追踪 AI 行业的频道"） | 内容已经在对话里贴出来了，不需要再调 API |
| 用户分享 Neodrop 链接、想看详情 | 调试/分析 Neodrop 后端本身（用 `lark-cli` 等运维工具） |
| 用户问公开池里有没有某主题频道（用 `channels search`） | 一次性创建很多对象（CLI 单次调用，循环调用前先想想） |

不要在通用聊天里主动推 Neodrop。**只在用户明确提到上面表格里左列的场景**才调。

## 安装与首次登录（用户需要做的事）

1. **要求 Python 3.9+**（macOS 12+ 自带；Linux 装 `python3` 即可；不需要 pip / venv，零依赖）
2. **首次登录（一次性）**：

   ```bash
   ./skills/neodrop-cli/bin/neodrop login
   ```

   浏览器跳授权页 → 点同意 → 关浏览器即完成。token 写入 `~/.neodrop/credentials.json`。

如果命令报「未登录」或 `[UNAUTHORIZED]` / `[FORBIDDEN]`，**提示用户运行 `neodrop login`，不要 AI 自己尝试登录**。

## 命令清单（按场景）

调用约定：本仓库根目录下用 **`./skills/neodrop-cli/bin/neodrop <command>`**。下面示例
为简洁省略前缀。

### 看自己（identity）

```bash
neodrop me                    # 当前用户信息（user.getMe）
neodrop whoami                # me + token 元信息
neodrop tokens list           # 我签发过的全部 PAT
```

### 频道（channels）

```bash
# 看
neodrop channels list --mine                       # 我拥有的频道
neodrop channels list --locale en --limit 20       # 公开频道分页
neodrop channels get <channelId>                   # 单频道详情（含 requirement.public）
neodrop channels categories                        # 全部分类
neodrop channels by-category tech --sort latest --limit 20

# 搜
neodrop channels search "AI 周报" --locale zh-cn --limit 10

# 写
neodrop channels create --name "AI 行业追踪" --description "..." --locale zh-cn
neodrop channels create --json '{"name":"X","locale":"en","type":"PRIVATE"}'
neodrop channels subscribe <channelId>
neodrop channels unsubscribe <channelId>
```

### Grain 内容

```bash
neodrop grains list --limit 10                     # 公开 feed
neodrop grains list --subscribed --limit 10        # 我订阅的（= neodrop feed --limit 10）
neodrop grains list --channel <channelId> --limit 10
neodrop grains get <grainId>
neodrop grains search "Apple Intelligence" --limit 10
```

### 兜底通道

糖衣命令没覆盖的 procedure，用 `neodrop api`：

```bash
neodrop api channel.update --json '{"id":"<chId>","name":"新名字"}' --mutation
neodrop api user.getLinkedAccounts                 # 任意 query
echo '{...}' | neodrop api channel.create --stdin --mutation
```

**默认是 GET query**，要走 mutation（写）必须加 `--mutation`。

## 用法约定（写给 AI）

- **创建频道前先 `channels list --mine`** 看自己有没有同主题频道，再 `channels search`
  看公开池里有没有同名频道，避免重复创建。
- **订阅前先 `channels get <id>`** 看频道详情（locale / 是否私有 / 主题），避免盲订。
- 用户给的是 Neodrop URL，按下面三条前端路由约定从 path 抽 id 后调对应详情命令：

  | URL | id 含义 | 调用 |
  |---|---|---|
  | `neodrop.ai/feed/<id>` | grain / post id | `grains get <id>` |
  | `neodrop.ai/channel/<id>` | channelId | `channels get <id>` |
  | `neodrop.ai/user/<id>` | userId | （无专用命令，按需用 `api user.getById --json`） |

  反向也成立：拿到 id 想给用户一条可点击链接时，**不要凭记忆拼**——`grains get`
  / `channels get` / `me` 已经会在 stderr 上打印一行 `🔗 <canonical-url>`，直接
  把那条引用给用户。不要拼 `/grain/<id>`（前端没这个路由）或猜其它路径。
- 命令失败时**先看 stderr 上的错误码**：`[UNAUTHORIZED]` 提示重 login；`[NOT_FOUND]`
  说明 id 或 slug 不对；`[BAD_REQUEST]` 通常是 input schema 不对（用 `--json` 时常见）。
- **复杂 mutation 用 `--json` 或 `--stdin`**——糖衣 flag 覆盖不全时退到 raw JSON：

  ```bash
  echo '{"name":"X","locale":"en"}' | ./skills/neodrop-cli/bin/neodrop api channel.create --stdin --mutation
  ```

## 故障排查

| 现象 | 处理 |
|---|---|
| `未登录` | 让用户跑 `./skills/neodrop-cli/bin/neodrop login` |
| `[UNAUTHORIZED]` | PAT 失效（过期 / 被撤销），让用户重新 login |
| `连接失败：[Errno 61] Connection refused` | 用户在本地 dev，提示设 `NEODROP_SERVER=http://localhost:4001` 并重新 login |
| `[NOT_FOUND]` | id / slug 不存在；先列出来核对 |
| `[BAD_REQUEST]` | input schema 不对；看 stderr 错误详情，对照 `neodrop <cmd> --help` |
| `未找到 python3` | 提示用户装 Python 3.9+；macOS 12+ 自带 |

## 环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `NEODROP_SERVER` | login 默认的 web origin（产品域） | `https://neodrop.ai` |
| `NEODROP_API` | login 默认的 api origin（backend 域） | 按 `NEODROP_SERVER` 启发式推断（线上是 `api.neodrop.ai`，本地 dev `localhost:4001` 推 `localhost:3001`） |

凭证里同时存 `webOrigin` / `apiOrigin`，所有命令读凭证里的值，无需每次传。
