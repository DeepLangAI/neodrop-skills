"""统一输出：stdout = JSON（AI 直接 json.loads），stderr = 日志/提示。

默认单行 JSON；--pretty 切 2 空格缩进 JSON——两者都是合法 JSON，AI 不需要
flag 切换也能解析。
"""

from __future__ import annotations

import json
import sys
from typing import Any

_pretty = False


def set_pretty(value: bool) -> None:
    global _pretty
    _pretty = value


def emit(data: Any) -> None:
    if data is None:
        return
    indent = 2 if _pretty else None
    sys.stdout.write(json.dumps(data, indent=indent, ensure_ascii=False) + "\n")


def note(msg: str, end: str = "\n") -> None:
    sys.stderr.write(msg + end)
    if end != "\n":
        sys.stderr.flush()
