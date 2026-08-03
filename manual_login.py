import glob
import os
import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from config import SESSION_FILE

CDP_PORT = 9222
CHROME_PROFILE = Path.home() / ".tiktok-chrome"

SESSION_COOKIES = {"sessionid", "sid_guard", "sid_tt", "sessionid_ss"}


def session_displays():
    xs = sorted(glob.glob("/tmp/.X11-unix/X*"), key=os.path.getmtime, reverse=True)
    displays = []
    for x in xs:
        num = os.path.basename(x)[1:]
        if num.isdigit() and num != "0":
            displays.append(f":{num}")
    return displays


def xserver_auth(display):
    num = display.lstrip(":")
    try:
        out = subprocess.run(
            ["pgrep", "-af", f"Xorg .*-auth .*:{num}|Xvnc .*:{num}"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except Exception:
        return None
    for line in out.splitlines():
        if "-auth" in line:
            for i, part in enumerate(line.split()):
                if part == "-auth" and i + 1 < len(line.split()):
                    return line.split()[i + 1]
    return None


def chrome_alive():
    try:
        with sync_playwright() as pw:
            b = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
            b.close()
            return True
    except Exception:
        return False


def launch_chrome(display):
    env = dict(
        os.environ,
        DISPLAY=display,
        XAUTHORITY=xserver_auth(display) or str(Path.home() / ".Xauthority"),
        HOME=str(Path.home()),
    )
    cmd = [
        "google-chrome",
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={CHROME_PROFILE}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--window-size=1250,880",
        "https://www.tiktok.com/login?lang=en",
    ]
    return subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_for_session(timeout_s=900, cancel_check=None):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if cancel_check and cancel_check():
            return False
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
                for context in browser.contexts:
                    names = {c["name"] for c in context.cookies()}
                    if names & SESSION_COOKIES:
                        context.storage_state(path=str(SESSION_FILE))
                        browser.close()
                        return True
                browser.close()
        except Exception:
            pass
        time.sleep(3)
    return False


def manual_login(login_timeout_seconds=900, cancel_check=None):
    if chrome_alive():
        print("Chrome already running with the login profile - reuse it.")
    else:
        display = os.environ.get("DASHBOARD_DISPLAY")
        if not display:
            displays = session_displays()
            display = displays[0] if displays else None
        if not display:
            print(
                "No desktop session found. Connect to the server via Remote Desktop, "
                "then click 'Login in browser' again."
            )
            return False, "No desktop session is connected. Connect via Remote Desktop first, then click again."
        print(f"Launching Chrome on display {display} (profile: {CHROME_PROFILE})")
        proc = launch_chrome(display)
        time.sleep(6)
        if proc.poll() is not None:
            print(f"Chrome exited immediately on display {display} - is the RDP session still active?")
            return False, f"Chrome could not open on display {display} - is the RDP session still active?"

    print("Waiting for you to log in to TikTok in the Chrome window...")
    ok = wait_for_session(login_timeout_seconds, cancel_check=cancel_check)
    if not ok:
        print("Timed out waiting for login in the Chrome window.")
        return False, "Timed out waiting for login in the Chrome window."
    print(f"Session saved to {SESSION_FILE}")
    return True, None
