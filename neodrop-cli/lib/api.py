"""tRPC 11 HTTP 调用最小封装（适配 backend 配置的 superjson transformer）。

URL 形态：
  - query:    GET  /trpc/<proc>?input=<urlencoded {json:<input>}>
  - mutation: POST /trpc/<proc>  body = {json:<input>}

响应形态（superjson）：
  - 成功：{ result: { data: { json: <T>, meta?: {...} } } }
  - 失败：{ error: { json: { message, code, data: {...} } } }

入参 / 出参的 superjson `meta` 字段用于 Date 等非 JSON 类型还原，CLI 用不上
（凭证 expiresAt 直接用 ISO 字符串），这里只取 `json` 字段。

stdlib only：用 urllib.request，避免让用户 pip install requests。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional, TypedDict


class ApiOptions(TypedDict, total=False):
    apiOrigin: str
    token: Optional[str]


class ApiError(RuntimeError):
    """tRPC 业务错误（res.ok=false 或 body.error 非空）。

    code 来自 tRPC 的错误码（'UNAUTHORIZED' / 'NOT_FOUND' / 'BAD_REQUEST' 等），
    CLI 上层可据此分流（如 401 提示重 login）。
    """

    def __init__(self, message: str, code: str = "", http_status: int = 0) -> None:
        super().__init__(f"[{code}] {message}" if code else message)
        self.code = code
        self.http_status = http_status


def _build_url(api_origin: str, proc: str, input_value: Any = None) -> str:
    base = f"{api_origin.rstrip('/')}/trpc/{proc}"
    if input_value is None:
        return base
    # superjson 入参 wrapper：{ json: <value> }
    encoded = urllib.parse.urlencode({"input": json.dumps({"json": input_value})})
    return f"{base}?{encoded}"


# Cloudflare WAF 看到默认的 "Python-urllib/3.x" UA 会按 bot 拒（HTTP 403 + error
# code 1010）。给一个老实的客户端身份——告诉 CF/origin「这是 neodrop-cli」，便于
# 排查与白名单。**改 UA 不是为了伪装**，是为了通过基础的 client fingerprint check。
USER_AGENT = "neodrop-cli/0.1 (+https://github.com/DeepLangAI/neodrop-skills)"


def _do_request(
    *,
    method: str,
    url: str,
    token: Optional[str],
    body: Optional[bytes],
) -> tuple[int, bytes]:
    headers = {
        "content-type": "application/json",
        "user-agent": USER_AGENT,
        "accept": "application/json",
    }
    if token:
        headers["authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        # 非 2xx：仍读 body，tRPC 错误细节在里面
        return e.code, e.read()
    except urllib.error.URLError as e:
        # 网络层错误（连接拒绝、DNS、超时）
        raise RuntimeError(f"连接失败：{e.reason}") from e


def _handle_response(status: int, raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace")
    try:
        body = json.loads(text) if text else None
    except json.JSONDecodeError:
        snippet = text[:200]
        raise RuntimeError(f"非 JSON 响应（HTTP {status}）：{snippet}")

    if status >= 400 or (body and body.get("error")):
        err = (body or {}).get("error") or {}
        err_json = err.get("json") or {}
        msg = err_json.get("message") or err.get("message") or f"HTTP {status}"
        code = (err_json.get("data") or {}).get("code") or err.get("code") or ""
        raise ApiError(msg, code=code, http_status=status)

    # superjson 响应剥层
    return body["result"]["data"]["json"]


def trpc_query(opts: ApiOptions, proc: str, input_value: Any = None) -> Any:
    url = _build_url(opts["apiOrigin"], proc, input_value)
    status, raw = _do_request(method="GET", url=url, token=opts.get("token"), body=None)
    return _handle_response(status, raw)


def trpc_mutation(opts: ApiOptions, proc: str, input_value: Any = None) -> Any:
    url = _build_url(opts["apiOrigin"], proc)
    # mutation 永远发 JSON body：input 为 None 时发 {"json": null}
    body = json.dumps({"json": input_value}).encode("utf-8")
    status, raw = _do_request(method="POST", url=url, token=opts.get("token"), body=body)
    return _handle_response(status, raw)
