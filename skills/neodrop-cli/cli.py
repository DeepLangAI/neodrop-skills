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
from lib.web_urls import channel_url, post_url, user_url  # noqa: E402

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


def _is_headless_env() -> bool:
    """探测当前是不是「拉不起浏览器」的环境。

    判据（任一命中即视为 headless）：
    - 非 macOS / 非 Windows 下 `DISPLAY` 与 `WAYLAND_DISPLAY` 都没设
    - 在 SSH 远程会话里（`SSH_CONNECTION` 或 `SSH_TTY` 有值）且无 DISPLAY
    - `NEODROP_HEADLESS=1` 显式声明

    Mac / Windows 默认有 GUI 不判 headless（webbrowser stdlib 在这两个平台
    几乎总能找到默认浏览器）。
    """
    if os.environ.get("NEODROP_HEADLESS") == "1":
        return True
    if sys.platform in ("darwin", "win32"):
        return False
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    in_ssh = bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"))
    return (not has_display) or in_ssh


def cmd_login(args: argparse.Namespace) -> None:
    # --import 走完全不同的路径：从 stdin 读凭证 JSON、远端校验、落地
    if getattr(args, "import_creds", False):
        _cmd_login_import(args)
        return

    web_origin: str = args.server
    # --api 显式 > NEODROP_API env > 启发式推断
    api_origin: str = args.api or ENV_API_OVERRIDE or infer_api_origin(web_origin)
    name: str = args.name
    port: int = args.port

    # 决策：是否尝试拉浏览器
    # - 显式 --no-browser → 不拉
    # - 否则 headless 自动探测 → 不拉 + 提示
    # - 其它情况 → 拉
    headless = _is_headless_env()
    open_browser = not args.no_browser and not headless

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
    note("")
    note("👉 在任意能上网的浏览器打开下面 URL 授权：")
    note(f"   {auth_url}")
    note("")

    if open_browser:
        opened = open_in_browser(auth_url)
        if not opened:
            note("⚠ 无法自动拉起浏览器——请手动复制上面的 URL 打开。")
    elif args.no_browser:
        note("（--no-browser：不尝试拉起浏览器，等你手动打开 URL 完成授权）")
    else:
        note("（检测到 headless 环境：没启浏览器。等你手动打开上面 URL 完成授权；")
        note("  如果你这台机器和浏览器在不同网络，loopback callback 不可达，请改用 `login --import`，详见 references/auth.md）")

    note(f"等待授权回调（最长 10 分钟）...")

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


