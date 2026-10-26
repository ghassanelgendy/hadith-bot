#!/usr/bin/env python3
"""Scheduled job to post queued Arabic sharh comment on TikTok video at 8:00 PM Cairo Time (17:00 UTC)."""

import json
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

from config import PENDING_COMMENT_FILE, SESSION_FILE
from openclaw_notify import send_whatsapp
from tiktok_poster import (
    STEALTH_JS,
    USER_AGENT,
    _browser,
    post_comment,
)


def main():
    if not PENDING_COMMENT_FILE.exists():
        print("No pending comment found. Nothing to post.")
        return 0

    try:
        with open(PENDING_COMMENT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        print(f"Error reading {PENDING_COMMENT_FILE}: {exc}")
        return 1

    post_url = data.get("post_url")
    sharh = data.get("sharh")
    hadith_num = data.get("hadith_number", "")
    collection = data.get("collection", "")

    if not post_url or not sharh:
        print("Invalid pending comment data (missing post_url or sharh). Removing file.")
        PENDING_COMMENT_FILE.unlink(missing_ok=True)
        return 0

    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting scheduled 8:00 PM comment post for Hadith {collection} #{hadith_num}...")
    print(f"Target video: {post_url}")

    success = False
    with sync_playwright() as pw:
        browser = _browser(pw, headless=True, channel="chrome")
        try:
            ctx = browser.new_context(
                storage_state=str(SESSION_FILE),
                user_agent=USER_AGENT,
                viewport={"width": 1280, "height": 900},
            )
            ctx.add_init_script(STEALTH_JS)
            success = post_comment(ctx, post_url, sharh, max_attempts=3)
        except Exception as exc:
            print(f"Error executing post_comment: {exc}")
        finally:
            browser.close()

    if success:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Successfully posted comment on {post_url}!")
        PENDING_COMMENT_FILE.unlink(missing_ok=True)
        try:
            send_whatsapp(f"✅ Comment posted successfully!\nHadith #{hadith_num}\n{post_url}")
        except Exception as e:
            print(f"WhatsApp notification error: {e}")
        return 0
    else:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Failed to post comment after retries on {post_url}")
        try:
            send_whatsapp(f"⚠️ Failed to post scheduled comment for Hadith #{hadith_num}\n{post_url}")
        except Exception as e:
            print(f"WhatsApp notification error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
