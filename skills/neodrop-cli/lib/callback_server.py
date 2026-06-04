"""本地 OAuth callback server：起一次性 HTTP server 接 /cli-auth 回调。

授权页同意后会把 token / state / expires_at 拼到 callback URL（http://127.0.0.1:<port>/cb）
的 query 上 redirect 回来。本模块只校验 state（防 CSRF）并返回 token 给调用方。

设计：start() 立即返回 handle（含 .url），调用方先拼授权 URL 打开浏览器，再
.await_result() 阻塞等回调——这样主线程能在等之前把 URL 打到 stderr 给用户看。
"""

from __future__ import annotations

import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional, TypedDict
from urllib.parse import parse_qs, urlparse


class CallbackResult(TypedDict):
    token: str
    state: str
    expiresAt: str


def generate_state() -> str:
    """32 字节随机 hex（≈64 字符），授权页 state 长度上限 128，下限 8。"""
    return secrets.token_hex(32)


def _html_page(title: str, hint: str) -> bytes:
    return (
        f"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;
background:#fafaf9;color:#1c1917}}
.box{{max-width:420px;padding:32px;border-radius:12px;background:#fff;
box-shadow:0 1px 3px rgba(0,0,0,0.08);text-align:center}}
h1{{margin:0 0 12px;font-size:20px}}p{{margin:0;color:#57534e}}</style></head>
<body><div class="box"><h1>{title}</h1><p>{hint}</p></div>
<script>setTimeout(()=>window.close(),1500)</script></body></html>"""
    ).encode("utf-8")


class CallbackHandle:
    """本地 server 的 handle——start() 立刻返回，调用方拿 .url 拼授权 URL。

    .await_result(timeout) 阻塞等回调；done 后 server 自动 close。
    """

    def __init__(self, port: int, expected_state: str) -> None:
        self.expected_state = expected_state
        # 用 list 当 mutable holder（嵌套函数闭包写入用）
        self._holder: dict = {}

        handler_cls = self._make_handler()
        self._httpd = HTTPServer(("127.0.0.1", port), handler_cls)
        self._actual_port: int = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._actual_port}/cb"

    @property
    def port(self) -> int:
        return self._actual_port

    def await_result(self, timeout_seconds: int = 600) -> CallbackResult:
        try:
            deadline = time.time() + timeout_seconds
            while time.time() < deadline:
                if "result" in self._holder or "error" in self._holder:
                    # 让浏览器把 HTML 收完再关
                    time.sleep(0.2)
                    break
                time.sleep(0.05)
            else:
                raise TimeoutError(
                    f"等待授权超时（{timeout_seconds}s），请重新运行 neodrop login"
                )
        finally:
            self.close()

        if "error" in self._holder:
            raise RuntimeError(self._holder["error"])
        return self._holder["result"]

    def close(self) -> None:
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        except Exception:  # noqa: BLE001
            pass

    def _make_handler(self):
        holder = self._holder
        expected_state = self.expected_state

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # 抑制 default access log
                pass

            def _respond_html(self, status: int, html: bytes) -> None:
                self.send_response(status)
                self.send_header("content-type", "text/html; charset=utf-8")
                self.send_header("cache-control", "no-store")
                self.send_header("content-length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)

            def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler 接口固定)
                parsed = urlparse(self.path)
                if parsed.path != "/cb":
                    self._respond_html(404, b"not found")
                    return

                params = parse_qs(parsed.query)
                state = (params.get("state") or [""])[0]
                token = (params.get("token") or [""])[0]
                expires_at = (params.get("expires_at") or [""])[0]
                error = (params.get("error") or [""])[0]

                if state != expected_state:
                    self._respond_html(
                        400, _html_page("❌ state 校验失败", "请重新运行 neodrop login。")
                    )
                    holder["error"] = "state 校验失败，请重试 neodrop login"
                    return

                if error:
                    self._respond_html(400, _html_page("❌ 授权被拒绝", "可以关闭此页面。"))
                    holder["error"] = f"授权被拒绝：{error}"
                    return

                if not token or not expires_at:
                    self._respond_html(
                        400,
                        _html_page("❌ 回调缺少 token / expires_at", "请重新运行 neodrop login。"),
                    )
                    holder["error"] = "回调参数不完整"
                    return

                self._respond_html(200, _html_page("✅ 授权完成", "可以关闭此页面，回到终端。"))
                holder["result"] = {"token": token, "state": state, "expiresAt": expires_at}

        return Handler


def start_callback_server(
    *, expected_state: str, port: int = 0
) -> CallbackHandle:
    """起本地 server，立即返回 handle。调用方拿 handle.url 拼授权 URL，再 handle.await_result()。"""
    return CallbackHandle(port=port, expected_state=expected_state)
