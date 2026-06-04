"""Neodrop 部署的 web origin（产品域）与 api origin（backend 域）解耦。

线上：web = https://neodrop.ai，api = https://api.neodrop.ai
本地 dev：web = http://localhost:4001，api = http://localhost:3001

CLI 让用户显式传 --api；不传时按 web origin 启发式推断。self-host 用户默认
假设 backend 反代到 web 同域 /trpc/*，需要时用 --api 覆盖。
"""

from __future__ import annotations

from urllib.parse import urlparse


def infer_api_origin(web_origin: str) -> str:
    parsed = urlparse(web_origin)
    host = (parsed.hostname or "").lower()
    port = parsed.port

    # 线上 neodrop.ai → api.neodrop.ai
    if host == "neodrop.ai":
        return f"{parsed.scheme}://api.neodrop.ai"

    # 本地 dev：localhost:4001 / 127.0.0.1:4001 → 同 host 3001
    if host in ("localhost", "127.0.0.1") and port == 4001:
        return f"{parsed.scheme}://{host}:3001"

    # 其他（self-host 反代等）：默认与 web 同域，假设 /trpc/* 反代到 backend。
    return web_origin.rstrip("/")
