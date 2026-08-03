import os
import subprocess
import sys

from config import BASE_DIR

OPENCLAW_HOME = BASE_DIR.parent / ".openclaw"
GATEWAY_CONTAINER = "openclaw-openclaw-gateway-1"
IMAGE = "ghcr.io/openclaw/openclaw:2026.7.1"
WHATSAPP_TARGET = os.environ.get("WHATSAPP_TARGET", "")
WHATSAPP_ACCOUNT = "default"


def send_whatsapp(message):
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
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except Exception as exc:
        print(f"[notify] failed to run: {exc}", file=sys.stderr)
        return False
    ok = proc.returncode == 0 and '"messageId"' in proc.stdout
    print(
        f"[notify] {'sent' if ok else 'FAILED rc=' + str(proc.returncode)} "
        f"stdout={proc.stdout.strip()[:160]} stderr={proc.stderr.strip()[:160]}",
        file=sys.stderr if not ok else sys.stdout,
    )
    return ok
