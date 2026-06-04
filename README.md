# Neodrop Skills

让你的 AI agent（Claude Code、Cursor、Codex 等）以你的身份操作 [Neodrop](https://neodrop.ai)——查频道、看 grain、订阅 feed、创建频道，不需要离开你的编辑器。

本仓库收录所有官方 Neodrop AI skill。当前只有 `neodrop-cli`，将来会有更多。

## 目录约定

每个 skill 在 `skills/<skill-name>/` 一个独立目录，目录名即 skill 名（与 `SKILL.md` frontmatter 的 `name:` 完全一致，遵循 [Anthropic Skill 规范](https://docs.anthropic.com/claude/docs/build-skills)）：

```
neodrop-skills/
├── README.md              ← 你在看的这个，面向外部用户的总览
├── LICENSE                ← MIT
└── skills/                ← 所有 skill 在这里并列
    └── neodrop-cli/       ← 第一个 skill（将来可有 neodrop-pm/、neodrop-search/ 等）
        ├── SKILL.md       ← AI agent skill 描述 + 路由触发词
        ├── cli.py         ← Python 入口（stdlib only）
        ├── lib/           ← api / credentials / callback_server / ...
        └── bin/neodrop    ← bash 包装 → python3 cli.py
```

## 装这个 skill 能让 AI 做什么

- **看你的 Neodrop**：你问「我订阅了哪些频道」「我最近订阅频道更新了什么」，AI 直接调命令拿数据回答你
- **创建频道**：你说「帮我建一个追踪 AI 行业的频道」，AI 帮你拼好 input 直接 create
- **发现内容**：你说「Neodrop 上有没有讲量化交易的频道」，AI 帮你 `channels search` + `channels by-category` 综合查
- **管理订阅**：你说「订阅这个频道」「取消订阅 X」，AI 直接调

所有操作以**你的身份**进行（用 Personal Access Token 鉴权），等价于你自己登录 Neodrop 网页操作。

## 装机

### 1. 准备

| 要求 | 说明 |
|---|---|
| **Python 3.9+** | macOS 12+ 自带；Linux 一般有；Windows 装 [python.org](https://www.python.org/) 官方版 |
| **一个 Neodrop 账号** | 没注册的话先去 https://neodrop.ai 注册 |
| **一个 AI agent** | Claude Code / Cursor / Codex / 任何能跑 shell 的 agent |

> 不需要 Node、Bun、npm、pip——零额外依赖，全部 Python 标准库实现。

### 2. Clone 仓库

挑一个你愿意放代码的目录，把仓库 clone 下来：

```bash
git clone https://github.com/DeepLangAI/neodrop-skills.git
cd neodrop-skills
```

或者把它作为某个项目的 git submodule：

```bash
git submodule add https://github.com/DeepLangAI/neodrop-skills.git neodrop-skills
```

### 3. 登录（一次）

```bash
./skills/neodrop-cli/bin/neodrop login
```

浏览器会自动跳到授权页 → 点「同意」→ 关浏览器即完成。token 写入 `~/.neodrop/credentials.json`（chmod 0600，只有你能读）。

验证：

```bash
./skills/neodrop-cli/bin/neodrop whoami --pretty
```

应该看到你的用户信息和 token 元信息的 JSON。

### 4. 接入 AI agent

#### Claude Code

把整个 skill 目录软链到 Claude Code 的 skill 目录（**链整个目录而不是只链 SKILL.md**——这样 `bin/`、`cli.py` 等也一并就位，Claude Code 才能跑命令）。目录名必须与 `SKILL.md` 的 `name: neodrop-cli` 一致：

```bash
ln -sf "$PWD/skills/neodrop-cli" ~/.claude/skills/neodrop-cli
```

重启 Claude Code（或新开一个会话），AI 看到「我订阅了什么频道」之类的提问就会自动调本 skill。

可选——把 `bin/neodrop` 加进 Claude Code 的 Bash allowlist（避免每次确认权限），编辑 `~/.claude/settings.json`：

```json
{
  "permissions": {
    "allow": ["Bash(./skills/neodrop-cli/bin/neodrop:*)"]
  }
}
```

#### Cursor

把 SKILL.md 内容复制到 `.cursorrules` 或 Cursor 设置里的 system prompt 末尾，告诉 Cursor「需要操作 Neodrop 时调 `./skills/neodrop-cli/bin/neodrop ...`」。

#### 别的 agent

只要 agent 能跑 shell 命令、能读 stdout，就能用——把 `SKILL.md` 内容贴到 agent 的 system prompt / instructions 里，agent 会按照 skill 里的命令清单调用。

## 命令速查

```
元命令       login / logout / whoami / me
PAT 管理     tokens list / tokens revoke <id>
频道         channels list [--mine] / get <id> / create / subscribe <id> / unsubscribe <id>
             channels search <q> / categories / by-category <slug>
Grain        grains list [--subscribed | --channel <id>] / get <id> / search <q>
             feed（= grains list --subscribed）
兜底         api <procedure> [--json '...' | --stdin] [--mutation]
全局         --pretty（缩进 JSON 给人看，但依然是合法 JSON）
```

详细用法：`./skills/neodrop-cli/bin/neodrop --help` 或看 [`neodrop-cli/SKILL.md`](neodrop-cli/SKILL.md)。

## 输出契约

CLI 给 AI 设计：

| 通道 | 内容 |
|---|---|
| `stdout` | **永远是合法 JSON**，AI 直接 `json.loads` |
| `stderr` | 日志、进度、错误描述（给人看） |
| exit `0` | 成功 |
| exit `1` | 业务错误（鉴权失败 / 找不到 / 后端 reject 参数等） |
| exit `2` | 参数错误（CLI 用法不对） |

默认 stdout 是单行 JSON；加 `--pretty` 切换到缩进 JSON——**两种都是合法 JSON**，AI 不需要 flag 切换也能 parse。

## 数据安全

- token 是明文存在 `~/.neodrop/credentials.json`，文件权限自动设为 `0600`（只有当前用户能读）
- 同 GitHub PAT / npm token 一样，**请保护好你的 home 目录**——任何能读你 home 目录的进程都能拿到这个 token，等价于你的登录身份
- 默认 token 90 天过期；可在 [neodrop.ai/settings/cli-tokens](https://neodrop.ai/settings/cli-tokens) 网页随时撤销
- 本地丢 token：`./skills/neodrop-cli/bin/neodrop logout`（撤销 + 删本地凭证），然后 `login` 重发
- 担心 token 在文件里裸奔：每次用完都 logout；或定期 `tokens list` 检查并撤销陌生条目

## 私有部署 / Self-host

如果你跑的是私有 Neodrop 实例：

```bash
# 方式 A：环境变量
NEODROP_SERVER=https://your-neodrop.example.com ./skills/neodrop-cli/bin/neodrop login

# 方式 B：login flag
./skills/neodrop-cli/bin/neodrop login --server https://your-neodrop.example.com
```

默认 API 域按 web origin 启发式推断：`neodrop.ai` → `api.neodrop.ai`；`localhost:4001` → `localhost:3001`；其他默认与 web origin 同域（假设 backend 反代在 `/trpc/*`）。如果你的 api 域不同，传 `--api <url>` 或设 `NEODROP_API`。

## 反馈与贡献

- 用着不爽 / 命令不够用：[开 issue](https://github.com/DeepLangAI/neodrop-skills/issues)
- 想加新命令糖衣 / 新 skill：PR welcome

License: MIT
