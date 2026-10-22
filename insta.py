import os
from instagrapi import Client

SESSION = "ig_session.json"
_cl = None

def _client() -> Client:
    global _cl
    if _cl:
        return _cl
    cl = Client()
    if os.path.exists(SESSION):
        cl.load_settings(SESSION)
        cl.login(os.getenv("IG_USER"), os.getenv("IG_PASS"))
        cl.get_timeline_feed()
    else:
        cl.login(os.getenv("IG_USER"), os.getenv("IG_PASS"))
        cl.dump_settings(SESSION)
    _cl = cl
    return cl

def send_tool_link(username: str, url: str):
    cl = _client()
    uid = cl.user_id_from_username(username)
    cl.direct_send(
        f"こんにちは！こちらからログインをお願いします👇\n{url}", [uid])
    return uid
