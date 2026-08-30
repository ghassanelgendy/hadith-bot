import base64
import json
import random
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright
from zxingcpp import read_barcodes

from config import (
    CAPTION_HASHTAGS,
    OUTPUT_DIR,
    SESSION_FILE,
    SOUND_MIX_ENABLED,
    SOUND_MIX_SEARCH,
    SOUND_MIX_VOLUME,
    TIKTOK_UPLOAD_URL,
)
from hadith_picker import load_state, save_state

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
if (originalQuery) {
  window.navigator.permissions.query = (parameters) =>
    parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(parameters);
}
"""


def _browser(pw, headless=True, channel=None):
    kwargs = dict(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
    )
    if channel:
        kwargs["channel"] = channel
    return pw.chromium.launch(**kwargs)


def _is_logged_in(page):
    if page.locator("[data-e2e='user-info']").count() > 0:
        return True
    if page.locator("a[href='/upload']").count() > 0:
        return True
    cookies = page.context.cookies()
    names = {c["name"] for c in cookies}
    return bool(names & {"sessionid", "sid_guard", "sid_tt", "sessionid_ss"})


def _wait_logged_in(page, login_timeout_seconds, cancel_check=None):
    deadline = time.time() + login_timeout_seconds
    while time.time() < deadline:
        if cancel_check and cancel_check():
            return "cancelled"
        time.sleep(2)
        if _is_logged_in(page):
            return "ok"
    return "timeout"


def login(login_timeout_seconds=600):
    with sync_playwright() as pw:
        browser = _browser(pw, headless=False, channel="chrome")
        context = browser.new_context(user_agent=USER_AGENT)
        context.add_init_script(STEALTH_JS)
        page = context.new_page()
        page.goto("https://www.tiktok.com", wait_until="domcontentloaded")
        print("Please log in to TikTok in the opened browser window.")
        print("Waiting up to", login_timeout_seconds, "seconds for login...")
        result = _wait_logged_in(page, login_timeout_seconds)
        if result != "ok":
            print("Timed out waiting for login.")
            browser.close()
            return False
        time.sleep(3)
        context.storage_state(path=str(SESSION_FILE))
        print(f"Session saved to {SESSION_FILE}")
        browser.close()
        return True


def _login_context(browser):
    context = browser.new_context(
        user_agent=USER_AGENT,
        device_scale_factor=2,
        viewport={"width": 1280, "height": 800},
        locale="en-US",
        timezone_id="Africa/Cairo",
    )
    context.add_init_script(STEALTH_JS)
    return context


def login_password(username, password, login_timeout_seconds=300, cancel_check=None):
    with sync_playwright() as pw:
        browser = _browser(pw, headless=True, channel="chrome")
        context = _login_context(browser)
        page = context.new_page()
        page.goto("https://www.tiktok.com/login?lang=en", wait_until="load", timeout=60000)
        page.wait_for_timeout(5000)

        tab = page.get_by_text("Use phone / email / username").first
        if tab.count():
            tab.click()
            page.wait_for_timeout(2500)
        email_tab = page.get_by_text("Log in with email or username").first
        if email_tab.count():
            email_tab.click()
            page.wait_for_timeout(2500)

        user_field = page.locator('input[name="username"]').first
        if user_field.count():
            user_field.click()
            page.keyboard.type(username, delay=20)
        pass_field = page.locator('input[type="password"]').first
        if pass_field.count():
            pass_field.click()
            page.keyboard.type(password, delay=20)

        login_btn = page.locator('[data-e2e="login-button"]').first
        enabled_at = time.time() + 30
        while time.time() < enabled_at:
            if login_btn.count() and not login_btn.is_disabled():
                break
            page.wait_for_timeout(1000)
        if not login_btn.count() or login_btn.is_disabled():
            browser.close()
            return {"ok": False, "error": "The login form never became ready. TikTok may be rate-limiting this network - wait a while and try again."}
        login_btn.click()

        deadline = time.time() + login_timeout_seconds
        while time.time() < deadline:
            if cancel_check and cancel_check():
                browser.close()
                return {"ok": False, "error": "cancelled"}
            if _is_logged_in(page):
                time.sleep(3)
                context.storage_state(path=str(SESSION_FILE))
                print(f"Session saved to {SESSION_FILE}")
                browser.close()
                return {"ok": True}
            page.wait_for_timeout(1500)
            body = page.locator("body").inner_text()
            if "captcha" in body.lower() or page.locator("iframe[src*='captcha']").count():
                browser.close()
                return {"ok": False, "error": "TikTok showed a CAPTCHA. Password login is blocked for this network - wait and retry, or use the QR login."}
            for hint in [
                "maximum number of attempts",
                "incorrect password",
                "wrong password",
                "account not found",
                "doesn't exist",
                "invalid username",
                "try again later",
                "too many attempts",
                "suspended",
            ]:
                if hint in body.lower():
                    browser.close()
                    return {"ok": False, "error": f"TikTok rejected the login: '{hint}'. Check the credentials and try again later."}
        browser.close()
        return {"ok": False, "error": "Timed out waiting for login. Check the credentials and try again."}


def login_qr(qr_path, login_timeout_seconds=600, cancel_check=None):
    with sync_playwright() as pw:
        browser = _browser(pw, headless=True, channel="chrome")
        context = _login_context(browser)
        page = context.new_page()
        page.goto("https://www.tiktok.com/login?lang=en", wait_until="load", timeout=60000)
        page.wait_for_timeout(5000)

        qr_tab = page.get_by_text("Use QR code").first
        if qr_tab.count():
            qr_tab.click()
            page.wait_for_timeout(3000)

        last_qr_text = None
        deadline = time.time() + login_timeout_seconds
        while time.time() < deadline:
            if cancel_check and cancel_check():
                browser.close()
                return False
            try:
                qr = page.locator("canvas").first
                if qr.count():
                    tmp = qr_path.with_suffix(".tmp.png")
                    qr.screenshot(path=str(tmp))
                    found = read_barcodes(Image.open(str(tmp)))
                    if found:
                        text = found[0].text
                        if text != last_qr_text:
                            tmp.replace(qr_path)
                            last_qr_text = text
                            print("QR updated:", text[:40])
                    else:
                        tmp.unlink(missing_ok=True)
            except Exception as exc:
                print("qr screenshot error:", exc)
            if _is_logged_in(page):
                break
            time.sleep(3)

        result = _wait_logged_in(page, login_timeout_seconds=deadline - time.time(), cancel_check=cancel_check)
        if result != "ok":
            print("Login result:", result)
            browser.close()
            return False
        time.sleep(3)
        context.storage_state(path=str(SESSION_FILE))
        print(f"Session saved to {SESSION_FILE}")
        browser.close()
        return True


def _clean_pbuh(text):
    return text.replace("\u200f", "").replace("(ﷺ)", "(peace be upon him)").replace("(way peace be upon him)", "(peace be upon him)")


def _session_headers():
    cookies = []
    try:
        data = json.load(open(SESSION_FILE, encoding="utf-8"))
        for c in data.get("cookies", []):
            if c.get("domain", "").endswith("tiktok.com") and c.get("name") in (
                "sessionid", "sessionid_ss", "sid_guard", "sid_tt", "uid_tt", "ttwid", "s_v_web_id", "msToken",
            ):
                cookies.append(f"{c['name']}={c['value']}")
    except Exception:
        pass
    return {
        "User-Agent": USER_AGENT,
        "Referer": "https://www.tiktok.com/",
        "Cookie": "; ".join(cookies),
    }


def _find_sound(search):
    """Resolve a sound from TikTok. If the search is a direct TikTok music URL,
    navigates to it in Playwright and intercepts the CDN audio URL from network traffic.
    Otherwise, searches the TikTok music API."""
    if search.startswith("http://") or search.startswith("https://") or "/music/" in search:
        print("find_sound: resolving direct URL:", search)
        with sync_playwright() as pw:
            browser = _browser(pw, headless=True, channel="chrome")
            try:
                context = browser.new_context(user_agent=USER_AGENT)
                context.add_init_script(STEALTH_JS)
                page = context.new_page()
                audio_urls = []
                
                def on_req(request):
                    u = request.url
                    # Match any .mp3 link, mime_type=audio link, or standard tiktokcdn CDN audio URLs
                    if ("mime_type=audio" in u or ".mp3" in u or "audio_mpeg" in u or ("tiktokcdn.com" in u and "/tos/" in u)) and u not in audio_urls:
                        audio_urls.append(u)
                        
                page.on("request", on_req)
                page.goto(search, wait_until="networkidle", timeout=60000)
                
                # Wait up to 10 seconds for the audio request to appear
                for _ in range(20):
                    if audio_urls:
                        break
                    page.wait_for_timeout(500)
                
                title = page.title() or "Locked Sound"
                if " - " in title:
                    title, author = title.split(" - ", 1)
                    title = title.strip()
                    author = author.replace("| TikTok", "").strip()
                else:
                    title = title.replace("| TikTok", "").strip()
                    author = "Unknown"
                
                if not audio_urls:
                    print("find_sound: failed to capture audio URL for", search)
                    return None
                    
                print(f"find_sound: successfully resolved to '{title}' by '{author}'")
                return {
                    "title": title,
                    "author": author,
                    "url": audio_urls[0],
                    "duration": 60,
                }
            except Exception as e:
                print("find_sound: error resolving URL:", e)
                return None
            finally:
                browser.close()

    with sync_playwright() as pw:
        browser = _browser(pw, headless=True, channel="chrome")
        try:
            context = browser.new_context(storage_state=str(SESSION_FILE), user_agent=USER_AGENT)
            context.add_init_script(STEALTH_JS)
            page = context.new_page()
            page.goto(TIKTOK_UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            res = page.evaluate(
                """async (kw) => {
                    const r = await fetch('/api/search/music/full/?aid=1988&keyword='
                        + encodeURIComponent(kw) + '&cursor=0&count=10&search_channel=tiktok_music_create',
                        {credentials: 'include'});
                    const j = await r.json();
                    const items = (j.data || []).map(it => it.music_info)
                        .filter(it => it && it.play_url && it.play_url.uri && it.duration >= 30);
                    if (!items.length) return null;
                    return items;
                }""",
                search,
            )
            if not res:
                return None
            # Avoid repeating the same track on consecutive posts.
            state = load_state()
            last = state.get("last_sound")
            it = random.choice(res)
            for _ in range(6):
                if it["title"] != last:
                    break
                it = random.choice(res)
            state["last_sound"] = it["title"]
            save_state(state)
            return {
                "title": it["title"],
                "author": it["author"],
                "url": it["play_url"]["uri"],
                "duration": it["duration"],
            }
        except Exception as exc:
            print("find_sound: api search failed:", exc)
            return None
        finally:
            browser.close()


def _mix_sound(video_path, search=SOUND_MIX_SEARCH, volume=SOUND_MIX_VOLUME):
    """Embed a TikTok sound into the video with ffmpeg so the posted video
    reliably plays it. Returns the new video path (original is untouched)."""
    video_path = Path(video_path)
    sound = _find_sound(search)
    if not sound:
        print(f"mix_sound: no sound found for {search!r}, posting without sound")
        return str(video_path)
    print(f"mix_sound: found {sound['title']!r} by {sound.get('author', '')} ({sound.get('duration')}s)")
    audio_tmp = Path(video_path).parent / f"_sound_{video_path.stem}.bin"
    try:
        req = urllib.request.Request(sound["url"], headers=_session_headers())
        audio_tmp.write_bytes(urllib.request.urlopen(req, timeout=90).read())
        print(f"mix_sound: downloaded {audio_tmp.stat().st_size} bytes")
        from imageio_ffmpeg import get_ffmpeg_exe

        out = video_path.with_name(f"{video_path.stem}_snd.mp4")
        cmd = [
            get_ffmpeg_exe(), "-y",
            "-i", str(video_path),
            "-i", str(audio_tmp),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "160k",
            "-af", f"volume={volume}",
            "-shortest",
            str(out),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print("mix_sound: ffmpeg failed:", r.stderr[-200:])
            return str(video_path)
        print("mix_sound: mixed audio into", out.name)
        return str(out)
    except Exception as exc:
        print(f"mix_sound: failed ({type(exc).__name__}: {exc}) - posting without sound")
        return str(video_path)
    finally:
        audio_tmp.unlink(missing_ok=True)


def build_caption(hadith):
    lines = []
    text = _clean_pbuh(hadith["english"])
    if len(text) > 480:
        text = text[:477].rsplit(" ", 1)[0] + "..."
    lines.append(text)
    if hadith["narrator"]:
        lines.append(_clean_pbuh(hadith["narrator"]))
    collection = f"Sahih al-{hadith['collection'].title()}"
    lines.append(f"{collection} - Hadith {hadith['hadith_number']}")
    lines.append(" ".join(CAPTION_HASHTAGS))
    return "\n".join(lines)


SOUND_SEARCHES = [
    "quran karim",
    "تلاوة القران",
    "surah islamic",
    "دعاء",
    "nasheed islamic",
    "قرآن",
    "المنشاوي",
]


def attach_sound(page, post_number=1):
    """LEGACY: attach a sound through the Studio music panel.

    No longer called - the panel's add button ignores clicks when the page is
    automated (its overlay/tour blocks real clicks and the handlers ignore
    synthetic ones). Sound is now embedded into the video with ffmpeg via
    _mix_sound() before uploading. Kept for reference / manual debugging."""
    try:
        search = SOUND_SEARCHES[(post_number - 1) % len(SOUND_SEARCHES)]
        item_idx = (post_number - 1) % 3
        btns = page.locator("button")
        clicked = False
        for i in range(btns.count()):
            if btns.nth(i).inner_text().strip() == "Sounds":
                btns.nth(i).click()
                clicked = True
                break
        if not clicked:
            print("attach_sound: no Sounds button")
            return None
        page.wait_for_timeout(2500)
        box = page.locator('input[placeholder="Search sounds"]').first
        box.wait_for(timeout=15000)
        box.click()
        box.type(search, delay=40)
        page.wait_for_timeout(3000)
        sug = page.locator(".MusicPanelSugList__item").first
        sug.click(timeout=5000)
        page.wait_for_timeout(6000)
        items = page.locator(".MusicPanelMusicItem__wrap")
        if items.count() == 0:
            print("attach_sound: no results")
            return None
        n = min(item_idx, items.count() - 1)
        sound = items.nth(n).locator(".MusicPanelMusicItem__infoBasicTitle").inner_text()
        items.nth(n).locator(".MusicPanelMusicItem__operation button").evaluate("el => el.click()")
        page.wait_for_timeout(3000)
        page.keyboard.press("Escape")
        print(f"attach_sound: attached '{sound}'")
        return sound
    except Exception as exc:
        print(f"attach_sound: failed ({type(exc).__name__})")
        return None


def set_cover(page, image_path):
    try:
        page.get_by_text("Edit cover", exact=True).click(timeout=10000)
        page.wait_for_timeout(4000)
        cover_input = None
        fis = page.locator('input[type="file"]')
        for i in range(fis.count()):
            accept = fis.nth(i).get_attribute("accept") or ""
            if "image" in accept:
                cover_input = fis.nth(i)
                break
        if cover_input is None:
            print("set_cover: no image input found")
            return False
        cover_input.set_input_files(str(image_path))
        page.wait_for_timeout(8000)
        page.get_by_text("Save", exact=True).last.click(timeout=10000)
        page.wait_for_timeout(3000)
        print("set_cover: cover uploaded")
        return True
    except Exception as exc:
        print(f"set_cover: failed ({type(exc).__name__})")
        return False


def _studio_username(page):
    """Return the @username (not numeric ID) for profile verification."""
    try:
        data = page.evaluate(
            """async () => {
                const r = await fetch('/tiktokstudio/api/web/user', {credentials: 'include'});
                return await r.json();
            }"""
        )
        prof = data.get("userBaseInfo", {}).get("UserProfile", {})
        base = prof.get("UserBase") or {}
        for key in ("uniqueId", "unique_id", "UniqueId", "username"):
            v = base.get(key)
            if v:
                return str(v)
    except Exception:
        pass
    # Fallback: the studio header links to the profile as /@username
    try:
        p2 = page.context.new_page()
        try:
            p2.goto("https://www.tiktok.com/tiktokstudio", wait_until="domcontentloaded", timeout=60000)
            p2.wait_for_timeout(4000)
            for a in p2.locator("a[href^='/@']").all():
                href = a.get_attribute("href") or ""
                if href.startswith("/@"):
                    return href[2:].strip().split("?")[0]
        finally:
            p2.close()
    except Exception:
        pass
    return ""


def _newest_own_video(context, username):
    p = context.new_page()
    try:
        p.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded", timeout=60000)
        p.wait_for_timeout(4000)
        links = p.locator("a[href*='/video/']")
        if links.count() > 0:
            href = links.first.get_attribute("href") or ""
            return href.rstrip("/").split("/")[-1]
    except Exception:
        pass
    finally:
        p.close()
    return None


def _already_posted(context, username, before):
    """Check once whether a new video has appeared since `before`. Used right
    before retrying a round, so a slow-to-propagate success from a prior
    round (error screen shown, but the post actually went through) is
    detected instead of blindly re-uploading and creating a duplicate."""
    if not username or not before:
        return None
    now = _newest_own_video(context, username)
    if now and now != before:
        return now
    return None


def _studio_error(page):
    try:
        body = page.locator("body").inner_text() or ""
    except Exception:
        return False
    return "something went wrong" in body.lower() and "retry" in body.lower()


def _click_retry(page):
    retry = page.get_by_role("button", name="Retry", exact=True).first
    for _ in range(3):
        try:
            if retry.count():
                retry.click(timeout=5000)
                page.wait_for_timeout(6000)
            if not _studio_error(page):
                return True
        except Exception:
            pass
        page.wait_for_timeout(3000)
    return not _studio_error(page)


def _wait_upload_done(page, timeout=180):
    """TikTok Studio shows 'Uploading...', '%' and 'seconds left' until the video
    is fully uploaded. Clicking Post before that fails the upload, so we must
    wait until the progress text disappears."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _studio_error(page):
            if not _click_retry(page):
                raise RuntimeError("studio error screen during upload")
            deadline = time.time() + timeout
            continue
        body = page.locator("body").inner_text() or ""
        busy = bool(re.search(r"uploading|\d+(\.\d+)?%|\d+ (seconds?|mins?|hours?) (left|remaining)|[0-9.]+ ?[KMGT]?B/", body, re.I))
        if not busy:
            return True
        page.wait_for_timeout(2000)
    return False


