# -*- coding: utf-8 -*-
"""
panel_bot.py ── Discord に操作パネル（ボタン付き）を出すモジュール

使い方A: app.py に相乗り（推奨）
    import panel_bot
    panel_bot.register_routes(app)          # ボタンが叩くAPIを作る
    panel_bot.start_in_thread()             # botを裏で起動

使い方B: 単体起動
    python panel_bot.py

環境変数:
    DISCORD_BOT_TOKEN   必須。Discord Developer Portal → Bot → Token
    GUILD_ID            推奨。スラッシュコマンドが即時反映される
    PANEL_COMMAND       コマンド名。既定 "panel"（!panel / /panel）
    PANEL_PREFIX        プレフィックス。既定 "!"
    PANEL_CHANNEL_ID    起動時に自動でパネルを貼るチャンネルID
    PANEL_AUTOPOST      既定 "1"（PANEL_CHANNEL_ID 指定時のみ有効）
    APP_BASE_URL        ボタンが叩くURL。既定 http://127.0.0.1:$PORT
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional

try:
    import requests
except Exception:
    requests = None

try:
    import discord
    from discord import app_commands
    from discord.ext import commands
except Exception:
    discord = None

LOG = logging.getLogger("panel_bot")
if not LOG.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# ============ 設定 ============
TOKEN = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("BOT_TOKEN") or ""
PREFIX = os.getenv("PANEL_PREFIX", "!")
PANEL_COMMAND = os.getenv("PANEL_COMMAND", "panel")
GUILD_ID = os.getenv("GUILD_ID")
PANEL_CHANNEL_ID = os.getenv("PANEL_CHANNEL_ID")
APP_BASE_URL = (os.getenv("APP_BASE_URL") or f"http://127.0.0.1:{os.getenv('PORT', '10000')}").rstrip("/")
PANEL_SECRET = os.getenv("PANEL_SECRET", "")

PANEL_TITLE = os.getenv("PANEL_TITLE", "Instagram 操作パネル")

# ボタンのラベル → 叩くパス（増やしたい時はここに足すだけ / 1行5個まで）
ACTIONS: Dict[str, str] = {
    "🔄 状態更新": "/panel/status",
    "▶️ 開始": "/panel/start",
    "⏹️ 停止": "/panel/stop",
    "🔑 再ログイン": "/panel/relogin",
}

# ボタンが叩かれた時に実行する処理（register_routes に渡す）
HOOKS: Dict[str, Callable[[], Any]] = {}


# ============ アプリ側API ============
def call_app(path: str, payload: Optional[dict] = None, timeout: float = 20.0):
    """自アプリのAPIを叩く。戻り値 (ok: bool, message: str)"""
    if requests is None:
        return False, "requests が未インストールです"
    url = APP_BASE_URL + path
    headers = {"X-Panel-Secret": PANEL_SECRET} if PANEL_SECRET else {}
    try:
        r = requests.post(url, json=payload or {}, headers=headers, timeout=timeout)
        return r.ok, f"HTTP {r.status_code} {r.text[:400]}"
    except Exception as exc:
        return False, repr(exc)


def register_routes(app, hooks: Optional[Dict[str, Callable[[], Any]]] = None):
    """Flask にボタン用エンドポイントを生やす。hooks で実処理を差し込む。

    例:
        panel_bot.register_routes(app, {"▶️ 開始": my_start_func})
    """
    global HOOKS
    if hooks:
        HOOKS.update(hooks)

    def _make(key: str, path: str):
        def _view():
            if PANEL_SECRET and os.getenv("PANEL_SECRET"):
                from flask import request
                if request.headers.get("X-Panel-Secret") != PANEL_SECRET:
                    return {"ok": False, "error": "unauthorized"}, 401
            fn = HOOKS.get(key)
            if not callable(fn):
                return {"ok": True, "result": f"{key}: hook 未設定 ({path})"}
            try:
                return {"ok": True, "result": str(fn())}
            except Exception as exc:
                return {"ok": False, "error": repr(exc)}
        return _view

    for key, path in ACTIONS.items():
        app.add_url_rule(
            path,
            endpoint="panel_" + key,
            view_func=_make(key, path),
            methods=["GET", "POST"],
        )
    LOG.info("panel API を登録しました: %s", ", ".join(ACTIONS.values()))
    return app


# ============ パネル表示 ============
def build_panel_embed(ok: Optional[bool] = None, message: str = "") -> "discord.Embed":
    embed = discord.Embed(
        title=PANEL_TITLE,
        color=0x5865F2 if ok is not False else 0xE74C3C,
        timestamp=dt.datetime.now(dt.timezone.utc),
    )
    embed.description = "下のボタンから操作できます。"
    embed.add_field(name="接続先", value=f"`{APP_BASE_URL}`", inline=False)
    if ok is not None:
        mark = "✅" if ok else "❌"
        embed.add_field(name="実行結果", value=f"{mark} {message or '-'}"[:1024], inline=False)
    embed.set_footer(text=f"コマンド: {PREFIX}{PANEL_COMMAND}  /  /{PANEL_COMMAND}")
    return embed


class ActionButton(discord.ui.Button):
    def __init__(self, label: str, path: str):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary,
            custom_id="panel:" + path.strip("/").replace("/", "_"),  # 再起動後も有効
        )
        self.path = path

    async def callback(self, interaction: "discord.Interaction"):
        await interaction.response.defer()  # 3秒制限対策
        ok, text = await asyncio.to_thread(call_app, self.path)
        await interaction.edit_original_response(embed=build_panel_embed(ok=ok, message=text), view=PanelView())


class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # 永続
        for label, path in ACTIONS.items():
            self.add_item(ActionButton(label, path))


# ============ コマンド ============
async def _panel_prefix_cb(ctx):
    await ctx.send(embed=build_panel_embed(), view=PanelView())


async def _panel_slash_cb(interaction: "discord.Interaction"):
    await interaction.response.send_message(embed=build_panel_embed(), view=PanelView())


if discord is not None:
    panel_prefix_cmd = commands.command(name=PANEL_COMMAND, aliases=["パネル", "p"])(_panel_prefix_cb)
    panel_slash_cmd = app_commands.Command(
        name=PANEL_COMMAND,
        description="操作パネルを表示します",
        callback=_panel_slash_cb,
    )
else:  # pragma: no cover
    panel_prefix_cmd = panel_slash_cmd = None


def setup_panel(bot):
    """既にbotインスタンスがある場合はこれを呼ぶだけ"""
    bot.add_view(PanelView())
    if all(c.name != PANEL_COMMAND for c in getattr(bot, "commands", [])):
        bot.add_command(panel_prefix_cmd)
    try:
        bot.tree.add_command(panel_slash_cmd)
    except Exception:
        pass
    return bot


# ============ bot 起動 ============
_bot = None
_thread = None


def create_bot():
    intents = discord.Intents.default()
    intents.message_content = True  # Developer Portal 側でもONが必要
    bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

    async def _setup_hook():
        setup_panel(bot)
        try:
            if GUILD_ID:
                guild = discord.Object(id=int(GUILD_ID))
                bot.tree.copy_global_to(guild=guild)
                await bot.tree.sync(guild=guild)
            else:
                await bot.tree.sync()
            LOG.info("スラッシュコマンドを同期しました")
        except Exception as exc:
            LOG.warning("スラッシュコマンド同期に失敗: %s", exc)

    bot.setup_hook = _setup_hook

    @bot.event
    async def on_ready():
        LOG.info("ログイン完了: %s (id=%s)", bot.user, getattr(bot.user, "id", "?"))
        if PANEL_CHANNEL_ID and os.getenv("PANEL_AUTOPOST", "1") == "1":
            try:
                ch = bot.get_channel(int(PANEL_CHANNEL_ID)) or await bot.fetch_channel(int(PANEL_CHANNEL_ID))
                await ch.send(embed=build_panel_embed(), view=PanelView())
                LOG.info("パネルを自動投稿しました: #%s", getattr(ch, "name", PANEL_CHANNEL_ID))
            except Exception as exc:
                LOG.warning("パネル自動投稿に失敗: %s", exc)

    return bot


def start_in_thread():
    """Flask等から裏でbotを動かす"""
    global _bot, _thread
    if discord is None:
        LOG.warning("discord.py 未インストール: pip install \"discord.py>=2.4.0\"")
        return None
    if not TOKEN:
        LOG.warning("DISCORD_BOT_TOKEN が未設定です")
        return None
    if _thread is not None and _thread.is_alive():
        return _thread

    _bot = create_bot()

    def _runner():
        try:
            _bot.run(TOKEN, log_handler=None)
        except Exception:
            LOG.exception("Discord bot が停止しました")

    _thread = threading.Thread(target=_runner, daemon=True, name="discord-panel-bot")
    _thread.start()
    LOG.info("Discord bot をバックグラウンドで起動しました")
    return _thread


if __name__ == "__main__":
    if discord is None:
        print('先に: pip install "discord.py>=2.4.0" requests')
    else:
        start_in_thread()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("停止します")
