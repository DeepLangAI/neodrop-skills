"""本地凭证（~/.neodrop/credentials.json，chmod 0600）。

只支持单一 active token——切换 server / 换号都走 logout → login。
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Optional, TypedDict


class Credentials(TypedDict):
    """凭证 schema。

    web_origin / api_origin 拆开是因为 Neodrop 部署的产品域和 API 域不同
    （neodrop.ai vs api.neodrop.ai；本地 dev 是 4001 vs 3001）。
    """

    webOrigin: str
    apiOrigin: str
    token: str
    tokenId: str
    name: str
    expiresAt: str  # ISO 8601
    createdAt: str  # ISO 8601


FILE_DIR = Path.home() / ".neodrop"
FILE_PATH = FILE_DIR / "credentials.json"


def credentials_path() -> Path:
    return FILE_PATH


def read_credentials() -> Optional[Credentials]:
    if not FILE_PATH.exists():
        return None
    try:
        return json.loads(FILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise RuntimeError(f"无法解析 {FILE_PATH}：{err}") from err


def write_credentials(creds: Credentials) -> None:
    """原子写：写临时文件 → chmod 0600 → rename。

    chmod 在 rename 之前完成，确保最终文件出现的瞬间权限就是 0600，
    不存在其他进程能读到 world-readable 的窗口。
    """
    FILE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = FILE_PATH.with_suffix(FILE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(creds, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    tmp.replace(FILE_PATH)


def clear_credentials() -> None:
    try:
        FILE_PATH.unlink()
    except FileNotFoundError:
        pass


def require_credentials() -> Credentials:
    creds = read_credentials()
    if creds is None:
        raise RuntimeError("未登录。先运行：./skills/neodrop-cli/bin/neodrop login")
    return creds
