import hashlib
import json
import os
import socket
import threading
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from config import OUTPUT_DIR, SESSION_FILE, STATE_FILE
from hadith_picker import load_state, pick_today, preview_next
from image_gen import generate_image, generate_layers, pick_palette
from manual_login import manual_login
from tiktok_poster import build_caption, login_password, login_qr, post_video
from video_gen import generate_video

HOST = "0.0.0.0"
PORT = int(os.environ.get("DASHBOARD_PORT", "1517"))
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "1234")
TOKEN = hashlib.sha256(PASSWORD.encode()).hexdigest()
COOKIE = "hadith_token"
LOG_FILE = OUTPUT_DIR / "bot.log"

LOCAL_TZ = ZoneInfo("Africa/Cairo")


def now_str():
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


_job = {"status": "idle", "message": "", "started": None, "finished": None, "cancel": False, "thread": None}
_job_lock = threading.Lock()

# ── preview cache ──────────────────────────────────────────────────────────
_preview_cache = {"value": None, "ts": 0}
_PREVIEW_TTL = 60  # seconds between re-picks


def _cached_preview():
    now = time.time()
    if now - _preview_cache["ts"] < _PREVIEW_TTL and _preview_cache["value"] is not None:
        return _preview_cache["value"]
    result = preview_next()
    _preview_cache["value"] = result
    _preview_cache["ts"] = now
    return result


def _invalidate_preview():
    _preview_cache["ts"] = 0
    _preview_cache["value"] = None


def log(line):
    LOG_FILE.parent.mkdir(exist_ok=True)
    ts = now_str()
    entry = f"[{ts}] {line}"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(entry + "\n")
    print(entry, flush=True)


def _reset_stuck():
    thread = _job.get("thread")
    if _job["status"] == "running" and thread and not thread.is_alive():
        log("Job thread died unexpectedly - resetting")
        _job["status"] = "error"
        _job["message"] = "Job thread died - reset"
        _job["finished"] = now_str()


def run_job(name, fn):
    with _job_lock:
        _reset_stuck()
        if _job["status"] not in ("idle", "done", "error", "cancelled"):
            return False
        _job["status"] = "running"
        _job["message"] = name
        _job["started"] = now_str()
        _job["finished"] = None
        _job["cancel"] = False

    def worker():
        try:
            log(f"Job started: {name}")
            fn()
            with _job_lock:
                _job["status"] = "done" if not _job["cancel"] else "cancelled"
                _job["message"] = "Completed" if not _job["cancel"] else "Cancelled"
                _job["finished"] = now_str()
            log(f"Job finished: {name}")
        except BaseException as exc:
            log(f"Job failed: {name} -> {exc}")
            with _job_lock:
                _job["status"] = "cancelled" if _job["cancel"] else "error"
                _job["message"] = str(exc)
                _job["finished"] = now_str()
        finally:
            _invalidate_preview()

    thread = threading.Thread(target=worker, daemon=True)
    with _job_lock:
        _job["thread"] = thread
    thread.start()
    return True


def cancel_job():
    with _job_lock:
        _reset_stuck()
        if _job["status"] == "running":
            _job["cancel"] = True
            return True
        return False


