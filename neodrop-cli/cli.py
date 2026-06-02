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


def cmd_grains_list(args: argparse.Namespace) -> None:
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


def cmd_grains_get(args: argparse.Namespace) -> None:
    api_origin, token, _ = _authed_ctx()
    emit(trpc_query({"apiOrigin": api_origin, "token": token}, "grain.getById", {"id": args.id}))


def cmd_grains_search(args: argparse.Namespace) -> None:
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
    parser = argparse.ArgumentParser(
        prog="neodrop",
        description="Neodrop CLI — AI agent 与人类共用，stdout = JSON。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--pretty", action="store_true", help="缩进 JSON 输出（依然是合法 JSON）")
    sub = parser.add_subparsers(dest="cmd", metavar="<command>", required=True)

    # login
    p = sub.add_parser("login", help="浏览器 OAuth-like 授权，把 PAT 写到 ~/.neodrop/credentials.json")
    p.add_argument("--server", default=DEFAULT_SERVER, help=f"web origin（默认 {DEFAULT_SERVER}，NEODROP_SERVER 覆盖）")
    p.add_argument(
        "--api",
        default=None,
        help="api origin（不传按 --server 启发式推断：neodrop.ai → api.neodrop.ai；NEODROP_API 覆盖）",
    )
    p.add_argument("--name", default=_detect_client_name(), help="客户端名（授权页显示给用户辨认）")
    p.add_argument("--port", type=int, default=0, help="本地 callback server 端口（0=随机）")
    p.set_defaults(func=cmd_login)

    sub.add_parser("logout", help="撤销 PAT + 删本地凭证").set_defaults(func=cmd_logout)
    sub.add_parser("whoami", help="显示当前 token + user 信息").set_defaults(func=cmd_whoami)
    sub.add_parser("me", help="当前用户信息（user.getMe）").set_defaults(func=cmd_me)

    # tokens
    p = sub.add_parser("tokens", help="管理已签发的 PAT").add_subparsers(dest="sub", required=True)
    p.add_parser("list", help="列出所有 PAT").set_defaults(func=cmd_tokens_list)
    pr = p.add_parser("revoke", help="撤销指定 PAT")
    pr.add_argument("id", help="PAT id")
    pr.set_defaults(func=cmd_tokens_revoke)

    # channels
    ch = sub.add_parser("channels", help="频道操作").add_subparsers(dest="sub", required=True)

    pl = ch.add_parser("list", help="列频道：默认公开池；--mine 列我拥有的")
    pl.add_argument("--mine", action="store_true")
    pl.add_argument("--limit", type=int, default=20)
    pl.add_argument("--cursor", default=None)
    pl.add_argument("--locale", default="en", help="locale 缺省 en，与 Web 默认 locale 一致")
    pl.set_defaults(func=cmd_channels_list)

    pg = ch.add_parser("get", help="单频道详情")
    pg.add_argument("id")
    pg.set_defaults(func=cmd_channels_get)

    pc = ch.add_parser("create", help="创建频道")
    pc.add_argument("--name", default=None)
    pc.add_argument("--description", default=None)
    pc.add_argument("--type", choices=["PUBLIC", "PRIVATE"], default=None)
    pc.add_argument("--locale", default=None)
    g = pc.add_mutually_exclusive_group()
    g.add_argument("--json", default=None, help="原始 JSON 输入（复杂场景）")
    g.add_argument("--stdin", action="store_true", help="从 stdin 读 JSON")
    pc.set_defaults(func=cmd_channels_create)

    ps = ch.add_parser("subscribe", help="订阅频道")
    ps.add_argument("id", help="channelId")
    ps.set_defaults(func=cmd_channels_subscribe)

    pu = ch.add_parser("unsubscribe", help="取消订阅频道")
    pu.add_argument("id", help="channelId")
    pu.set_defaults(func=cmd_channels_unsubscribe)

    psearch = ch.add_parser("search", help="按 query 搜公开频道")
    psearch.add_argument("query")
    psearch.add_argument("--limit", type=int, default=None)
    psearch.add_argument("--locale", default=None)
    psearch.add_argument("--strict", action="store_true", help="strictLocale=true（locale 必填）")
    psearch.set_defaults(func=cmd_channels_search)

    ch.add_parser("categories", help="全部分类").set_defaults(func=cmd_channels_categories)

    pbc = ch.add_parser("by-category", help="按分类列频道")
    pbc.add_argument("slug")
    pbc.add_argument("--limit", type=int, default=None)
    pbc.add_argument("--cursor", default=None)
    pbc.add_argument("--locale", default=None)
    pbc.add_argument("--sort", choices=["latest", "popular"], default=None)
    pbc.set_defaults(func=cmd_channels_by_category)

    # grains
    gr = sub.add_parser("grains", help="grain 内容操作").add_subparsers(dest="sub", required=True)

    gl = gr.add_parser("list", help="列 grain：默认公开 feed；--subscribed 我订阅的；--channel 指定频道")
    gl.add_argument("--channel", default=None)
    gl.add_argument("--subscribed", action="store_true")
    gl.add_argument("--limit", type=int, default=20)
    gl.add_argument("--cursor", default=None)
    gl.add_argument("--locale", default=None)
    gl.set_defaults(func=cmd_grains_list)

    gg = gr.add_parser("get", help="单 grain 详情")
    gg.add_argument("id")
    gg.set_defaults(func=cmd_grains_get)

    gs = gr.add_parser("search", help="按 query 搜公开 grain")
    gs.add_argument("query")
    gs.add_argument("--limit", type=int, default=None)
    gs.add_argument("--locale", default=None)
    gs.add_argument("--strict", action="store_true")
    gs.set_defaults(func=cmd_grains_search)

    # feed
    pf = sub.add_parser("feed", help="我订阅的 grain 流（grain.listSubscribed 的简写）")
    pf.add_argument("--limit", type=int, default=20)
    pf.add_argument("--cursor", default=None)
    pf.set_defaults(func=cmd_feed)

    # api 兜底
    pa = sub.add_parser("api", help="任意 tRPC procedure 直调（糖衣没覆盖时用）")
    pa.add_argument("procedure", help="如 channel.update / grain.remove")
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
