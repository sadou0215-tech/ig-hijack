# -*- coding: utf-8 -*-
"""
discord_notify.py  ── 貼るだけで動く Discord Webhook 通知モジュール

app.py からは例えばこう呼べます（位置引数・キーワードどちらでもOK）:
    from discord_notify import build_embed, post_embedded
    embed = build_embed("タイトル", "説明", color=0x2ECC71, fields=[("項目", "値", True)])
    post_embedded(embed)

    post_embedded("本文だけ送る")
    post_embedded(title="タイトル", description="説明")   # 直接渡しでもOK
    notify("エラー出たよ")

Webhook URL は環境変数から読みます（Render の Environment に設定）:
    DISCORD_WEBHOOK_URL   (DISCORD_WEBHOOK / WEBHOOK_URL などでも可)
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:  # requests が無くても urllib で動くようにする
    import requests
except Exception:  # pragma: no cover
    requests = None

try:
    from urllib import request as _urlreq
    from urllib import error as _urlerr
except Exception:  # pragma: no cover
    _urlreq = None
    _urlerr = None

__all__ = [
    "build_embed", "build_embeds", "build_payload",
    "post_embedded", "post_embed", "send_embed", "send_discord",
    "send_discord_message", "send_notification", "post_notification",
    "notify", "notify_discord", "notify_error",
    "post_embedded_async", "apost_embedded", "send_embed_async", "notify_async",
    "webhook_url", "DEFAULT_COLOR",
]

LOG = logging.getLogger("discord_notify")
if not LOG.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

# ----------------------------------------------------------------------------
# 設定
# ----------------------------------------------------------------------------
DEFAULT_COLOR = 0x5865F2  # Discord blurple
_ENV_KEYS = (
    "DISCORD_WEBHOOK_URL",
    "DISCORD_WEBHOOK",
    "DISCORD_WEBHOOK_URI",
    "DISCORD_URL",
    "WEBHOOK_URL",
    "DISCORD_NOTIFY_WEBHOOK",
)
_TIMEOUT = float(os.getenv("DISCORD_WEBHOOK_TIMEOUT", "10"))
_RETRIES = int(os.getenv("DISCORD_WEBHOOK_RETRIES", "3"))

# Discord 側の文字数上限
_LIM = {
    "title": 256,
    "description": 4096,
    "field_name": 256,
    "field_value": 1024,
    "footer": 2048,
    "author": 256,
    "url": 512,
    "content": 2000,
    "total": 6000,
}

_EMBED_KEYS = {
    "title", "description", "desc", "message", "text", "body", "content",
    "color", "colour", "fields", "footer", "footer_text", "url",
    "thumbnail", "thumbnail_url", "image", "image_url",
    "author", "author_name", "timestamp", "ts",
}


# ----------------------------------------------------------------------------
# 小物
# ----------------------------------------------------------------------------
def _clip(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def webhook_url(explicit: Optional[str] = None) -> Optional[str]:
    """Webhook URL を解決する（引数 > 環境変数）"""
    if explicit:
        return str(explicit).strip()
    for key in _ENV_KEYS:
        val = os.getenv(key)
        if val and val.strip():
            return val.strip()
    return None


def _is_webhook_url(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("http") and "webhook" in value.lower()


def _color(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return max(0, min(0xFFFFFF, value))
    text = str(value).strip()
    if text.startswith("#"):
        text = text[1:]
    elif text.lower().startswith("0x"):
        text = text[2:]
    try:
        return max(0, min(0xFFFFFF, int(text, 16)))
    except ValueError:
        return None


def _as_dict(value: Any, text_key: str) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (list, tuple)) and value:
        first = value[0]
        if isinstance(first, dict):
            return dict(first)
        return {text_key: _clip(first, _LIM[text_key])}
    return {text_key: _clip(value, _LIM[text_key])}


def _norm_fields(fields: Any) -> List[Dict[str, Any]]:
    if not fields:
        return []
    out: List[Dict[str, Any]] = []
    if isinstance(fields, dict):
        fields = list(fields.items())
    for item in fields:
        if item is None:
            continue
        if isinstance(item, dict):
            name = item.get("name")
            value = item.get("value")
            inline = bool(item.get("inline", False))
        elif isinstance(item, (list, tuple)):
            if len(item) == 2:
                name, value = item
                inline = False
            elif len(item) >= 3:
                name, value, inline = item[0], item[1], bool(item[2])
            else:
                continue
        else:
            name, value, inline = str(item), "\u200b", False
        if name is None and value is None:
            continue
        out.append({
            "name": _clip(name if name is not None else "\u200b", _LIM["field_name"]) or "\u200b",
            "value": _clip(value if value is not None else "\u200b", _LIM["field_value"]) or "\u200b",
            "inline": inline,
        })
        if len(out) >= 25:
            break
    return out


# ----------------------------------------------------------------------------
# Embed 組み立て
# ----------------------------------------------------------------------------
_POSITIONAL = (
    "title", "description", "color", "fields",
    "footer", "url", "thumbnail", "image", "author", "timestamp",
)

_ALIASES = {
    "desc": "description",
    "message": "description",
    "text": "description",
    "body": "description",
    "msg": "description",
    "colour": "color",
    "embed_color": "color",
    "footer_text": "footer",
    "image_url": "image",
    "thumbnail_url": "thumbnail",
    "author_name": "author",
    "ts": "timestamp",
}


def build_embed(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Discord の embed（dict）を組み立てて返す。

    位置引数は title, description, color, fields, footer, url, ... の順で解釈。
    """
    data: Dict[str, Any] = {}
    for name, val in zip(_POSITIONAL, args):
        data.setdefault(name, val)
    for key, val in kwargs.items():
        data[_ALIASES.get(key, key)] = val

    embed: Dict[str, Any] = {}

    title = data.get("title")
    if title is not None and str(title) != "":
        embed["title"] = _clip(title, _LIM["title"])

    description = data.get("description")
    if description is not None and str(description) != "":
        embed["description"] = _clip(description, _LIM["description"])

    url = data.get("url")
    if url:
        embed["url"] = _clip(url, _LIM["url"])

    embed["color"] = _color(data.get("color")) if data.get("color") is not None else DEFAULT_COLOR

    fields = _norm_fields(data.get("fields"))
    if fields:
        embed["fields"] = fields

    author = _as_dict(data.get("author"), "name")
    if author:
        embed["author"] = author

    footer = _as_dict(data.get("footer"), "text")
    if footer:
        embed["footer"] = footer

    thumbnail = _as_dict(data.get("thumbnail"), "url")
    if thumbnail:
        embed["thumbnail"] = thumbnail

    image = _as_dict(data.get("image"), "url")
    if image:
        embed["image"] = image

    ts = data.get("timestamp")
    if ts is False:
        pass
    elif ts in (None, True):
        embed["timestamp"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    else:
        embed["timestamp"] = str(ts)

    # 合計 6000 文字制限の安全策
    total = len(embed.get("title", "")) + len(embed.get("description", ""))
    total += len(embed.get("footer", {}).get("text", "")) + len(embed.get("author", {}).get("name", ""))
    for f in embed.get("fields", []):
        total += len(f["name"]) + len(f["value"])
    if total > _LIM["total"] and "description" in embed:
        over = total - _LIM["total"]
        embed["description"] = _clip(embed["description"], max(64, len(embed["description"]) - over))

    return embed


def build_embeds(*items: Any) -> List[Dict[str, Any]]:
    """dict のリスト（各要素が build_embed の kwargs）から複数 embed を作る"""
    embeds: List[Dict[str, Any]] = []
    for item in items:
        if item is None:
            continue
        if isinstance(item, dict):
            embeds.append(build_embed(**item))
        else:
            embeds.append(build_embed(item))
        if len(embeds) >= 10:
            break
    return embeds


# ----------------------------------------------------------------------------
# payload 組み立て
# ----------------------------------------------------------------------------
def build_payload(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Webhook に POST する payload を正規化して作る。

    受け付ける形:
        build_payload(embed_dict)
        build_payload("content", embed_dict)
        build_payload(embeds=[...])
        build_payload(title=..., description=..., username=...)
        build_payload("https://discord.com/api/webhooks/...", embed_dict)
    """
    embed = kwargs.pop("embed", None)
    embeds = kwargs.pop("embeds", None)
    content = kwargs.pop("content", None) or kwargs.pop("text_content", None)
    username = kwargs.pop("username", None) or kwargs.pop("user", None)
    avatar_url = kwargs.pop("avatar_url", None) or kwargs.pop("avatar", None)
    tts = kwargs.pop("tts", None)
    thread_name = kwargs.pop("thread_name", None)
    found_url: Optional[str] = None

    for arg in args:
        if arg is None:
            continue
        if _is_webhook_url(arg) and found_url is None:
            found_url = arg
            continue
        if isinstance(arg, dict):
            if "embeds" in arg and embed is None:
                embeds = list(arg.get("embeds") or []) + list(embeds or [])
                if arg.get("content") and content is None:
                    content = arg["content"]
            elif embed is None:
                embed = arg
            else:
                embeds = list(embeds or []) + [arg]
        elif isinstance(arg, (list, tuple)):
            for item in arg:
                if isinstance(item, dict):
                    embeds = list(embeds or []) + [item]
                elif item is not None and content is None:
                    content = str(item)
        else:
            content = str(arg) if content is None else f"{content}\n{arg}"

    # embed 側に embeds ラッパーが入っていた場合の吸収
    if isinstance(embed, dict) and "embeds" in embed and "title" not in embed:
        embeds = list(embed.get("embeds") or []) + list(embeds or [])
        embed = None

    # embed 未指定でも embed っぽい kwargs があれば作る
    if embed is None and not embeds:
        leftovers = {k: v for k, v in kwargs.items() if k in _EMBED_KEYS}
        if leftovers:
            embed = build_embed(**leftovers)
            for k in leftovers:
                kwargs.pop(k, None)

    embeds_list: List[Dict[str, Any]] = []
    if isinstance(embed, dict):
        embeds_list.append(embed)
    elif embed is not None:
        embeds_list.append(build_embed(embed))
    if embeds:
        for item in embeds:
            if isinstance(item, dict):
                embeds_list.append(item)

    payload: Dict[str, Any] = {}
    if username:
        payload["username"] = _clip(username, 80)
    if avatar_url:
        payload["avatar_url"] = str(avatar_url)
    if content:
        payload["content"] = _clip(content, _LIM["content"])
    if embeds_list:
        payload["embeds"] = embeds_list[:10]
    if tts is not None:
        payload["tts"] = bool(tts)
    if thread_name:
        payload["thread_name"] = _clip(thread_name, 100)

    return payload, found_url


# ----------------------------------------------------------------------------
# 送信
# ----------------------------------------------------------------------------
class _Result(dict):
    """dict としても `await` しても使える戻り値（sync/async 両対応）"""

    def __bool__(self) -> bool:  # type: ignore[override]
        return bool(self.get("ok"))

    def __await__(self):
        async def _inner():
            return self
        return _inner().__await__()


def _http_post_json(url: str, payload: Dict[str, Any], timeout: float) -> Tuple[int, str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if requests is not None:
        resp = requests.post(
            url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "discord-notify/1.0"},
            timeout=timeout,
        )
        return resp.status_code, resp.text
    if _urlreq is None:
        raise RuntimeError("requests も urllib も使えません")
    req = _urlreq.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "discord-notify/1.0"},
        method="POST",
    )
    with _urlreq.urlopen(req, timeout=timeout) as res:
        return res.status, res.read().decode("utf-8", "replace")


def _send(payload: Dict[str, Any], url: Optional[str], retries: int, timeout: float) -> _Result:
    if not payload:
        LOG.warning("discord_notify: payload が空です")
        return _Result(ok=False, status=0, reason="empty payload")

    if not url:
        LOG.warning("discord_notify: Webhook URL が未設定です（環境変数 %s を設定してください）", _ENV_KEYS[0])
        return _Result(ok=False, status=0, reason="no webhook url")

    last = ""
    for attempt in range(1, max(1, retries) + 1):
        try:
            status, text = _http_post_json(url, payload, timeout)
        except Exception as exc:  # ネットワーク系
            last = repr(exc)
            LOG.warning("discord_notify: 送信失敗 (%s/%s) %s", attempt, retries, exc)
            time.sleep(min(2 ** attempt, 8))
            continue

        if 200 <= status < 300:
            return _Result(ok=True, status=status, reason="ok", payload=payload)
        if status == 429:  # rate limit
            retry_after = 1.0
            try:
                retry_after = float(json.loads(text).get("retry_after", 1.0))
            except Exception:
                pass
            LOG.warning("discord_notify: 429 rate limit → %.1fs 待機", retry_after)
            time.sleep(min(retry_after, 10) + 0.2)
            last = text
            continue
        if status == 204:
            return _Result(ok=True, status=status, reason="no content", payload=payload)

        last = text
        LOG.warning("discord_notify: HTTP %s (%s/%s) %s", status, attempt, retries, text[:300])
        if 400 <= status < 500 and status != 429:
            break
        time.sleep(min(2 ** attempt, 8))

    return _Result(ok=False, status=0, reason=last or "failed", payload=payload)


def post_embedded(*args: Any, **kwargs: Any) -> _Result:
    """Discord Webhook に送信する。戻り値は truthy 判定も await も可能。"""
    url = kwargs.pop("webhook_url", None) or kwargs.pop("webhook", None)
    retries = int(kwargs.pop("retries", _RETRIES))
    timeout = float(kwargs.pop("timeout", _TIMEOUT))
    payload, found_url = build_payload(*args, **kwargs)
    return _send(payload, webhook_url(url or found_url), retries, timeout)


# エイリアス（どの名前で import されてもOK）
post_embed = post_embedded
send_embed = post_embedded
send_discord = post_embedded
send_discord_message = post_embedded
send_notification = post_embedded
post_notification = post_embedded
notify_discord = post_embedded


def notify(message: Any = None, **kwargs: Any) -> _Result:
    """本文だけ送る簡易版: notify("エラー") / notify("タイトル", "本文")"""
    if message is not None and "content" not in kwargs and "embed" not in kwargs and "title" not in kwargs:
        kwargs["content"] = message
    elif message is not None and "title" not in kwargs:
        kwargs["title"] = message
    return post_embedded(**kwargs)


def notify_error(title: str, error: Any, **kwargs: Any) -> _Result:
    """例外・エラー通知用: notify_error("処理失敗", e)"""
    return post_embedded(
        title=str(title),
        description=f"```\n{_clip(repr(error), 1500)}\n```",
        color=0xE74C3C,
        **kwargs,
    )


# ----------------------------------------------------------------------------
# async 版（discord.py など await で呼ばれる場合用）
# ----------------------------------------------------------------------------
async def post_embedded_async(*args: Any, **kwargs: Any) -> _Result:
    return await asyncio.to_thread(post_embedded, *args, **kwargs)


apost_embedded = post_embedded_async
send_embed_async = post_embedded_async


async def notify_async(message: Any = None, **kwargs: Any) -> _Result:
    return await asyncio.to_thread(notify, message, **kwargs)


# ----------------------------------------------------------------------------
# 動作確認: python discord_notify.py
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    print("webhook:", webhook_url())
    demo = build_embed(
        "テスト通知",
        "discord_notify.py から送信できています。",
        color=0x2ECC71,
        fields=[("状態", "OK", True), ("時刻", str(_dt.datetime.now()), True)],
    )
    print("embed:", json.dumps(demo, ensure_ascii=False, indent=2))
    print("result:", post_embedded(demo))