def _palette_for_next_post():
    state = load_state()
    palette = pick_palette(avoid=state.get("last_palette"))
    state["last_palette"] = palette
    json.dump(state, open(STATE_FILE, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    return palette


def job_generate():
    hadith = pick_today()
    if hadith is None:
        raise RuntimeError("No unused hadiths left. Delete state.json to start over.")
    generate_image(hadith, OUTPUT_DIR / "hadith.png", palette=_palette_for_next_post())
    log(f"Generated card for {hadith['id']}")


def job_post():
    hadith = pick_today()
    if hadith is None:
        raise RuntimeError("No unused hadiths left. Delete state.json to start over.")
    palette = _palette_for_next_post()
    generate_image(hadith, OUTPUT_DIR / "hadith.png", palette=palette)
    bg, txt = generate_layers(
        hadith, OUTPUT_DIR / "hadith_bg.png", OUTPUT_DIR / "hadith_text.png", palette=palette
    )
    video = generate_video(bg, txt, OUTPUT_DIR / "hadith.mp4")
    caption = build_caption(hadith)
    post_video(video, caption, post_number=hadith.get("post_number", 1))
    log(f"Posted {hadith['id']}")


def job_generate_next():
    nxt = preview_next()
    if nxt is None:
        raise RuntimeError("No unused hadiths left. Delete state.json to start over.")
    generate_image(nxt, OUTPUT_DIR / "hadith_next.png")
    bg, txt = generate_layers(
        nxt, OUTPUT_DIR / "hadith_next_bg.png", OUTPUT_DIR / "hadith_next_text.png"
    )
    generate_video(bg, txt, OUTPUT_DIR / "hadith_next.mp4")
    with open(OUTPUT_DIR / "next_preview.json", "w") as f:
        json.dump({"id": nxt["id"], "post_number": nxt.get("post_number", 1)}, f)
    log(f"Generated next-post preview #{nxt.get('post_number', 1)} ({nxt['id']})")


def job_login():
    qr_path = OUTPUT_DIR / "login_qr.png"
    if qr_path.exists():
        qr_path.unlink()

    def cancel_check():
        return _job.get("cancel", False)

    ok = login_qr(qr_path, login_timeout_seconds=600, cancel_check=cancel_check)
    if _job.get("cancel"):
        raise RuntimeError("Cancelled by user")
    if not ok:
        raise RuntimeError("Timed out waiting for TikTok QR scan.")


def job_login_password(username, password):
    def cancel_check():
        return _job.get("cancel", False)

    result = login_password(username, password, login_timeout_seconds=300, cancel_check=cancel_check)
    if _job.get("cancel"):
        raise RuntimeError("Cancelled by user")
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "Login failed."))


def job_manual_login():
    def cancel_check():
        return _job.get("cancel", False)

    ok, reason = manual_login(login_timeout_seconds=900, cancel_check=cancel_check)
    if _job.get("cancel"):
        raise RuntimeError("Cancelled by user")
    if not ok:
        raise RuntimeError(reason or "Manual login failed.")


def job_reset_counter():
    """Reset state so posting restarts from hadith #1."""
    state = {"posted": [], "last_date": None, "today": None, "history": []}
    json.dump(state, open(STATE_FILE, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    for fname in (
        "hadith_next.png", "hadith_next.mp4", "next_preview.json",
        "hadith.png", "hadith_bg.png", "hadith_text.png",
        "hadith_next_bg.png", "hadith_next_text.png",
    ):
        p = OUTPUT_DIR / fname
        if p.exists():
            p.unlink()
    _invalidate_preview()
    log("Counter reset — will start from hadith #1 next post")


def status_payload():
    with _job_lock:
        _reset_stuck()
        job = dict(_job)
        job.pop("thread", None)
    state = load_state()
    today = state.get("today")
    today_date = state.get("last_date")
    posted_today = today_date == date.today().isoformat()
    image = OUTPUT_DIR / "hadith.png"
    history = []
    for entry in reversed(state.get("history", [])):
        collection = entry.get("collection", "")
        label = f"Sahih al-{collection.title()}" if collection else ""
        history.append(
            {
                "date": entry.get("date"),
                "ref": f"{label} - Hadith {entry.get('hadith_number', '')}",
                "book": entry.get("book", ""),
            }
        )
    qr_file = OUTPUT_DIR / "login_qr.png"

    # Use cached preview to avoid re-parsing large datasets on every poll
    nxt = _cached_preview()
    next_image = OUTPUT_DIR / "hadith_next.png"
    next_video = OUTPUT_DIR / "hadith_next.mp4"
    next_info = {}
    if nxt:
        next_post_number = nxt.get("post_number", 1)
        next_info = {
            "post_number": next_post_number,
            "caption": build_caption(nxt),
        }
        next_sidecar = {}
        try:
            next_sidecar = json.loads((OUTPUT_DIR / "next_preview.json").read_text())
        except Exception:
            pass
        next_info["video_available"] = next_video.exists()
        next_info["image_available"] = next_image.exists()
        next_info["stale"] = (
            not next_video.exists()
            or not next_image.exists()
            or next_sidecar.get("post_number") != next_post_number
        )
    return {
        "job": job,
        "session": SESSION_FILE.exists(),
        "qr_available": qr_file.exists(),
        "qr_updated_at": qr_file.stat().st_mtime if qr_file.exists() else 0,
        "today": {
            "date": today_date,
            "is_today": posted_today,
            "caption": build_caption(today) if today else None,
            "image_available": image.exists(),
        },
        "next": next_info,
        "history": history[-50:],
        "logs": tail_log(100),
        "total_posted": len(state.get("history", [])),
    }


def tail_log(n):
    if not LOG_FILE.exists():
        return []
    lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-n:]


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 53))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "localhost"


