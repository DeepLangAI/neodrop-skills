"""跨平台打开浏览器。

用 stdlib `webbrowser` 而不是手写 spawn open/xdg-open/start——webbrowser 自己
处理三平台分支，且对 SSH 远程会话等无 display 环境也有 graceful fallback。
"""

from __future__ import annotations

import webbrowser


def open_in_browser(url: str) -> bool:
    """打开 URL；失败返回 False，让调用方提示用户手动复制。"""
    try:
        return webbrowser.open(url, new=2, autoraise=True)
    except webbrowser.Error:
        return False