def _recover(page, video_path):
    retry = page.get_by_role("button", name="Retry", exact=True).first
    try:
        if retry.count():
            retry.click(timeout=5000)
            page.wait_for_timeout(6000)
    except Exception:
        pass
    fi = page.locator('input[type="file"]').first
    for _ in range(2):
        try:
            fi.wait_for(state="attached", timeout=20000)
            fi.set_input_files(str(video_path))
            return True
        except Exception:
            page.goto(TIKTOK_UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
            fi = page.locator('input[type="file"]').first
    return False


def _is_initial_screen(page):
    """True when Studio reset to the empty 'Select videos to upload' screen,
    which happens after an upload failure/crash."""
    try:
        body = page.locator("body").inner_text() or ""
    except Exception:
        return False
    return "select videos to upload" in body.lower() or "or drag and drop them here" in body.lower()


def _fill_editor(page, caption, post_number, cover_path, video_path, max_attempts=3, include_extras=True):
    for attempt in range(1, max_attempts + 1):
        try:
            deadline = time.time() + 150
            while True:
                if _studio_error(page):
                    print(f"editor attempt {attempt}: error screen, recovering")
                    if not _recover(page, video_path):
                        raise RuntimeError("studio error screen; retry did not recover")
                    deadline = time.time() + 150
                    continue
                if _is_initial_screen(page):
                    print(f"editor attempt {attempt}: studio reset to upload screen, re-uploading")
                    if not _recover(page, video_path):
                        raise RuntimeError("studio reset; retry did not recover")
                    deadline = time.time() + 150
                    continue
                # Studio uses [data-e2e="post_caption"] or a contenteditable div
                caption_ready = (
                    page.locator("[data-e2e='post_caption']").first.count() > 0
                    or page.locator("[contenteditable='true']").first.count() > 0
                )
                if caption_ready:
                    break
                if time.time() > deadline:
                    raise RuntimeError("caption box never appeared after upload")
                page.wait_for_timeout(1500)

            for _ in range(3):
                try:
                    modal_cancel = page.locator("[role='dialog'] button:has-text('Cancel')").first
                    modal_cancel.click(timeout=3000)
                    page.wait_for_timeout(1200)
                except Exception:
                    break
            page.evaluate(
                """() => {
                    document.querySelectorAll('[data-test-id="overlay"], #react-joyride-portal').forEach(e => e.remove());
                }"""
            )

            # Prefer the Studio caption element; fall back to generic contenteditable
            box = page.locator("[data-e2e='post_caption']").first
            if box.count() == 0:
                box = page.locator("[contenteditable='true']").first
            box.click()
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            lines = caption.split("\n")
            for i, line in enumerate(lines):
                page.keyboard.type(line, delay=12)
                page.wait_for_timeout(1500)
                if i < len(lines) - 1:
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(1200)
            print("caption typed")

            if include_extras:
                set_cover(page, cover_path)
                print("cover set")

            # Post must not be clicked while the video is still uploading,
            # otherwise TikTok Studio shows "Something went wrong" or discards it.
            if not _wait_upload_done(page):
                raise RuntimeError("video upload never completed in the editor")
            print("upload done")
            return True
        except Exception as exc:
            print(f"editor attempt {attempt} failed: {type(exc).__name__}: {exc}")
            if not _recover(page, video_path):
                return False
            # After a crash, skip the flaky sound/cover extras and just post.
            include_extras = False
    return False


def post_video(video_path, caption, post_number=1, headless=True, max_rounds=3):
    """Post a video to TikTok Studio (web upload).

    Reliable flow, verified against the live Studio UI:
      0. Mix the configured sound (e.g. an ابتهال) into the video with ffmpeg
         so the post always has audio - the Studio music panel cannot be
         automated (its overlay/tour blocks clicks, the add button ignores
         events).
      1. Open the upload page and attach the video via the file input.
      2. Wait for the editor (caption box) to appear; recover by clicking
         "Retry" or re-uploading if Studio shows "Something went wrong" or
         resets to the empty "Select videos to upload" screen.
      3. Type the caption line by line (Enter between lines).
      4. Set the cover - optional; skipped after a crash.
      5. Wait until the upload finishes (progress text like "36.25%" or
         "seconds left" disappears) - clicking Post earlier FAILS the upload.
      6. Click the [data-e2e="post_video_button"] Post button (JS click);
         it can sit at any x position, so no x-position filter is used.
      7. Confirm via the dialog's own Post button if a dialog appears.
      8. Verify success: a new video appears on the profile, or the page
         navigated away from /tiktokstudio/upload (e.g. to /content).
      9. On failure, re-run the whole round (up to max_rounds) in the same
         browser - no recursion, no orphan browsers.
    """
    upload_path = video_path
    if SOUND_MIX_ENABLED:
        upload_path = _mix_sound(video_path, search=SOUND_MIX_SEARCH, volume=SOUND_MIX_VOLUME)
    cover_path = str(Path(video_path).with_suffix(".png"))

    with sync_playwright() as pw:
        browser = _browser(pw, headless=headless, channel="chrome")
        try:
            context = browser.new_context(storage_state=str(SESSION_FILE), user_agent=USER_AGENT)
            context.add_init_script(STEALTH_JS)
            page = context.new_page()

            username = None
            before = None
            include_extras = True

            for round_no in range(1, max_rounds + 1):
                print(f"post round {round_no}/{max_rounds}")

                if round_no > 1:
                    # A prior round may have actually posted despite showing an
                    # error/reset (TikTok's profile can be slow to update) - check
                    # before re-uploading to avoid a duplicate post.
                    posted_id = _already_posted(context, username, before)
                    if posted_id:
                        post_url = f"https://www.tiktok.com/@{username}/video/{posted_id}"
                        print("Posted successfully (detected before retrying):", post_url)
                        return post_url

                page.goto(TIKTOK_UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
                file_input = page.locator('input[type="file"]').first
                try:
                    file_input.wait_for(state="attached", timeout=45000)
                except Exception:
                    page.goto(TIKTOK_UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
                    file_input = page.locator('input[type="file"]').first
                    file_input.wait_for(state="attached", timeout=45000)
                file_input.set_input_files(str(upload_path))

                if not _fill_editor(
                    page, caption, post_number, cover_path, str(upload_path),
                    include_extras=include_extras,
                ):
                    page.screenshot(path=str(OUTPUT_DIR / f"post_fail_{int(time.time())}.png"))
                    body = page.locator("body").inner_text()[:300]
                    raise RuntimeError(
                        f"Studio kept crashing during editing. url={page.url} body={body!r}"
                    )

                if username is None:
                    username = _studio_username(page)
                    before = _newest_own_video(context, username) if username else None

                post_button = None
                for name, loc, require_right in (
                    # [data-e2e="post_video_button"] is THE Post button; it can sit
                    # at any x position depending on the panel layout, no x filter.
                    ("e2e", page.locator('[data-e2e="post_video_button"]').first, False),
                    ("post", page.get_by_role("button", name="Post", exact=True).first, True),
                    ("publish", page.get_by_role("button", name="Publish", exact=True).first, True),
                ):
                    if loc.count() == 0:
                        continue
                    try:
                        loc.wait_for(state="visible", timeout=20000)
                        bb = loc.bounding_box()
                        if bb and bb["y"] > 200 and (not require_right or bb["x"] + bb["width"] > 800):
                            post_button = loc
                            break
                    except Exception:
                        continue
                if post_button is None:
                    if _studio_error(page):
                        print("post button: error screen, recovering")
                        _click_retry(page)
                        continue  # fresh round: re-upload
                    if _is_initial_screen(page):
                        print("post button: studio reset to upload screen")
                        continue  # fresh round: re-upload
                    page.screenshot(path=str(OUTPUT_DIR / f"post_fail_{int(time.time())}.png"))
                    body = page.locator("body").inner_text()[:600]
                    raise RuntimeError(f"Post button not found. url={page.url} body={body!r}")

                # Use JS click to bypass the Timeline/VideoClip overlay that intercepts pointer events
                post_button.evaluate("el => el.click()")

                page.wait_for_timeout(5000)
                for _ in range(2):
                    try:
                        page.locator("[role='dialog'] button:has-text('Cancel')").first.click(timeout=2000)
                        page.wait_for_timeout(1500)
                    except Exception:
                        break
                # The confirm dialog (when present) has its own Post button; the main
                # one is hidden at that point, so prefer a Post button in a dialog.
                confirm = None
                for loc in (
                    page.locator("[role='dialog'] button:has-text('Post')").first,
                    page.locator("[data-floating-ui-portal] button:has-text('Post')").first,
                    page.get_by_role("button", name="Post", exact=True).last,
                ):
                    if loc.count() == 0:
                        continue
                    try:
                        loc.wait_for(state="visible", timeout=4000)
                        bb = loc.bounding_box()
                        if bb:
                            confirm = loc
                            break
                    except Exception:
                        continue
                if confirm is not None:
                    try:
                        confirm.click(timeout=3000)
                    except Exception:
                        pass
                page.wait_for_timeout(3000)

                if _studio_error(page):
                    # An error screen right after clicking Post does NOT mean the
                    # post failed - it can appear even though the video was
                    # created (that's how hadith 3 got posted twice). Check the
                    # profile for the new video before considering a retry.
                    print("studio error after clicking Post - checking if the post went through")
                    if username and before:
                        for _ in range(10):
                            time.sleep(10)
                            now = _newest_own_video(context, username)
                            if now and now != before:
                                post_url = f"https://www.tiktok.com/@{username}/video/{now}"
                                print("Posted successfully (new video appeared):", post_url)
                                return post_url
                    print("no new video after error screen -> retrying in a fresh round")
                    continue

                posted_id = None
                if username and before:
                    for _ in range(15):
                        time.sleep(12)
                        now = _newest_own_video(context, username)
                        if now and now != before:
                            posted_id = now
                            break
                time.sleep(5)
                url = page.url
                if posted_id:
                    post_url = f"https://www.tiktok.com/@{username}/video/{posted_id}"
                    print("Posted successfully:", post_url)
                    return post_url
                # If we navigated away from the upload page, treat it as success
                if "tiktokstudio/upload" not in url and "upload" not in url:
                    if username and before:
                        now = _newest_own_video(context, username)
                        if now and now != before:
                            post_url = f"https://www.tiktok.com/@{username}/video/{now}"
                            print("Posted successfully (navigated away):", post_url)
                            return post_url
                    print("Posted successfully (navigated away from upload page):", url)
                    return None
                # Still on upload page - studio may have crashed; retry once more
                include_extras = False
                print("still on upload page - retrying without sound/cover")

            page.screenshot(path=str(OUTPUT_DIR / f"post_fail_{int(time.time())}.png"))
            raise RuntimeError("Posting may have failed - still on upload page after posting.")
        finally:
            browser.close()
