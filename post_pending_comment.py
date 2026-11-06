#!/usr/bin/env python3
"""Scheduled job to post queued Arabic sharh comment on TikTok video at 8:00 PM Cairo Time (17:00 UTC)."""

import json
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

from config import PENDING_COMMENT_FILE, SESSION_FILE
from telegram_notify import send_telegram
from tiktok_poster import (
    STEALTH_JS,
    USER_AGENT,
    _browser,
    post_comment,
)


def main():
    # User decision: Do not post separate comments to TikTok (they trigger CAPTCHAs).
    # Sharh is already included directly in the video description.
    PENDING_COMMENT_FILE.unlink(missing_ok=True)
    print("Comment posting disabled; sharh is placed in video description.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