PAGE_LOGIN = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Daily Hadith Bot - Login</title>
<style>
body{background:#0a1f15;color:#f5f2eb;font-family:Georgia,serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.card{background:#152e22;border:1px solid #d4af5f;border-radius:12px;padding:40px;text-align:center;width:320px}
h1{color:#e8c882;font-size:22px;margin:0 0 20px}
input{padding:12px;width:100%;box-sizing:border-box;border:1px solid #5a7a66;border-radius:6px;background:#0a1f15;color:#f5f2eb;margin-bottom:16px;font-size:16px}
button{background:#d4af5f;border:none;border-radius:6px;padding:12px;width:100%;font-size:16px;font-weight:bold;color:#0a1f15;cursor:pointer}
.err{color:#ff7b72;margin-top:12px}
</style></head><body>
<div class="card">
<h1>Daily Hadith Bot</h1>
<form method="post" action="/login">
<input type="password" name="password" placeholder="Password" autofocus required>
<button type="submit">Unlock</button>
</form>
<div class="err" id="err"></div>
</div>
<script>
const u = new URLSearchParams(location.search);
if (u.get('bad')) document.getElementById('err').textContent = 'Wrong password';
</script>
</body></html>"""

PAGE_DASHBOARD = r"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Daily Hadith Bot</title>
<style>
:root{--bg:#0a1f15;--card:#152e22;--gold:#d4af5f;--gold2:#e8c882;--text:#f5f2eb;--muted:#aab9aa;--err:#ff7b72;--ok:#7bd88f}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:Georgia,serif;margin:0;padding:24px}
.wrap{max-width:860px;margin:0 auto}
header{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:20px}
h1{color:var(--gold2);font-size:24px;margin:0}
.badge{padding:6px 14px;border-radius:20px;font-size:13px;font-weight:bold}
.badge.ok{background:#13321f;color:var(--ok);border:1px solid var(--ok)}
.badge.no{background:#33161a;color:var(--err);border:1px solid var(--err)}
.card{background:var(--card);border:1px solid #3f5f4d;border-radius:12px;padding:20px;margin-bottom:16px}
h2{color:var(--gold);font-size:18px;margin:0 0 14px}
img.preview{width:100%;max-width:340px;border-radius:8px;display:block;margin:0 auto 16px;border:1px solid #3f5f4d}
video.preview{width:100%;max-width:340px;border-radius:8px;display:block;margin:0 auto 16px;border:1px solid #3f5f4d}
.caption{white-space:pre-wrap;background:#0a1f15;border:1px solid #3f5f4d;border-radius:8px;padding:14px;font-size:14px;line-height:1.6}
.row{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px;align-items:center}
button{background:var(--gold);border:none;border-radius:8px;padding:11px 18px;font-size:15px;font-weight:bold;color:#0a1f15;cursor:pointer;transition:opacity .15s,transform .1s;position:relative}
button:active{transform:scale(.97)}
button.secondary{background:#2a4a3a;color:var(--text)}
button.danger{background:#5a2430;color:#ffb3ac}
button:disabled{opacity:.45;cursor:not-allowed;transform:none}
.spinner{display:none;width:13px;height:13px;border:2px solid rgba(0,0,0,.25);border-top-color:#0a1f15;border-radius:50%;animation:spin .7s linear infinite;margin-left:7px;vertical-align:middle;display:inline-block}
button:not(.loading) .spinner{visibility:hidden}
button.loading .spinner{visibility:visible}
@keyframes spin{to{transform:rotate(360deg)}}
#jobline{margin-top:12px;padding:10px 14px;border-radius:8px;font-size:14px;background:#0a1f15;border:1px solid #3f5f4d}
#jobline.running{border-color:var(--gold);color:var(--gold2)}
#jobline.error{border-color:var(--err);color:var(--err)}
#jobline.done{border-color:var(--ok);color:var(--ok)}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #2f4a3d}
th{color:var(--gold);font-weight:bold}
.muted{color:var(--muted);font-size:13px}
pre.log{background:#0a1f15;border:1px solid #3f5f4d;border-radius:8px;padding:12px;font-size:12px;overflow-x:auto;max-height:260px;white-space:pre-wrap}
a{color:var(--gold2)}
input[type=text],input[type=password]{padding:10px;width:100%;border:1px solid #5a7a66;border-radius:6px;background:#0a1f15;color:#f5f2eb;font-size:14px;margin-bottom:8px}
/* Toast */
#toast{position:fixed;bottom:28px;left:50%;transform:translateX(-50%) translateY(20px);background:#1a3a2a;border:1px solid var(--gold);color:var(--text);padding:12px 24px;border-radius:10px;font-size:14px;opacity:0;pointer-events:none;transition:opacity .25s,transform .25s;z-index:999;max-width:90vw;text-align:center}
#toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
#toast.tok{border-color:var(--ok);color:var(--ok)}
#toast.terr{border-color:var(--err);color:var(--err)}
/* Reset modal */
#resetModal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:1000;align-items:center;justify-content:center}
#resetModal.open{display:flex}
#resetBox{background:#152e22;border:1px solid var(--err);border-radius:12px;padding:28px;max-width:380px;text-align:center}
#resetBox h3{color:var(--err);margin:0 0 12px}
#resetBox p{color:var(--muted);font-size:14px;margin:0 0 20px}
#resetBox .row{justify-content:center}
</style></head><body>
<div class="wrap">
<header>
<h1>Daily Hadith Bot</h1>
<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
<span class="badge no" id="sessionBadge">TikTok: ?</span>
<span class="muted" id="postedCount" style="font-size:12px"></span>
<button class="secondary" onclick="logout()" style="padding:7px 14px;font-size:13px">Logout</button>
</div>
</header>

<div class="card">
<h2>Next Post <span class="muted" id="nextNum" style="font-weight:normal"></span>
<label id="videoToggleWrap" style="display:none;float:right;font-size:13px;color:var(--muted);font-weight:normal;cursor:pointer">
<input type="checkbox" id="videoToggle" style="vertical-align:middle"> Video preview
</label></h2>
<div id="nextArea"><div class="muted">Loading...</div></div>
</div>

<div class="card">
<h2>Today's Post</h2>
<div id="todayArea"><div class="muted">Loading...</div></div>
<div id="jobline" style="display:none"></div>
</div>

<div class="card">
<h2>TikTok Authentication</h2>
<p class="muted" id="authHint">Checking session...</p>
<div id="qrArea" style="display:none;text-align:center">
<p class="muted">Scan this QR code with the TikTok app on your phone<br>(Profile &rarr; &ldquo;Scan QR code&rdquo;). Refreshes automatically.</p>
<img id="qrImg" class="preview" style="max-width:240px" alt="login QR">
</div>
<div class="row">
<button id="loginBtn" onclick="api('/api/tiktok-login',this)">Login with QR<span class="spinner"></span></button>
<button id="manualBtn" onclick="api('/api/manual-login',this)">Login in browser<span class="spinner"></span></button>
<button class="danger" id="logoutBtn" onclick="api('/api/tiktok-logout',this)">Clear session<span class="spinner"></span></button>
</div>
<details style="margin-top:14px">
<summary style="cursor:pointer;color:#e8c882">Or login with password</summary>
<div style="margin-top:10px">
<input type="text" id="pwUser" placeholder="Phone / email / username">
<input type="password" id="pwPass" placeholder="Password">
<button onclick="pwLogin()">Log in</button>
<div class="muted" id="pwMsg" style="margin-top:8px"></div>
</div>
</details>
</div>

<div class="card">
<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:14px">
<h2 style="margin:0">Posting History</h2>
<button class="danger" onclick="openReset()" style="padding:7px 14px;font-size:13px">&#8635; Reset counter</button>
</div>
<div id="historyArea"><div class="muted">Loading...</div></div>
</div>

<div class="card">
<h2>Logs</h2>
<pre class="log" id="logArea">Loading...</pre>
</div>
</div>

<div id="toast"></div>

<div id="resetModal">
<div id="resetBox">
<h3>Reset Counter?</h3>
<p>This clears all posting history and starts fresh from hadith #1.<br>Your TikTok session will NOT be affected.</p>
<div class="row">
<button class="danger" onclick="doReset()">Yes, reset</button>
<button class="secondary" onclick="closeReset()">Cancel</button>
</div>
</div>
</div>

<script>
let nextKey='';
let showVideo=false;
let _lastJobStatus='idle';
let _lastJobName='';
try{ showVideo=(localStorage.getItem('showVideo')==='1'); }catch(e){}

document.addEventListener('DOMContentLoaded',()=>{
  const vt=document.getElementById('videoToggle');
  if(!vt) return;
  vt.addEventListener('change',(e)=>{
    showVideo=e.target.checked;
    try{ localStorage.setItem('showVideo',showVideo?'1':'0'); }catch(err){}
    nextKey='';refresh();
  });
});

function showToast(msg,cls,ms){
  const t=document.getElementById('toast');
  t.textContent=msg;
  t.className='show '+(cls||'');
  clearTimeout(t._tid);
  t._tid=setTimeout(()=>{ t.className=''; },ms||4000);
}

function esc(s){ return s.replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c])); }

async function jf(r){
  if(r.url.endsWith('/login')){location.href='/login';throw new Error('redirect')}
  return r.json();
}

async function refresh(){
  try{
    const s=await jf(await fetch('/api/status'));

    const badge=document.getElementById('sessionBadge');
    badge.className='badge '+(s.session?'ok':'no');
    badge.textContent='TikTok: '+(s.session?'Authenticated':'Not authenticated');
    document.getElementById('authHint').textContent=s.session
      ?'Session active. Bot can post automatically.'
      :'No session saved. Click Login to TikTok to get a QR code.';
    const pc=document.getElementById('postedCount');
    if(s.total_posted!=null) pc.textContent='Total posted: '+s.total_posted;

    const qrRunning=s.job.status==='running'&&s.job.message==='TikTok login';
    document.getElementById('qrArea').style.display=qrRunning?'':'none';
    if(qrRunning&&s.qr_available){
      const q=document.getElementById('qrImg');
      if(q.dataset.ts!==String(s.qr_updated_at)){q.src='/api/login-qr?ts='+s.qr_updated_at;q.dataset.ts=String(s.qr_updated_at)}
    }

    const t=s.today;
    const area=document.getElementById('todayArea');
    if(t.caption){
      let html='';
      if(t.image_available) html+=`<img class="preview" src="/hadith.png?ts=${Date.now()}">`;
      html+=`<div class="caption">${esc(t.caption)}</div>`;
      html+=`<div class="muted" style="margin-top:8px">${t.is_today?'Picked today':'Last picked'} - ${t.date||''}</div>`;
      html+=`<div class="row">
        <button id="genBtn" onclick="doGenerate()">Generate card<span class="spinner"></span></button>
        <button id="postBtn" onclick="doPost()">Post now<span class="spinner"></span></button>
      </div>`;
      area.innerHTML=html;
      const busy=s.job.status==='running';
      document.getElementById('postBtn').disabled=!s.session||busy;
      document.getElementById('genBtn').disabled=busy;
    }else{
      area.innerHTML=`<div class="muted">No hadith picked today yet.</div>
        <div class="row"><button id="genBtn" onclick="doGenerate()">Pick and generate<span class="spinner"></span></button></div>`;
      if(s.job.status==='running') document.getElementById('genBtn').disabled=true;
    }

    const n=s.next;
    const nArea=document.getElementById('nextArea');
    document.getElementById('nextNum').textContent=n.post_number?'- Post #'+n.post_number:'';
    const vt=document.getElementById('videoToggle');
    document.getElementById('videoToggleWrap').style.display=n.post_number?'':'none';
    vt.checked=showVideo;
    const key=n.post_number+'|'+(n.image_available?'1':'0')+'|'+(n.video_available?'1':'0')+'|'+(n.stale?'1':'0');
    if(key!==nextKey){
      nextKey=key;
      if(n.post_number){
        let html='';
        if(showVideo&&n.video_available){
          html+=`<video class="preview" controls autoplay muted loop playsinline src="/hadith_next.mp4?ts=${Date.now()}"></video>`;
        }else{
          html+=`<img class="preview" src="/hadith_next.png?ts=${Date.now()}">`;
        }
        if(n.stale) html+=`<div class="muted" style="margin-bottom:10px">Rebuilding preview...</div>`;
        html+=`<div class="caption">${esc(n.caption)}</div>`;
        html+=`<div class="row"><button class="secondary" onclick="api('/api/generate-next',this)">Rebuild preview<span class="spinner"></span></button></div>`;
        nArea.innerHTML=html;
      }else{
        nArea.innerHTML='<div class="muted">No unused hadiths left.</div>';
      }
    }
    if(n.stale&&s.job.status!=='running'){
      fetch('/api/generate-next',{method:'POST'}).catch(()=>{});
    }

    const j=s.job;
    // Toast on job completion
    if(_lastJobStatus==='running'&&j.status==='done'){
      showToast('✅ '+_lastJobName+' completed!','tok');
    } else if(_lastJobStatus==='running'&&(j.status==='error'||j.status==='cancelled')){
      showToast('❌ '+j.message,'terr',7000);
    }
    if(j.status==='running') _lastJobName=j.message;
    _lastJobStatus=j.status;

    const line=document.getElementById('jobline');
    line.className=j.status==='running'?'running':j.status==='error'?'error':j.status==='done'?'done':j.status==='cancelled'?'error':'';
    line.textContent=j.status==='running'
      ?'Running: '+j.message+' (started '+j.started+')...'
      :(j.status==='done'?'✅ Last job completed.':j.status==='error'?'❌ Error: '+j.message:'Idle.');
    line.style.display=j.status==='idle'?'none':'';
    if(j.status==='running'){
      const cb=document.createElement('button');
      cb.textContent='Cancel';cb.className='danger';cb.style.marginLeft='10px';
      cb.onclick=async()=>{await fetch('/api/cancel',{method:'POST'});refresh()};
      line.appendChild(cb);
    }

    document.getElementById('historyArea').innerHTML=s.history.length
      ?'<table><tr><th>#</th><th>Date</th><th>Reference</th><th>Book</th></tr>'+
        s.history.map((h,i)=>`<tr><td>${s.total_posted-i}</td><td>${h.date}</td><td>${h.ref}</td><td>${h.book}</td></tr>`).join('')+'</table>'
      :'<div class="muted">Nothing posted yet.</div>';

    document.getElementById('logArea').textContent=s.logs.join('\n')||'(empty)';
    const l=document.getElementById('logArea');l.scrollTop=l.scrollHeight;
  }catch(err){
    if(err.message!=='redirect') console.error('refresh failed',err);
  }
}

async function api(url,btn){
  if(btn){ btn.disabled=true; btn.classList.add('loading'); }
  try{
    const r=await fetch(url,{method:'POST'});
    if(r.status===409){ showToast('A job is already running — wait for it.','terr'); return; }
    await refresh();
  }finally{
    if(btn){ btn.disabled=false; btn.classList.remove('loading'); }
  }
}

async function doPost(){
  const btn=document.getElementById('postBtn');
  if(!btn||btn.disabled) return;
  btn.disabled=true; btn.classList.add('loading');
  try{
    const r=await fetch('/api/post',{method:'POST'});
    if(r.status===409){ showToast('A job is already running — wait for it.','terr'); return; }
    await refresh();
  }finally{
    btn.disabled=false; btn.classList.remove('loading');
  }
}
async function doGenerate(){
  const btn=document.getElementById('genBtn');
  if(!btn||btn.disabled) return;
  btn.disabled=true; btn.classList.add('loading');
  try{
    const r=await fetch('/api/generate',{method:'POST'});
    if(r.status===409){ showToast('A job is already running — wait for it.','terr'); return; }
    await refresh();
  }finally{
    btn.disabled=false; btn.classList.remove('loading');
  }
}

async function pwLogin(){
  const user=document.getElementById('pwUser').value.trim();
  const pass=document.getElementById('pwPass').value;
  const msg=document.getElementById('pwMsg');
  if(!user||!pass){msg.textContent='Enter phone/email and password.';return}
  msg.textContent='Logging in... this can take up to a minute.';
  const r=await fetch('/api/tiktok-login-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:user,password:pass})});
  const d=await r.json();
  if(d.ok) msg.textContent='Login started - watch the badge.';
  else if(r.status===409) msg.textContent='A job is already running - wait for it to finish.';
  else msg.textContent='Error: '+(d.error||'unknown');
}

function openReset(){ document.getElementById('resetModal').classList.add('open'); }
function closeReset(){ document.getElementById('resetModal').classList.remove('open'); }
async function doReset(){
  closeReset();
  try{
    const r=await fetch('/api/reset',{method:'POST'});
    const d=await r.json();
    nextKey='';
    if(d.ok) showToast('Counter reset — starting from hadith #1','tok');
    else showToast('Reset failed: '+(d.error||'unknown'),'terr',6000);
  }catch(e){ showToast('Reset failed','terr'); }
  refresh();
}

function logout(){location.href='/logout'}
setInterval(refresh,3000); refresh();
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "HadithBot/1.0"

    def log_message(self, fmt, *args):
        pass

    def _cookies(self):
        c = SimpleCookie()
        c.load(self.headers.get("Cookie", ""))
        return c

    def _authorized(self):
        token = self._cookies().get(COOKIE)
        return token is not None and token.value == TOKEN

    def _send(self, code, body=b"", ctype="text/html", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if extra:
            for k, v in extra:
                self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _redirect(self, location):
        self._send(302, b"", extra=[("Location", location)])

    def _json(self, payload, code=200):
        self._send(code, json.dumps(payload).encode(), ctype="application/json")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/login":
            return self._send(200, PAGE_LOGIN.encode())
        if not self._authorized():
            return self._redirect("/login")
        if path == "/":
            return self._send(200, PAGE_DASHBOARD.encode())
        if path == "/logout":
            extra = [("Set-Cookie", f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")]
            return self._send(200, b"Logged out. <a href='/login'>Login again</a>", extra=extra)
        if path == "/api/status":
            return self._json(status_payload())
        if path == "/hadith.png":
            img = OUTPUT_DIR / "hadith.png"
            if not img.exists():
                return self._send(404, b"not found")
            return self._send(200, img.read_bytes(), ctype="image/png")
        if path == "/hadith_next.png":
            img = OUTPUT_DIR / "hadith_next.png"
            if not img.exists():
                return self._send(404, b"not found")
            return self._send(200, img.read_bytes(), ctype="image/png")
        if path == "/hadith_next.mp4":
            vid = OUTPUT_DIR / "hadith_next.mp4"
            if not vid.exists():
                return self._send(404, b"not found")
            return self._send(200, vid.read_bytes(), ctype="video/mp4")
        if path == "/api/login-qr":
            qr = OUTPUT_DIR / "login_qr.png"
            if not qr.exists():
                return self._send(404, b"not found")
            extra = [("Cache-Control", "no-store")]
            return self._send(200, qr.read_bytes(), ctype="image/png", extra=extra)
        return self._send(404, b"not found")

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/login":
            length = int(self.headers.get("Content-Length", 0))
            params = parse_qs(self.rfile.read(length).decode() or "")
            if params.get("password", [""])[0] == PASSWORD:
                extra = [("Set-Cookie", f"{COOKIE}={TOKEN}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax")]
                return self._send(200, b"ok", extra=extra)
            return self._send(200, b"bad")
        if not self._authorized():
            return self._send(401, b"unauthorized")
        if path == "/api/generate":
            ok = run_job("generate card", job_generate)
            return self._json({"ok": ok}, 200 if ok else 409)
        if path == "/api/generate-next":
            ok = run_job("generate next preview", job_generate_next)
            return self._json({"ok": ok}, 200 if ok else 409)
        if path == "/api/post":
            ok = run_job("post to TikTok", job_post)
            return self._json({"ok": ok}, 200 if ok else 409)
        if path == "/api/tiktok-login":
            ok = run_job("TikTok login", job_login)
            return self._json({"ok": ok}, 200 if ok else 409)
        if path == "/api/manual-login":
            ok = run_job("TikTok login (manual browser)", job_manual_login)
            return self._json({"ok": ok}, 200 if ok else 409)
        if path == "/api/tiktok-login-password":
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length).decode() or "{}")
            except Exception:
                data = {}
            username = (data.get("username") or "").strip()
            password = data.get("password") or ""
            if not username or not password:
                return self._json({"ok": False, "error": "username and password required"})
            ok = run_job("TikTok login (password)", lambda: job_login_password(username, password))
            return self._json({"ok": ok}, 200 if ok else 409)
        if path == "/api/cancel":
            return self._json({"ok": cancel_job()})
        if path == "/api/tiktok-logout":
            if SESSION_FILE.exists():
                SESSION_FILE.unlink()
                log("TikTok session cleared")
            return self._json({"ok": True})
        if path == "/api/reset":
            try:
                job_reset_counter()
                return self._json({"ok": True})
            except Exception as exc:
                log(f"Reset failed: {exc}")
                return self._json({"ok": False, "error": str(exc)})
        return self._send(404, b"not found")


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    ip = lan_ip()
    print(f"Daily Hadith Bot dashboard:")
    print(f"  Local:   http://localhost:{PORT}")
    print(f"  Network: http://{ip}:{PORT}")
    print(f"  Password: {PASSWORD}")
    log(f"Dashboard started on port {PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Dashboard stopped")
        server.server_close()


if __name__ == "__main__":
    main()
