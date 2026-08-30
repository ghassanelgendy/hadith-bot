import os
import subprocess
import sys
import time

from config import BASE_DIR

OPENCLAW_HOME = BASE_DIR.parent / ".openclaw"
GATEWAY_CONTAINER = "openclaw-openclaw-gateway-1"
IMAGE = "ghcr.io/openclaw/openclaw:2026.7.1"
WHATSAPP_TARGET = os.environ.get("WHATSAPP_TARGET", "+201120766619")
WHATSAPP_ACCOUNT = "default"


# Retry schedule (seconds) after a failed send. The OpenClaw WhatsApp health
# watchdog restarts the gateway within ~6 minutes of a disconnect, so retries
# are spread wide enough to land after the heal.
RETRY_DELAYS = (15, 90, 300)


def send_whatsapp(message, retries=len(RETRY_DELAYS)):
    if not WHATSAPP_TARGET:
        print("[notify] WHATSAPP_TARGET not set - skipping notification", file=sys.stderr)
        return True
    cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        f"container:{GATEWAY_CONTAINER}",
        "-v",
        f"{OPENCLAW_HOME}:/home/node/.openclaw",
        "-e",
        "HOME=/home/node",
        "-e",
        "OPENCLAW_HOME=/home/node",
        IMAGE,
        "node",
        "dist/index.js",
        "message",
        "send",
        "--channel",
        "whatsapp",
        "--account",
        WHATSAPP_ACCOUNT,
        "--target",
        WHATSAPP_TARGET,
        "--message",
        message,
        "--json",
    ]
    for attempt in range(1, retries + 1):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        except Exception as exc:
            print(f"[notify] attempt {attempt}: failed to run: {exc}", file=sys.stderr)
            ok = False
        else:
            ok = proc.returncode == 0 and '"messageId"' in proc.stdout
            print(
                f"[notify] attempt {attempt}: {'sent' if ok else 'FAILED rc=' + str(proc.returncode)} "
                f"stdout={proc.stdout.strip()[:160]} stderr={proc.stderr.strip()[:160]}",
                file=sys.stderr if not ok else sys.stdout,
            )
        if ok:
            return True
        if attempt < retries:
            delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)]
            print(f"[notify] attempt {attempt} failed - retrying in {delay}s", file=sys.stderr)
            time.sleep(delay)
    return False
