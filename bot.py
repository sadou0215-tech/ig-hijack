"""
Discord Bot - Render デプロイ用 完全版

入っている安全装置:
  1. トークンの自動サニタイズ (改行/空白/引用符/BOM/"Bot "プレフィックス除去)
  2. トークン形式の検証 + アプリIDのデコード表示 (どのBotのトークンか目視確認できる)
  3. LoginFailure / 401 を握りつぶさず、日本語で原因候補を出す
  4. Message Content Intent を有効化 (コマンドが無反応になる問題を解消)
  5. Render の Web Service でスピンダウンしないよう簡易ヘルスサーバを別スレッド起動
  6. Flask アプリと同居させたい場合は start_bot_in_thread() を import して呼ぶ
"""

from __future__ import annotations

import base64
import logging
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import discord
from discord.ext import commands

# ---------------------------------------------------------------------------
# ログ設定 (Render の Logs に秒単位で出る)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logging.getLogger("discord.http").setLevel(logging.WARNING)
log = logging.getLogger("bot")

# この順で探す。どれか1つ設定されていればOK
ENV_KEYS = ("DISCORD_TOKEN", "DISCORD_BOT_TOKEN", "TOKEN", "BOT_TOKEN")


# ---------------------------------------------------------------------------
# トークン処理
# ---------------------------------------------------------------------------
def sanitize_token(raw: str | None) -> str | None:
    """貼り付けミスで起きる 401 の大半をここで潰す。"""
    if not raw:
        return None
    t = raw.strip().strip("\"'` \t\r\n")
    # 途中に紛れた空白・改行・ゼロ幅スペース・BOM を全除去
    t = re.sub(r"[\s\u200b\ufeff]+", "", t)
    # "Bot xxx" の形で貼られた場合
    if t[:3].lower() == "bot" and t.count(".") == 2:
        t = t[3:]
    # "Bearer xxx" の形で貼られた場合
    if t[:6].lower() == "bearer" and t.count(".") == 2:
        t = t[6:]
    return t or None


def describe_token(t: str) -> str:
    """トークンの素性を可視化する (値そのものは出さない)。"""
    parts = t.split(".")
    out = [f"  ・長さ       : {len(t)} 文字", f"  ・セグメント数: {len(parts)} (正常=3)"]

    if len(parts) >= 1 and parts[0]:
        pad = "=" * (-len(parts[0]) % 4)
        try:
            app_id = base64.urlsafe_b64decode(parts[0] + pad).decode()
            if app_id.isdigit():
                out.append(f"  ・アプリID   : {app_id}")
                out.append(
                    "  ・招待URL    : https://discord.com/oauth2/authorize"
                    f"?client_id={app_id}&scope=bot%20applications.commands&permissions=8"
                )
            else:
                out.append(f"  ・アプリID   : デコード結果が数字でない ({app_id[:16]})")
        except Exception:
            out.append("  ・アプリID   : デコード失敗 (先頭セグメントが壊れています)")

    out.append(f"  ・先頭/末尾  : {t[:6]}...{t[-4:]}")
    return "\n".join(out)


def load_token() -> str:
    found = {k: os.getenv(k) for k in ENV_KEYS}
    raw = next((v for v in found.values() if v), None)

    if not raw:
        log.error("環境変数にトークンがありません。設定済みキー: %s",
                  [k for k, v in found.items() if v] or "なし")
        log.error("Render → Environment に DISCORD_TOKEN を追加し、保存後に Manual Deploy してください。")
        raise SystemExit(1)

    t = sanitize_token(raw)
    if not t:
        log.error("トークンが空、または空白のみです。")
        raise SystemExit(1)

    log.info("トークンを読み込みました:")
    log.info("\n%s", describe_token(t))

    if t.count(".") != 2:
        log.error("トークンの形式が不正です ('.' 区切り3セグメントのはずが %d 個)。", t.count(".") + 1)
        if t.count(".") == 0:
            log.error("→ Client Secret か Application ID を貼っている可能性が高いです。Bot タブの Token を使ってください。")
        elif t.count(".") == 1:
            log.error("→ トークンの一部が欠けています。コピーし直してください。")
        raise SystemExit(1)

    return t


# ---------------------------------------------------------------------------
# Bot 本体
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True   # ← これが無いと prefix コマンドが反応しない
intents.members = True           # メンバー取得を使うなら必要 (Portal で Server Members Intent もON)

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


@bot.event
async def on_ready():
    log.info("=" * 60)
    log.info("ログイン成功: %s (ID: %s)", bot.user, bot.user.id)
    log.info("所属サーバー: %d 件", len(bot.guilds))
    for g in bot.guilds:
        log.info("  ・%s (%s)", g.name, g.id)
    log.info("=" * 60)


@bot.command(name="ping")
async def ping(ctx: commands.Context):
    """疎通確認。"""
    await ctx.reply(f"pong / {round(bot.latency * 1000)}ms")


@bot.command(name="status")
async def status(ctx: commands.Context):
    """Botの状態とトークン情報を表示。"""
    parts = [
        f"**ログイン**: {bot.user}",
        f"**サーバー数**: {len(bot.guilds)}",
        f"**レイテンシ**: {round(bot.latency * 1000)}ms",
        f"**discord.py**: {discord.__version__}",
        f"**Message Content Intent**: {'ON' if bot.intents.message_content else 'OFF'}",
    ]
    await ctx.reply("\n".join(parts))


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply(f"引数が足りません: `{error.param.name}`")
        return
    log.exception("コマンドエラー: %s", error)
    await ctx.reply(f"エラー: `{type(error).__name__}: {error}`")


# ---------------------------------------------------------------------------
# ヘルスサーバ (Render Web Service でスピンダウンさせないため)
# ---------------------------------------------------------------------------
def start_health_server() -> None:
    port = int(os.getenv("PORT", "10000"))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"discord bot alive"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_HEAD(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, fmt, *args):
            pass

    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=srv.serve_forever, name="health", daemon=True).start()
    log.info("ヘルスサーバ起動: http://0.0.0.0:%d", port)


# ---------------------------------------------------------------------------
# 起動
# ---------------------------------------------------------------------------
def run_bot() -> None:
    token = load_token()
    try:
        bot.run(token)
    except discord.LoginFailure as e:
        log.error("-" * 60)
        log.error("LoginFailure: %s", e)
        log.error("原因候補:")
        log.error("  1) トークンが失効している (GitHub に push して Discord が自動 Revoke した)")
        log.error("     → Developer Portal → Bot → Reset Token → Render の環境変数を更新")
        log.error("  2) Bot タブの Token ではなく Client Secret / Application ID を貼っている")
        log.error("  3) 環境変数の値に引用符・空白・改行が残っている")
        log.error("  4) 環境変数を保存したあと再デプロイしていない")
        log.error("-" * 60)
        raise SystemExit(1)
    except discord.HTTPException as e:
        log.error("HTTPException: status=%s code=%s text=%s", e.status, e.code, e.text)
        raise SystemExit(1)


def start_bot_in_thread() -> threading.Thread:
    """Flask アプリの隣で Bot を動かしたいときに呼ぶ。"""
    t = threading.Thread(target=run_bot, name="discord-bot", daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    start_health_server()
    run_bot()
