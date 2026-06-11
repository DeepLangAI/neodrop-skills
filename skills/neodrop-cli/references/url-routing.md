# Neodrop URL → CLI 命令映射

用户贴 Neodrop 链接想看详情时，按下表从 path 抽 id 后调对应命令。

| URL 模式 | id 含义 | 调用 |
|---|---|---|
| `neodrop.ai/feed/<id>` | post id | `posts get <id>` |
| `neodrop.ai/channel/<id>` | channelId | `channels get <id>` |
| `neodrop.ai/user/<id>` | userId | 无专用糖衣命令，用 `api user.getById --json '{"id":"<id>"}'` |
| `neodrop.ai/discover` | 公开发现页 | `channels list --locale <l>` / `channels by-category <slug>` 按用户上下文挑 |
| `neodrop.ai/search?q=...` | 全站搜索 | `channels search "<q>"` + `posts search "<q>"` 综合呈现 |

## 反向：拿到 id 怎么给用户回链接

**不要凭记忆拼**——`posts get` / `channels get` / `me` 已经会在 stderr 打印一行 `🔗 <canonical-url>`，直接把那条引用给用户。

不要拼 `/grain/<id>`（前端没这个路由，老 `/grain` 已迁移到 `/feed`）或猜其它路径。如果命令没打印 canonical URL，按上表反推：

- channelId → `https://neodrop.ai/channel/<id>`
- postId → `https://neodrop.ai/feed/<id>`
- userId → `https://neodrop.ai/user/<id>`

私有部署的 host 看凭证里的 `webOrigin`（`neodrop whoami` 输出里有）。