def _cmd_login_import(_args: argparse.Namespace) -> None:
    """从 stdin 读凭证 JSON，远端校验通过后落地——用于无浏览器的远程 / 沙箱 agent。

    用法：
        cat ~/.neodrop/credentials.json | ./bin/neodrop login --import
        ssh agent './bin/neodrop login --import' < ~/.neodrop/credentials.json
    """
    if sys.stdin.isatty():
        raise RuntimeError(
            "--import 需要从 stdin 读凭证 JSON；请用管道或重定向：\n"
            "  cat creds.json | ./bin/neodrop login --import"
        )
    raw = sys.stdin.read().strip()
    if not raw:
        raise RuntimeError("--import: stdin 是空的，没读到凭证 JSON")
    try:
        creds = json.loads(raw)
    except json.JSONDecodeError as err:
        raise RuntimeError(f"--import: stdin 不是合法 JSON：{err}") from err

    required = {"webOrigin", "apiOrigin", "token", "tokenId", "name", "expiresAt"}
    missing = required - set(creds)
    if missing:
        raise RuntimeError(f"--import: 凭证 JSON 缺字段 {sorted(missing)}")

    # 远端校验：用导入的 token 调一次 user.getMe，确认 token 有效
    note(f"web   = {creds['webOrigin']}")
    note(f"api   = {creds['apiOrigin']}")
    note("校验导入的 token…")
    try:
        me = trpc_query({"apiOrigin": creds["apiOrigin"], "token": creds["token"]}, "user.getMe")
    except ApiError as err:
        raise RuntimeError(f"--import: token 校验失败（{err}）；该凭证可能已过期或被撤销") from err

    # createdAt 缺失补当前时间（兼容老版本凭证）
    if "createdAt" not in creds:
        creds["createdAt"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    write_credentials(creds)
    note(f"✅ 导入成功：{me.get('email') or me.get('id') or '<unknown>'}")
    note(f"   credentials = {credentials_path()}")
    emit(
        {
            "ok": True,
            "imported": True,
            "webOrigin": creds["webOrigin"],
            "apiOrigin": creds["apiOrigin"],
            "user": me,
            "tokenId": creds["tokenId"],
            "tokenName": creds["name"],
            "expiresAt": creds["expiresAt"],
        }
    )


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
    api_origin, token, creds = _authed_ctx()
    me = trpc_query({"apiOrigin": api_origin, "token": token}, "user.getMe")
    emit(me)
    if isinstance(me, dict) and me.get("id"):
        note(f"🔗 {user_url(creds['webOrigin'], me['id'])}")


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
    api_origin, token, creds = _authed_ctx()
    emit(trpc_query({"apiOrigin": api_origin, "token": token}, "channel.getById", {"id": args.id}))
    note(f"🔗 {channel_url(creds['webOrigin'], args.id)}")


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
    api_origin, token, creds = _authed_ctx()
    emit(trpc_query({"apiOrigin": api_origin, "token": token}, "grain.getById", {"id": args.id}))
    note(f"🔗 {post_url(creds['webOrigin'], args.id)}")


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
    p.add_argument(
        "--no-browser",
        action="store_true",
        help="不尝试拉起浏览器，只打印 URL；适合 SSH / 无 DISPLAY 环境，等用户在另一个浏览器里手动打开",
    )
    p.add_argument(
        "--import",
        dest="import_creds",
        action="store_true",
        help="从 stdin 读凭证 JSON 并校验落地（适合云沙箱 / 无浏览器机器，从另一台已登录机器把 ~/.neodrop/credentials.json 搬过来）",
    )
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

    pc = add(ch, "create", help="创建频道")
    pc.add_argument("--name", default=None)
    pc.add_argument("--description", default=None)
    pc.add_argument("--type", choices=["PUBLIC", "PRIVATE"], default=None)
    pc.add_argument("--locale", default=None)
    g = pc.add_mutually_exclusive_group()
    g.add_argument("--json", default=None, help="原始 JSON 输入（复杂场景）")
    g.add_argument("--stdin", action="store_true", help="从 stdin 读 JSON")
    pc.set_defaults(func=cmd_channels_create)

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

    # grains
    gr = add(sub, "grains", help="grain 内容操作").add_subparsers(dest="sub", required=True)

    gl = add(gr, "list", help="列 grain：默认公开 feed；--subscribed 我订阅的；--channel 指定频道")
    gl.add_argument("--channel", default=None)
    gl.add_argument("--subscribed", action="store_true")
    gl.add_argument("--limit", type=int, default=20)
    gl.add_argument("--cursor", default=None)
    gl.add_argument("--locale", default=None)
    gl.set_defaults(func=cmd_grains_list)

    gg = add(gr, "get", help="单 grain 详情")
    gg.add_argument("id")
    gg.set_defaults(func=cmd_grains_get)

    gs = add(gr, "search", help="按 query 搜公开 grain")
    gs.add_argument("query")
    gs.add_argument("--limit", type=int, default=None)
    gs.add_argument("--locale", default=None)
    gs.add_argument("--strict", action="store_true")
    gs.set_defaults(func=cmd_grains_search)

    # feed
    pf = add(sub, "feed", help="我订阅的 grain 流（grain.listSubscribed 的简写）")
    pf.add_argument("--limit", type=int, default=20)
    pf.add_argument("--cursor", default=None)
    pf.set_defaults(func=cmd_feed)

    # api 兜底
    pa = add(sub, "api", help="任意 tRPC procedure 直调（糖衣没覆盖时用）")
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
