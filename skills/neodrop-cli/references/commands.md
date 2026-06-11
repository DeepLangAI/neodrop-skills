# neodrop-cli 命令清单

> 调用约定：用 `npx neodrop <command>`（参见 [SKILL.md 调用方式](../SKILL.md#调用方式)）。
> 下面示例直接写 `neodrop <command>`。

## identity

```bash
neodrop me                    # 当前用户信息（user.getMe）
neodrop whoami                # me + token 元信息（凭证 + 过期时间）
neodrop tokens list           # 我签发过的全部 PAT
neodrop tokens revoke <id>    # 撤销指定 PAT；若撤销的是本机 token 会顺手清掉本地凭证
```

## channels

### 看 / 搜

```bash
neodrop channels list --mine                       # 我拥有的频道
neodrop channels list --locale en --limit 20       # 公开频道分页
neodrop channels get <channelId>                   # 单频道详情（含 requirement.public）
neodrop channels categories                        # 全部分类
neodrop channels by-category tech --sort latest --limit 20

neodrop channels search "AI 周报" --locale zh-cn --limit 10
```

### 写

```bash
neodrop channels create --name "AI 行业追踪" --description "..." --locale zh-cn
neodrop channels create --json '{"name":"X","locale":"en","type":"PRIVATE"}'
neodrop channels subscribe <channelId>
neodrop channels unsubscribe <channelId>
```

**默认 locale**：`channels list`、`channels search`、`channels by-category` 若不传 `--locale`，按当前用户登录 locale 走（后端默认）。需要跨 locale 查公开池时显式传 `--locale en` 等。

## posts

> 内容单元在 Neodrop 上叫 **post**。旧命令名 `grains` 仍作为向后兼容别名保留，新代码统一用 `posts`。

```bash
neodrop posts list --limit 10                      # 公开 feed
neodrop posts list --subscribed --limit 10         # 我订阅的（= neodrop feed --limit 10）
neodrop posts list --channel <channelId> --limit 10
neodrop posts get <postId>
neodrop posts search "Apple Intelligence" --limit 10

neodrop feed --limit 10                            # 等价 posts list --subscribed
```

## api

糖衣命令没覆盖的 tRPC procedure，用 `api` 兜底：

```bash
neodrop api channel.update --json '{"id":"<chId>","name":"新名字"}' --mutation
neodrop api user.getLinkedAccounts                 # 任意 query
echo '{...}' | neodrop api channel.create --stdin --mutation
```

- **默认是 GET query**，要走 mutation（写）必须显式加 `--mutation`，否则后端按 query 路由会拒
- 复杂 input 用 `--json '...'` 直传 JSON，或 `--stdin` 从标准输入读（适合 heredoc / pipeline）
- 想知道 procedure 全名，看主仓 `packages/backend/src/api/trpc/routers.ts` 或在后端 dev 模式下 `curl /api/trpc/<router>.<procedure>?input=...` 试探

## 全局

```bash
neodrop --pretty <cmd>          # 缩进 JSON 给人看（仍是合法 JSON）
neodrop <cmd> --help            # 看子命令参数
```
