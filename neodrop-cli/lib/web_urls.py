"""Neodrop 前端 URL 拼装：用户拿到 id 后能直接得到一条可点击的网页链接。

为什么放 skill 而不是后端 response：URL 路径（`/feed/<id>` / `/channel/<id>` /
`/user/<id>`）是前端的渲染契约，不是数据契约——`grain.id` 是稳定的数据；前端
明天可以把路由改成 `/post/<id>` 而无需后端发新版 API。把路由塞进 response 会让
backend 反向依赖前端渲染。前端自己也是 20+ 处直接字符串拼 `/feed/${id}`，没有
中心化 helper。这里给 CLI 内部用，作为 stderr 上的人类可读提示。

路径权威源 = `apps/web/src/app/[locale]/<route>/[id]/page.tsx`：
- post / grain  → `/feed/<id>`
- channel       → `/channel/<id>`
- user          → `/user/<id>`

注意：CLI 不挂 locale 前缀（neodrop.ai 的 `localePrefix='as-needed'`，默认
locale `en` 落到无前缀路径；其它 locale 由前端按用户偏好做客户端 redirect）。
"""

from __future__ import annotations


def _strip(origin: str) -> str:
    return origin.rstrip("/")


def post_url(web_origin: str, post_id: str) -> str:
    return f"{_strip(web_origin)}/feed/{post_id}"


def channel_url(web_origin: str, channel_id: str) -> str:
    return f"{_strip(web_origin)}/channel/{channel_id}"


def user_url(web_origin: str, user_id: str) -> str:
    return f"{_strip(web_origin)}/user/{user_id}"
