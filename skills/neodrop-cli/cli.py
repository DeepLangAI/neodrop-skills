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


def cmd_login(args: argparse.Namespace) -> None:
    """统一登录：session polling 模式。

    流程：
      1. startSession 拿到 sessionId + pollSecret + verification URL
         （pollSecret 只在本进程内持有，是 poll 领 token 的唯一凭据，不进 URL）
      2. 打印 URL 给用户复制到浏览器（不自动拉起，不开本地 server，无 callback）
      3. 带 pollSecret 轮询 pollSession 直到 APPROVED / DENIED / EXPIRED
      4. 拿到 token 写入 ~/.neodrop/credentials.json

    适用场景：
      - 本地有浏览器：复制 URL 到本地浏览器打开
      - SSH / 无 DISPLAY：复制到任意机器（手机、另一台笔记本）的浏览器
      - 云沙箱 / 容器：同上，URL 跨网络可达，浏览器不需要回连 CLI 机器

    跨机器复用本地登录：直接 scp ~/.neodrop/credentials.json，不需要专门的 import 命令。
    """
    import time

    web_origin: str = args.server.rstrip("/")
    # --api 显式 > NEODROP_API env > 启发式推断
    api_origin: str = args.api or ENV_API_OVERRIDE or infer_api_origin(web_origin)
    client_name: str = args.name

    note(f"web   = {web_origin}")
    note(f"api   = {api_origin}")

    # 1. 起 session
    session_info = trpc_mutation(
        {"apiOrigin": api_origin, "token": None},
        "cliToken.startSession",
        {"clientName": client_name, "webOrigin": web_origin},
    )
    session_id: str = session_info["sessionId"]
    # pollSecret 是后端只下发给本 CLI 进程的私有领取凭据，不进 verification URL；
    # poll 时必须回传它才能领到 token。URL 里只有 session_id，截图/转发泄漏也领不走 token。
    poll_secret: str = session_info["pollSecret"]
    verification_url: str = session_info["verificationUrl"]
    poll_interval: float = max(1.0, float(session_info.get("pollIntervalSeconds") or 2))

    note("")
    note("👉 在任意浏览器（手机 / 笔记本 / 同机都行）打开下面 URL 完成授权：")
    note("")
    # URL 顶格单独成行（不加缩进）：这条 URL 含 ?session= 256bit 串、必然超 80 列，
    # 终端会折行。带缩进会让有效行更长、更易折，且部分终端整行选中会把前导空格也带上。
    # 顶格单独一行最便于「三击选整行 / 鼠标拖整行」一次性把完整 URL 复制走。
    note(verification_url)
    note("")
    note("   （URL 较长会折行，复制时连同结尾的 ?session=... 一起选全）")
    note(f"   客户端名「{client_name}」（在授权页确认是本次启动的 CLI）")
    note("   授权链接 10 分钟内有效。授权后回到这个终端继续——CLI 会自动检测。")
    note("")

    # 2. 轮询
    deadline = time.monotonic() + 10 * 60  # 与后端 session 寿命对齐
    waited_dots = 0
    token: Optional[str] = None
    token_id: Optional[str] = None
    expires_at: Optional[str] = None
    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        try:
            res = trpc_query(
                {"apiOrigin": api_origin, "token": None},
                "cliToken.pollSession",
                {"sessionId": session_id, "pollSecret": poll_secret},
            )
        except ApiError as err:
            # NOT_FOUND 一般是 session 已被后端清理；其它错误也直接抛
            raise RuntimeError(f"轮询授权失败：{err}") from err

        status = res.get("status")
        if status == "APPROVED":
            if res.get("alreadyClaimed"):
                raise RuntimeError(
                    "授权 token 已被领走（极少触发，正常应是本 CLI 自己领）。请重新 `neodrop login`。"
                )
            token = res.get("token")
            token_id = res.get("tokenId")
            ea = res.get("tokenExpiresAt")
            expires_at = ea if isinstance(ea, str) else (ea.isoformat() if ea else None)
            if not token:
                raise RuntimeError("授权返回 APPROVED 但缺 token；请重新 `neodrop login`")
            break
        if status == "DENIED":
            raise RuntimeError("授权被用户拒绝。如有需要请重新 `neodrop login` 并核对客户端名。")
        if status == "EXPIRED":
            raise RuntimeError("授权链接已过期（10 分钟内未授权）。请重新 `neodrop login`。")
        # 还在 PENDING — 打点进度（每 5 次 poll 一个点，不刷屏）
        waited_dots += 1
        if waited_dots % 5 == 0:
            note(".", end="")
    else:
        raise RuntimeError("等待授权超时。请重新 `neodrop login`。")

    note("")

    # 3. 写凭证
    write_credentials(
        {
            "webOrigin": web_origin,
            "apiOrigin": api_origin,
            "token": token,
            "tokenId": token_id or "",
            "name": client_name,
            "expiresAt": expires_at or "",
            "createdAt": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
    )

    # 4. 校验 + 输出
    me = trpc_query({"apiOrigin": api_origin, "token": token}, "user.getMe")
    note(f"✅ 登录成功：{me.get('email') or me.get('id') or '<unknown>'}")
    note(f"   credentials = {credentials_path()}")
    emit(
        {
            "ok": True,
            "webOrigin": web_origin,
            "apiOrigin": api_origin,
            "user": me,
            "tokenId": token_id,
            "tokenName": client_name,
            "expiresAt": expires_at,
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
    p = add(
        sub,
        "login",
        help="打印授权 URL → 你在任意浏览器同意 → CLI 轮询拿 PAT 写入 ~/.neodrop/credentials.json。不开浏览器、不开本地 server、不回调",
    )
    p.add_argument("--server", default=DEFAULT_SERVER, help=f"web origin（默认 {DEFAULT_SERVER}，NEODROP_SERVER 覆盖）")
    p.add_argument(
        "--api",
        default=None,
        help="api origin（不传按 --server 启发式推断：neodrop.ai → api.neodrop.ai；NEODROP_API 覆盖）",
    )
    p.add_argument("--name", default=_detect_client_name(), help="客户端名（授权页显示给用户辨认）")
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
