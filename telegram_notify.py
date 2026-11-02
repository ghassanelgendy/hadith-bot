"""Telegram alerts for the hadith bot (stdlib only, plain text, no emojis).

Credentials live in `telegram.json` (git-ignored): {"token": "...", "chat_id": 123}.
Same bot as the Mounieeb pipeline (@MOUNIEEB_bot), kept in this repo's own file so the two
projects share nothing at runtime. Never raises: an alert failing must not fail the post.
"""
import json
import sys
import time
import urllib.parse
import urllib.request

from config import BASE_DIR

CONFIG_FILE = BASE_DIR / "telegram.json"
RETRY_DELAYS = (5, 20, 60)


def _config():
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return cfg["token"], cfg["chat_id"]
    except Exception:
        return None, None


def send_telegram(message, retries=len(RETRY_DELAYS) + 1):
    token, chat_id = _config()
    if not token or not chat_id:
        print("[notify] telegram.json missing or incomplete - alert not sent", file=sys.stderr)
        return False
    data = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": message[:4096], "disable_web_page_preview": "true"}
    ).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30) as resp:
                if json.load(resp).get("ok"):
                    print(f"[notify] attempt {attempt}: sent")
                    return True
        except Exception as exc:  # never print the URL: it contains the token
            print(f"[notify] attempt {attempt}: failed ({type(exc).__name__})", file=sys.stderr)
        if attempt < retries:
            time.sleep(RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)])
    return False
