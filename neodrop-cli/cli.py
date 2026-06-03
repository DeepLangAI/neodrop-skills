#!/usr/bin/env python3
"""neodrop CLI 入口。AI agent 与人类共用——直接打 Neodrop tRPC HTTP 接口
（Bearer PAT 鉴权），不走 MCP。

调用：
  python3 cli.py <command> [args...]
  或通过 bin/neodrop 包装：./bin/neodrop <command> [args...]

输出：
  stdout = JSON（AI 直接 json.loads）；--pretty 切缩进 JSON 给人看
  stderr = 日志 / 进度 / 错误描述
退出码：0 成功 / 1 业务错误 / 2 参数错误
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import socket
import sys
import time
from pathlib import Path
from typing import Any, Optional

# 让 `python3 cli.py` 直接跑（不需要 PYTHONPATH 配置）
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.api import ApiError, trpc_mutation, trpc_query  # noqa: E402
from lib.browser import open_in_browser  # noqa: E402
from lib.callback_server import generate_state, start_callback_server  # noqa: E402
from lib.credentials import (  # noqa: E402
    Credentials,
    clear_credentials,
    credentials_path,
    read_credentials,
    require_credentials,
    write_credentials,
)
from lib.origins import infer_api_origin  # noqa: E402
from lib.output import emit, note, set_pretty  # noqa: E402

DEFAULT_SERVER = os.environ.get("NEODROP_SERVER", "https://neodrop.ai")
ENV_API_OVERRIDE = os.environ.get("NEODROP_API")


def _detect_client_name() -> str:
    """客户端标识——授权页和 settings/cli-tokens 上显示给用户辨认。"""
    term = os.environ.get("TERM_PROGRAM", "")
    host = socket.gethostname() or platform.node() or "host"
    if os.environ.get("CLAUDECODE"):
        return f"Claude Code @ {host}"
    if os.environ.get("CURSOR_TRACE_ID"):
        return f"Cursor @ {host}"
    if term == "vscode":
        return f"VS Code @ {host}"
    if term == "WarpTerminal":
        return f"Warp @ {host}"
    return f"neodrop-cli @ {host}"


def _authed_ctx() -> tuple[str, str, Credentials]:
    creds = require_credentials()
    return creds["apiOrigin"], creds["token"], creds


def _read_stdin() -> str:
    return sys.stdin.read()


def _load_input_from_flags(args: argparse.Namespace) -> Optional[Any]:
    """`--json '<input>'` / `--stdin` 二选一，互斥（argparse 已校验）。"""
    if getattr(args, "json", None):
        try:
            return json.loads(args.json)
        except json.JSONDecodeError as e:
            raise SystemExit(f"--json 解析失败：{e}")
    if getattr(args, "stdin", False):
        return json.loads(_read_stdin())
    return None


# ---- 元命令 -----------------------------------------------------------


def cmd_login(args: argparse.Namespace) -> None:
    web_origin: str = args.server
    # --api 显式 > NEODROP_API env > 启发式推断
    api_origin: str = args.api or ENV_API_OVERRIDE or infer_api_origin(web_origin)
    name: str = args.name
    port: int = args.port

    state = generate_state()
    handle = start_callback_server(expected_state=state, port=port)
    auth_url = (
        f"{web_origin.rstrip('/')}/cli-auth?"
        + "&".join(
            [
                f"callback={_url_encode(handle.url)}",
                f"state={state}",
                f"name={_url_encode(name)}",
            ]
        )
    )

    note(f"web   = {web_origin}")
    note(f"api   = {api_origin}")
    note(f"监听   {handle.url}")
    note(f"授权 URL：{auth_url}")
    note("如果浏览器没自动打开，复制上面 URL 手动访问。")
    open_in_browser(auth_url)

    try:
        result = handle.await_result(timeout_seconds=600)
    except Exception:
        handle.close()
        raise

    me = trpc_query({"apiOrigin": api_origin, "token": result["token"]}, "user.getMe")
    tokens = trpc_query({"apiOrigin": api_origin, "token": result["token"]}, "cliToken.list") or []
    mine = sorted(
        [t for t in tokens if not t.get("revokedAt")],
        key=lambda x: x["createdAt"],
        reverse=True,
    )
    if not mine:
        raise RuntimeError("授权返回 ok 但 cliToken.list 找不到刚签发的 token，请重试")
    mine_first = mine[0]

    write_credentials(
        {
            "webOrigin": web_origin,
            "apiOrigin": api_origin,
            "token": result["token"],
            "tokenId": mine_first["id"],
            "name": mine_first["name"],
            "expiresAt": result["expiresAt"],
            "createdAt": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
    )
    note(f"✅ 登录成功：{me.get('email') or me.get('id') or '<unknown>'}")
    note(f"   credentials = {credentials_path()}")
    emit(
        {
            "ok": True,
            "webOrigin": web_origin,
            "apiOrigin": api_origin,
            "user": me,
            "tokenId": mine_first["id"],
            "tokenName": mine_first["name"],
            "expiresAt": result["expiresAt"],
        }
    )


def _url_encode(s: str) -> str:
    from urllib.parse import quote

    return quote(s, safe="")


def cmd_logout(_args: argparse.Namespace) -> None:
    creds = read_credentials()
    if creds is None:
        note("未登录，无需登出。")
        emit({"ok": True, "alreadyLoggedOut": True})
        return
    revoked = False
    try:
        trpc_mutation(
            {"apiOrigin": creds["apiOrigin"], "token": creds["token"]},
            "cliToken.revoke",
            {"id": creds["tokenId"]},
        )
        revoked = True
        note(f"✅ 已撤销 {creds['tokenId']}")
    except Exception as e:  # noqa: BLE001
        note(f"⚠ 撤销失败（继续清本地凭证）：{e}")
    clear_credentials()
    emit({"ok": True, "revoked": revoked})


def cmd_whoami(_args: argparse.Namespace) -> None:
    api_origin, token, creds = _authed_ctx()
    me = trpc_query({"apiOrigin": api_origin, "token": token}, "user.getMe")
    emit(
        {
            "webOrigin": creds["webOrigin"],
            "apiOrigin": api_origin,
            "tokenName": creds["name"],
            "tokenId": creds["tokenId"],
            "expiresAt": creds["expiresAt"],
            "user": me,
        }
    )


def cmd_tokens_list(_args: argparse.Namespace) -> None:
    api_origin, token, _ = _authed_ctx()
    emit(trpc_query({"apiOrigin": api_origin, "token": token}, "cliToken.list"))


def cmd_tokens_revoke(args: argparse.Namespace) -> None:
    api_origin, token, creds = _authed_ctx()
    r = trpc_mutation(
        {"apiOrigin": api_origin, "token": token},
        "cliToken.revoke",
        {"id": args.id},
    )
    if args.id == creds["tokenId"]:
        clear_credentials()
        note("（撤销的是本机当前 token，已清除本地凭证。需要重新 neodrop login。）")
    emit(r)


# ---- 业务命令 ---------------------------------------------------------


def cmd_me(_args: argparse.Namespace) -> None:
    api_origin, token, _ = _authed_ctx()
    emit(trpc_query({"apiOrigin": api_origin, "token": token}, "user.getMe"))


def cmd_channels_list(args: argparse.Namespace) -> None:
    api_origin, token, _ = _authed_ctx()
    if args.mine:
        emit(trpc_query({"apiOrigin": api_origin, "token": token}, "channel.getMyChannels"))
        return
    payload: dict = {"limit": args.limit, "locale": args.locale}
    if args.cursor:
        payload["cursor"] = args.cursor
    emit(trpc_query({"apiOrigin": api_origin, "token": token}, "channel.list", payload))


def cmd_channels_get(args: argparse.Namespace) -> None:
    api_origin, token, _ = _authed_ctx()
    emit(trpc_query({"apiOrigin": api_origin, "token": token}, "channel.getById", {"id": args.id}))


def cmd_channels_create(args: argparse.Namespace) -> None:
    api_origin, token, _ = _authed_ctx()
    input_value = _load_input_from_flags(args)
    if input_value is None:
        if not args.name:
            raise SystemExit(
                "用法：neodrop channels create --name <X> [--description <Y>] "
                "[--type PUBLIC|PRIVATE] [--locale zh-cn]\n"
                "或：neodrop channels create --json '{\"name\":\"X\",\"locale\":\"zh-cn\"}'\n"
                "或：neodrop channels create --stdin"
            )
        input_value = {"name": args.name}
        if args.description:
            input_value["description"] = args.description
        if args.type:
            input_value["type"] = args.type
        if args.locale:
            input_value["locale"] = args.locale
    emit(trpc_mutation({"apiOrigin": api_origin, "token": token}, "channel.create", input_value))


def _parse_config_kv(items: Optional[list[str]]) -> list[dict[str, str]]:
    """`--config "label=value"` 列表 → agentTask.create 的 config 入参。

    Web onboarding 表单的等价物：每条 {label, value} 会被 creation agent
    作为 initialUserText 的一部分消费（拼接成 "label: value\\n..."），
    用来推导 requirement.json 里的 schedule / carrier / whitelist 等。
    """
    if not items:
        return []
    out: list[dict[str, str]] = []
    for raw in items:
        if "=" not in raw:
            raise SystemExit(
                f"--config 必须是 label=value 形式，收到：{raw!r}（缺 '='）"
            )
        label, value = raw.split("=", 1)
        label = label.strip()
        value = value.strip()
        if not label or not value:
            raise SystemExit(f"--config 的 label 和 value 都不能为空：{raw!r}")
        out.append({"label": label, "value": value})
    return out


def cmd_channels_setup(args: argparse.Namespace) -> None:
    """一键创建 + 激活：调 agentTask.create 启 CHANNEL_CREATION agent。

    后端 procedure 原子地：建 DRAFT 频道 → 建 AI 会话 → 入 BullMQ 任务。
    creation agent 起来后自己写 requirement.json + 推 ACTIVE，完全不依赖
    Web 端的 onboarding UI。
    """
    api_origin, token, _ = _authed_ctx()

    payload: dict[str, Any] = {
        "channelName": args.name,
        "locale": args.locale,
        "timeZoneOffset": args.timezone,
    }
    if args.description:
        payload["channelDescription"] = args.description
        payload["description"] = args.description  # creation agent 读这个作为初始 user 消息
    config_items = _parse_config_kv(args.config)
    if config_items:
        payload["config"] = config_items
    if args.variant:
        payload["agentChainVariant"] = args.variant
    if args.connector_grants:
        payload["connectorGrantIds"] = args.connector_grants

    note(f"⏳ 提交 CHANNEL_CREATION 任务：{args.name}")
    task = trpc_mutation(
        {"apiOrigin": api_origin, "token": token},
        "agentTask.create",
        payload,
    )

    task_id = task.get("id")
    channel_id = task.get("channelId")
    note(f"✅ 任务已入队：taskId={task_id} channelId={channel_id}")
    note("   creation agent 后台跑中，会自动写 requirement.json 并推到 ACTIVE。")

    if args.watch:
        note(f"   --watch 阻塞轮询直到任务终态（超时 {args.timeout}s）...")
        final = _watch_task(api_origin, token, task_id, timeout_sec=args.timeout)
        emit({"task": final, "channelId": channel_id})
        return

    emit({"task": task, "channelId": channel_id, "taskId": task_id})


def cmd_channels_subscribe(args: argparse.Namespace) -> None:
    api_origin, token, _ = _authed_ctx()
    emit(
        trpc_mutation(
            {"apiOrigin": api_origin, "token": token},
            "channel.subscribe",
            {"channelId": args.id},
        )
    )


def cmd_channels_unsubscribe(args: argparse.Namespace) -> None:
    api_origin, token, _ = _authed_ctx()
    emit(
        trpc_mutation(
            {"apiOrigin": api_origin, "token": token},
            "channel.unsubscribe",
            {"channelId": args.id},
        )
    )


def cmd_channels_search(args: argparse.Namespace) -> None:
    api_origin, token, _ = _authed_ctx()
    payload: dict = {"query": args.query}
    if args.limit is not None:
        payload["limit"] = args.limit
    if args.locale:
        payload["locale"] = args.locale
    if args.strict:
        payload["strictLocale"] = True
    emit(trpc_query({"apiOrigin": api_origin, "token": token}, "channel.searchPublic", payload))


def cmd_channels_categories(_args: argparse.Namespace) -> None:
    api_origin, token, _ = _authed_ctx()
    emit(trpc_query({"apiOrigin": api_origin, "token": token}, "channel.getCategories"))


def cmd_channels_by_category(args: argparse.Namespace) -> None:
    api_origin, token, _ = _authed_ctx()
    payload: dict = {"categorySlug": args.slug}
    if args.limit is not None:
        payload["limit"] = args.limit
    if args.cursor:
        payload["cursor"] = args.cursor
    if args.locale:
        payload["locale"] = args.locale
    if args.sort:
        payload["sortBy"] = args.sort
    emit(trpc_query({"apiOrigin": api_origin, "token": token}, "channel.listByCategory", payload))


def cmd_posts_list(args: argparse.Namespace) -> None:
    api_origin, token, _ = _authed_ctx()
    if args.subscribed:
        payload: dict = {"limit": args.limit}
        if args.cursor:
            payload["cursor"] = args.cursor
        if args.channel:
            payload["channelId"] = args.channel
        emit(trpc_query({"apiOrigin": api_origin, "token": token}, "grain.listSubscribed", payload))
        return
    if args.channel:
        payload = {"channelId": args.channel, "limit": args.limit}
        if args.cursor:
            payload["cursor"] = args.cursor
        emit(trpc_query({"apiOrigin": api_origin, "token": token}, "grain.list", payload))
        return
    # 否则 listRecent（公开 feed）
    payload = {"limit": args.limit}
    if args.cursor:
        payload["cursor"] = args.cursor
    if args.locale:
        payload["locale"] = args.locale
    emit(trpc_query({"apiOrigin": api_origin, "token": token}, "grain.listRecent", payload))


def cmd_posts_get(args: argparse.Namespace) -> None:
    api_origin, token, _ = _authed_ctx()
    emit(trpc_query({"apiOrigin": api_origin, "token": token}, "grain.getById", {"id": args.id}))


def cmd_posts_search(args: argparse.Namespace) -> None:
    api_origin, token, _ = _authed_ctx()
    payload: dict = {"query": args.query}
    if args.limit is not None:
        payload["limit"] = args.limit
    if args.locale:
        payload["locale"] = args.locale
    if args.strict:
        payload["strictLocale"] = True
    emit(trpc_query({"apiOrigin": api_origin, "token": token}, "grain.searchPublic", payload))


def cmd_feed(args: argparse.Namespace) -> None:
    api_origin, token, _ = _authed_ctx()
    payload: dict = {"limit": args.limit}
    if args.cursor:
        payload["cursor"] = args.cursor
    emit(trpc_query({"apiOrigin": api_origin, "token": token}, "grain.listSubscribed", payload))


# ---- Agent 任务 -------------------------------------------------------

_TASK_TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


def _watch_task(
    api_origin: str,
    token: str,
    task_id: str,
    *,
    timeout_sec: int = 600,
    poll_interval_sec: float = 3.0,
) -> dict:
    """轮询 agentTask.getById 直到 status 落到终态或超时。

    每次状态变化或检测到首条内容时打 stderr 一行进度——给人类看；
    AI 只需要在 watch 结束时拿到最终 task 对象（含 channelId / status / 已产出内容数）。
    """
    deadline = time.monotonic() + timeout_sec
    last_status: Optional[str] = None
    last_post_count: int = -1

    while True:
        try:
            task = trpc_query(
                {"apiOrigin": api_origin, "token": token},
                "agentTask.getById",
                {"id": task_id},
            )
        except Exception as e:  # noqa: BLE001 — 轮询期 TLS / 网络抖动忽略，等下一轮
            note(f"  ⚠ 轮询异常忽略：{e}")
            if time.monotonic() >= deadline:
                raise
            time.sleep(poll_interval_sec)
            continue
        status = task.get("status")
        # 注意：后端 Prisma 模型仍叫 `grains`，这里只是把 CLI 层的人面术语换成
        # "post / 内容"，字段路径保持与 API 真相源一致，避免读取漂移。
        post_count = (task.get("channel") or {}).get("_count", {}).get("grains", 0) or 0

        if status != last_status:
            note(f"  [{status}] task={task_id}")
            last_status = status
        if post_count != last_post_count and post_count > 0:
            note(f"  📰 已产出 {post_count} 条内容")
            last_post_count = post_count

        if status in _TASK_TERMINAL:
            return task

        if time.monotonic() >= deadline:
            note(f"⚠ 轮询超时（{timeout_sec}s）；任务仍在跑，可后续 `tasks get {task_id}`")
            return task

        time.sleep(poll_interval_sec)


def cmd_tasks_get(args: argparse.Namespace) -> None:
    api_origin, token, _ = _authed_ctx()
    emit(trpc_query({"apiOrigin": api_origin, "token": token}, "agentTask.getById", {"id": args.id}))


def cmd_tasks_watch(args: argparse.Namespace) -> None:
    api_origin, token, _ = _authed_ctx()
    final = _watch_task(api_origin, token, args.id, timeout_sec=args.timeout)
    emit(final)


def cmd_tasks_list(args: argparse.Namespace) -> None:
    api_origin, token, _ = _authed_ctx()
    payload: dict[str, Any] = {"limit": args.limit}
    if args.cursor:
        payload["cursor"] = args.cursor
    emit(trpc_query({"apiOrigin": api_origin, "token": token}, "agentTask.list", payload))


# ---- 兜底通道 ---------------------------------------------------------


def cmd_api(args: argparse.Namespace) -> None:
    api_origin, token, _ = _authed_ctx()
    input_value = _load_input_from_flags(args)
    if args.mutation:
        emit(trpc_mutation({"apiOrigin": api_origin, "token": token}, args.procedure, input_value))
    else:
        emit(trpc_query({"apiOrigin": api_origin, "token": token}, args.procedure, input_value))


# ---- 路由 -------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    # 共享 flag——argparse 的 subparser 默认不继承父 parser 的 flag，所以
    # `neodrop whoami --pretty` 会报「unrecognized arguments」。用 parents=[shared]
    # 让每个 subcommand 都接受 --pretty，AI 不用纠结 `--pretty` 该放哪。
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--pretty", action="store_true", help="缩进 JSON 输出（依然是合法 JSON）")

    def add(parent: Any, name: str, **kwargs: Any) -> argparse.ArgumentParser:
        """add_parser 包装：自动注入 parents=[shared]，所有 leaf 都接受 --pretty。"""
        return parent.add_parser(name, parents=[shared], **kwargs)

    parser = argparse.ArgumentParser(
        prog="neodrop",
        description="Neodrop CLI — AI agent 与人类共用，stdout = JSON。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[shared],
    )
    sub = parser.add_subparsers(dest="cmd", metavar="<command>", required=True)

    # login
    p = add(sub, "login", help="浏览器 OAuth-like 授权，把 PAT 写到 ~/.neodrop/credentials.json")
    p.add_argument("--server", default=DEFAULT_SERVER, help=f"web origin（默认 {DEFAULT_SERVER}，NEODROP_SERVER 覆盖）")
    p.add_argument(
        "--api",
        default=None,
        help="api origin（不传按 --server 启发式推断：neodrop.ai → api.neodrop.ai；NEODROP_API 覆盖）",
    )
    p.add_argument("--name", default=_detect_client_name(), help="客户端名（授权页显示给用户辨认）")
    p.add_argument("--port", type=int, default=0, help="本地 callback server 端口（0=随机）")
    p.set_defaults(func=cmd_login)

    add(sub, "logout", help="撤销 PAT + 删本地凭证").set_defaults(func=cmd_logout)
    add(sub, "whoami", help="显示当前 token + user 信息").set_defaults(func=cmd_whoami)
    add(sub, "me", help="当前用户信息（user.getMe）").set_defaults(func=cmd_me)

    # tokens
    tokens_sub = add(sub, "tokens", help="管理已签发的 PAT").add_subparsers(dest="sub", required=True)
    add(tokens_sub, "list", help="列出所有 PAT").set_defaults(func=cmd_tokens_list)
    pr = add(tokens_sub, "revoke", help="撤销指定 PAT")
    pr.add_argument("id", help="PAT id")
    pr.set_defaults(func=cmd_tokens_revoke)

    # channels
    ch = add(sub, "channels", help="频道操作").add_subparsers(dest="sub", required=True)

    pl = add(ch, "list", help="列频道：默认公开池；--mine 列我拥有的")
    pl.add_argument("--mine", action="store_true")
    pl.add_argument("--limit", type=int, default=20)
    pl.add_argument("--cursor", default=None)
    pl.add_argument("--locale", default="en", help="locale 缺省 en，与 Web 默认 locale 一致")
    pl.set_defaults(func=cmd_channels_list)

    pg = add(ch, "get", help="单频道详情")
    pg.add_argument("id")
    pg.set_defaults(func=cmd_channels_get)

    pc = add(
        ch,
        "create",
        help="【低层】只建 DRAFT 空壳频道（无 schedule / carrier / requirement），日常用 setup",
    )
    pc.add_argument("--name", default=None)
    pc.add_argument("--description", default=None)
    pc.add_argument("--type", choices=["PUBLIC", "PRIVATE"], default=None)
    pc.add_argument("--locale", default=None)
    g = pc.add_mutually_exclusive_group()
    g.add_argument("--json", default=None, help="原始 JSON 输入（复杂场景）")
    g.add_argument("--stdin", action="store_true", help="从 stdin 读 JSON")
    pc.set_defaults(func=cmd_channels_create)

    pset = add(
        ch,
        "setup",
        help="【推荐】一键创建并激活：起 CHANNEL_CREATION agent 自动写配置 + 推 ACTIVE，无需 Web",
    )
    pset.add_argument("--name", required=True, help="频道名（必填）")
    pset.add_argument(
        "--description",
        default=None,
        help="自然语言完整需求描述；作为 agent 初始 user 消息消费（推荐填）",
    )
    pset.add_argument("--locale", default="zh-cn", help="频道 locale，缺省 zh-cn")
    pset.add_argument(
        "--timezone",
        type=int,
        default=8,
        help="时区小时偏移（-12 到 14，缺省 8 = 东八区）",
    )
    pset.add_argument(
        "--variant",
        choices=["lite", "standard"],
        default=None,
        help="agent chain 模式，缺省后端定（一般 lite）",
    )
    pset.add_argument(
        "--config",
        action="append",
        default=None,
        metavar="LABEL=VALUE",
        help="表单字段（可多次）；等价于 Web onboarding 的逐项回答，如 --config '推送频率=每天 08:00'",
    )
    pset.add_argument(
        "--connector-grants",
        action="append",
        default=None,
        metavar="CONNECTOR_ID",
        help="授权新频道访问的 connector id（可多次）",
    )
    pset.add_argument(
        "--watch",
        action="store_true",
        help="阻塞轮询任务到终态（COMPLETED / FAILED / CANCELLED）再返回",
    )
    pset.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="--watch 的轮询超时秒数（缺省 600 = 10 分钟）",
    )
    pset.set_defaults(func=cmd_channels_setup)

    ps = add(ch, "subscribe", help="订阅频道")
    ps.add_argument("id", help="channelId")
    ps.set_defaults(func=cmd_channels_subscribe)

    pu = add(ch, "unsubscribe", help="取消订阅频道")
    pu.add_argument("id", help="channelId")
    pu.set_defaults(func=cmd_channels_unsubscribe)

    psearch = add(ch, "search", help="按 query 搜公开频道")
    psearch.add_argument("query")
    psearch.add_argument("--limit", type=int, default=None)
    psearch.add_argument("--locale", default=None)
    psearch.add_argument("--strict", action="store_true", help="strictLocale=true（locale 必填）")
    psearch.set_defaults(func=cmd_channels_search)

    add(ch, "categories", help="全部分类").set_defaults(func=cmd_channels_categories)

    pbc = add(ch, "by-category", help="按分类列频道")
    pbc.add_argument("slug")
    pbc.add_argument("--limit", type=int, default=None)
    pbc.add_argument("--cursor", default=None)
    pbc.add_argument("--locale", default=None)
    pbc.add_argument("--sort", choices=["latest", "popular"], default=None)
    pbc.set_defaults(func=cmd_channels_by_category)

    # posts —— 单条内容（频道产出的一篇文章 / 一组图文 / 一条音频等）
    # 历史名 `grains`：保留作 hidden alias，避免老调用方一次性炸——SKILL.md / 用户面
    # 一律推 `posts`。
    def register_posts_group(group_name: str, *, visible: bool) -> None:
        kwargs = {"help": "单条内容操作（频道产出的 post）"} if visible else {}
        gr = add(sub, group_name, **kwargs).add_subparsers(dest="sub", required=True)

        gl = add(gr, "list", help="列 post：默认公开 feed；--subscribed 我订阅的；--channel 指定频道")
        gl.add_argument("--channel", default=None)
        gl.add_argument("--subscribed", action="store_true")
        gl.add_argument("--limit", type=int, default=20)
        gl.add_argument("--cursor", default=None)
        gl.add_argument("--locale", default=None)
        gl.set_defaults(func=cmd_posts_list)

        gg = add(gr, "get", help="单 post 详情")
        gg.add_argument("id")
        gg.set_defaults(func=cmd_posts_get)

        gs = add(gr, "search", help="按 query 搜公开 post")
        gs.add_argument("query")
        gs.add_argument("--limit", type=int, default=None)
        gs.add_argument("--locale", default=None)
        gs.add_argument("--strict", action="store_true")
        gs.set_defaults(func=cmd_posts_search)

    register_posts_group("posts", visible=True)
    register_posts_group("grains", visible=False)  # 历史 alias，不出现在 --help

    # tasks
    tk = add(sub, "tasks", help="Agent 任务（频道创建任务等）").add_subparsers(dest="sub", required=True)
    tg = add(tk, "get", help="按 id 查任务详情（含 status / channelId / 已产出内容数）")
    tg.add_argument("id", help="taskId")
    tg.set_defaults(func=cmd_tasks_get)
    tw = add(tk, "watch", help="阻塞轮询任务到终态")
    tw.add_argument("id", help="taskId")
    tw.add_argument("--timeout", type=int, default=600, help="超时秒数，缺省 600")
    tw.set_defaults(func=cmd_tasks_watch)
    tl = add(tk, "list", help="列出我的任务")
    tl.add_argument("--limit", type=int, default=20)
    tl.add_argument("--cursor", default=None)
    tl.set_defaults(func=cmd_tasks_list)

    # feed
    pf = add(sub, "feed", help="我订阅的内容流（= posts list --subscribed）")
    pf.add_argument("--limit", type=int, default=20)
    pf.add_argument("--cursor", default=None)
    pf.set_defaults(func=cmd_feed)

    # api 兜底
    pa = add(sub, "api", help="任意 tRPC procedure 直调（糖衣没覆盖时用）")
    pa.add_argument("procedure", help="如 channel.update / grain.remove（后端 procedure 名仍走 grain.*）")
    g = pa.add_mutually_exclusive_group()
    g.add_argument("--json", default=None)
    g.add_argument("--stdin", action="store_true")
    pa.add_argument("--mutation", action="store_true", help="走 POST mutation；默认 GET query")
    pa.set_defaults(func=cmd_api)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    set_pretty(bool(getattr(args, "pretty", False)))

    try:
        args.func(args)
    except SystemExit:
        raise
    except ApiError as e:
        # tRPC 业务错（401 / 404 / BAD_REQUEST 等）
        note(f"✗ {e}")
        return 1
    except KeyboardInterrupt:
        note("\n中断")
        return 130
    except Exception as e:  # noqa: BLE001
        note(f"✗ {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
