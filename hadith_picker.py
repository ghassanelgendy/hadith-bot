import hashlib
import json
import random
import re
from datetime import date, timedelta

from fontTools.ttLib import TTFont

from config import (
    ARABIC_FONT,
    BASE_DIR,
    DATASETS,
    MAX_ARABIC_LEN,
    MAX_ENGLISH_LEN,
    STATE_FILE,
)

REFERENCE_FILE = BASE_DIR / "hadiths_reference.json"

SHORT_TIERS = [200, 320, 460, 640]
MIN_ENGLISH_LEN = 60
MIN_ARABIC_LEN = 30

SALLALLAH = "\u0635\u0644\u0649 \u0627\u0644\u0644\u0647 \u0639\u0644\u064a\u0647 \u0648\u0633\u0644\u0645"

_CMAP = None
_HADITHS_CACHE = None
_REJECTIONS_CACHE = None


def font_chars():
    global _CMAP
    if _CMAP is None:
        _CMAP = set(TTFont(str(ARABIC_FONT)).getBestCmap())
    return _CMAP


def matn_fingerprint(matn):
    """Content-based fingerprint of a matn (Arabic letters only, no diacritics/
    punctuation/spacing). Used to never post the same hadith twice, even when
    the same narration appears in two collections or under another id."""
    norm = "".join(
        c
        for c in matn
        if 0x0621 <= ord(c) <= 0x06EA
    )
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def sanitize(text):
    if not text:
        return text
    keep = font_chars()
    out = []
    for ch in text:
        o = ord(ch)
        if 0x064B <= o <= 0x0652:
            continue
        if ch.isspace():
            out.append(" ")
        elif o in keep:
            out.append(ch)
    return " ".join("".join(out).split()).rstrip(".")


def load_hadiths():
    global _HADITHS_CACHE, _REJECTIONS_CACHE
    if _HADITHS_CACHE is not None:
        return _HADITHS_CACHE
    hadiths = []
    rejected = []

    def reject(collection, hid, inbook, reason):
        rejected.append(
            {
                "id": f"{collection}-{hid}",
                "collection": collection,
                "hadith_number": inbook,
                "reason": reason,
            }
        )

    for collection, path in DATASETS.items():
        data = json.load(open(path, encoding="utf-8"))
        chapters = {c["id"]: c for c in data["chapters"]}
        for h in data["hadiths"]:
            english = h.get("english") or {}
            text = " ".join(str(english.get("text", "")).split())
            arabic = " ".join(str(h.get("arabic", "")).split())
            narrator = english.get("narrator", "").strip()
            hid = h.get("id")
            inbook = h.get("idInBook")
            if not text:
                reject(collection, hid, inbook, "no english text")
                continue
            if "narration about the chain" in text or text.startswith(("(", '"', "…")):
                reject(collection, hid, inbook, "starts with chain/quote, not a standalone narration")
                continue
            if any(p in text for p in ("same chain of transmitters", "with the same chain", "a similar hadith", "as narrated above", "like the above")):
                reject(collection, hid, inbook, "contains another narration (merged/abridged)")
                continue
            if text.startswith("This hadith has been narrated on the authority"):
                reject(collection, hid, inbook, "continuation of a previous narration")
                continue
            if not narrator or len(narrator) > 120 or narrator.startswith("This hadith") or "same chain" in narrator:
                reject(collection, hid, inbook, f"bad narrator field (len {len(narrator)})")
                continue
            isnad, matn = split_matn(arabic)
            if is_duplicate_alias(arabic) or not matn or matn.startswith(CONT_PREFIXES):
                reject(collection, hid, inbook, "duplicate alias or matn starts mid-narration")
                continue
            if re.search(r"\sتَابَعَهُ", matn):
                reject(collection, hid, inbook, "merged narration (تابعه follow-up chains inside)")
                continue
            matn = sanitize(matn.replace("\ufdfa", SALLALLAH))
            isnad = sanitize(isnad.replace("\ufdfa", SALLALLAH))
            if not matn:
                reject(collection, hid, inbook, "matn has no renderable characters in the font")
                continue
            hadiths.append(
                {
                    "id": f"{collection}-{h['id']}",
                    "collection": collection,
                    "hadith_number": hid,
                    "book": chapters.get(h.get("chapterId"), {}).get("english", ""),
                    "narrator": narrator,
                    "english": text,
                    "isnad": isnad,
                    "arabic": matn,
                }
            )
    _HADITHS_CACHE = hadiths
    _REJECTIONS_CACHE = rejected
    return hadiths


def load_rejected():
    load_hadiths()
    return _REJECTIONS_CACHE


ANNA_COMMA = "\u060c \u0623\u064e\u0646\u0651\u064e "
ANNA_RASUL = " \u0623\u064e\u0646\u0651\u064e \u0631\u064e\u0633\u064f\u0648\u0644\u064e "
QALA = "\u0642\u064e\u0627\u0644\u064e "


def split_matn(arabic):
    """Split a hadith's Arabic into (isnad, matn) WITHOUT altering either.

    When the text is quoted, the matn is the LONGEST quoted block - that is
    the actual narration. Everything before its opening quote is the isnad
    (verbatim, so parentheticals like 'وهو يحدث عن فترة الوحي فقال' stay),
    and anything after its closing quote (e.g. تابعه follow-up chains) is
    dropped so no post ever merges two narrations."""
    clean = "".join(c for c in arabic if not "\u200e" <= c <= "\u200f")
    positions = [i for i, ch in enumerate(clean) if ch == '"']
    if len(positions) >= 2:
        pairs = [
            (positions[i], positions[i + 1])
            for i in range(0, len(positions) - (1 if len(positions) % 2 else 0), 2)
        ]
        if pairs:
            prev, last = max(pairs, key=lambda p: p[1] - p[0])
            if last > prev > 0:
                return clean[:prev].strip(), clean[prev + 1 : last].strip()
    for sep, offset in ((ANNA_COMMA, 2), (ANNA_RASUL, 1)):
        i = clean.rfind(sep)
        if i > 0:
            return clean[:i].strip(), clean[i + offset :].strip()
    qala = clean.rfind(QALA)
    if qala > 0:
        return clean[: qala + len(QALA)].strip(), clean[qala + len(QALA) :].strip()
    return "", clean.strip()


CONT_PREFIXES = (
    "\u062b\u064f\u0645\u0651\u064e",
    "\u0648\u064e\u0642\u064e\u0627\u0644\u064e",
    "\u0641\u064e\u0642\u064e\u0627\u0644\u064e",
    "\u0648\u064e\u0642\u064e\u062f\u0652",
)


def is_duplicate_alias(arabic):
    tail = arabic[-60:]
    markers = (
        "\u0628\u0650\u0645\u0650\u062b\u0652\u0644\u0650",
        "\u0628\u0650\u0646\u064e\u062d\u0652\u0648\u0650",
        "\u0628\u0650\u0645\u064e\u0639\u0652\u0646\u064e\u0649",
        "\u0628\u0650\u0645\u064e\u0639\u0652\u0646\u064e\u0627\u0647\u064f",
        "\u0628\u0650\u0647\u064e\u0630\u064e\u0627 \u0627\u0644\u0652\u0625\u0650\u0633\u0652\u0646\u064e\u0627\u062f\u0650",
        "\u0628\u0650\u0647\u064e\u0630\u064e\u0627 \u0627\u0644\u0625\u0650\u0633\u0652\u0646\u064e\u0627\u062f\u0650",
    )
    if any(m in tail for m in markers):
        return True
    return "\u0648\u064e\u0632\u064e\u0627\u062f\u064e" in arabic[-160:]


def _hash_for_posted_id(hid):
    pool = {h["id"]: h for h in load_hadiths()}
    if hid in pool:
        return matn_fingerprint(pool[hid]["arabic"])
    collection, num = hid.rsplit("-", 1)
    if collection not in DATASETS:
        return None
    data = json.load(open(DATASETS[collection], encoding="utf-8"))
    raw = next((h for h in data["hadiths"] if str(h.get("id")) == num), None)
    if not raw:
        return None
    isnad, matn = split_matn(" ".join(str(raw.get("arabic", "")).split()))
    return matn_fingerprint(sanitize(matn.replace("\ufdfa", SALLALLAH)))


def load_state():
    if STATE_FILE.exists():
        state = json.load(open(STATE_FILE, encoding="utf-8"))
        if "history" not in state:
            state["history"] = [
                {"date": state.get("last_date") or "unknown", "id": h_id}
                for h_id in state.get("posted", [])
            ]
        if "matn_hashes" not in state:
            state["matn_hashes"] = [
                fp for fp in (_hash_for_posted_id(hid) for hid in state.get("posted", [])) if fp
            ]
            save_state(state)
        return state
    return {"posted": [], "last_date": None, "today": None, "history": [], "matn_hashes": []}


def save_state(state):
    json.dump(state, open(STATE_FILE, "w", encoding="utf-8"), indent=2, ensure_ascii=False)


def _pick_for_date(date_str, posted, state=None):
    """Pick the shortest still-unposted hadith that fits the card nicely.

    Rules (per user preference):
    - Shorter hadiths first (matn + english length); collection order
      (Bukhari before Muslim) and hadith number only break ties.
    - Hadiths longer than the caps (MAX_ARABIC_LEN / MAX_ENGLISH_LEN) are
      never picked - they are logged as SKIPPED in hadiths_reference.json.
    - The matn is used verbatim from the dataset (never edited), and the
      isnad keeps phrases like 'وهو يحدث عن فترة الوحي فقال' intact.
    - A matn already posted (checked via content fingerprint) is never
      posted again, even under another id or in another collection."""
    hashes = set((state or load_state()).get("matn_hashes", []))
    pool = [
        h
        for h in load_hadiths()
        if h["id"] not in posted
        and matn_fingerprint(h["arabic"]) not in hashes
        and len(h["arabic"]) <= MAX_ARABIC_LEN
        and len(h["arabic"]) >= MIN_ARABIC_LEN
        and len(h["english"]) <= MAX_ENGLISH_LEN
        and len(h["english"]) >= MIN_ENGLISH_LEN
    ]
    pool.sort(
        key=lambda h: (
            len(h["arabic"]) + len(h["english"]),
            0 if h["collection"] == "bukhari" else 1,
            h.get("hadith_number") or 0,
        )
    )
    return pool[0] if pool else None


def build_reference(state=None):
    """Write hadiths_reference.json: every hadith we SKIPPED (with reason)
    and every one we SHARED (with the share date)."""
    state = load_state() if state is None else state
    posted = set(state.get("posted", []))
    hashes = set(state.get("matn_hashes", []))
    today = date.today().isoformat()

    skipped = []
    for r in load_rejected():
        if r["id"] in posted:
            continue
        skipped.append({**r, "date": today})
    for h in load_hadiths():
        if h["id"] in posted:
            continue
        fp = matn_fingerprint(h["arabic"])
        if fp in hashes:
            skipped.append(
                {
                    "id": h["id"],
                    "collection": h["collection"],
                    "hadith_number": h["hadith_number"],
                    "reason": "duplicate of an already-shared hadith (same matn)",
                    "date": today,
                }
            )
            continue
        if len(h["english"]) > MAX_ENGLISH_LEN or len(h["arabic"]) > MAX_ARABIC_LEN:
            skipped.append(
                {
                    "id": h["id"],
                    "collection": h["collection"],
                    "hadith_number": h["hadith_number"],
                    "reason": f"too long: matn {len(h['arabic'])} / english {len(h['english'])} chars (caps {MAX_ARABIC_LEN}/{MAX_ENGLISH_LEN})",
                    "date": today,
                }
            )

    shared = []
    for entry in state.get("history", []):
        shared.append(
            {
                "id": entry.get("id"),
                "collection": entry.get("collection"),
                "hadith_number": entry.get("hadith_number"),
                "date": entry.get("date"),
            }
        )

    ref = {
        "updated": today,
        "skipped": skipped,
        "shared": shared,
    }
    json.dump(ref, open(REFERENCE_FILE, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    return ref


def pick_today():
    state = load_state()
    today = date.today().isoformat()
    if state.get("last_date") == today and state.get("today"):
        build_reference(state)
        return state["today"]

    posted = set(state["posted"])
    choice = _pick_for_date(today, posted, state)
    if choice is None:
        return None

    state["posted"].append(choice["id"])
    state["matn_hashes"] = state.get("matn_hashes", []) + [matn_fingerprint(choice["arabic"])]
    state["today"] = choice
    state["last_date"] = today
    state["history"].append(
        {
            "date": today,
            "id": choice["id"],
            "collection": choice["collection"],
            "hadith_number": choice["hadith_number"],
            "book": choice["book"],
        }
    )
    choice["post_number"] = len(state["history"])
    save_state(state)
    build_reference(state)
    return choice


def preview_next():
    state = load_state()
    today = date.today().isoformat()
    if state.get("last_date") == today:
        date_str = (date.today() + timedelta(days=1)).isoformat()
    else:
        date_str = today
    choice = _pick_for_date(date_str, set(state["posted"]), state)
    if choice is None:
        return None
    choice = dict(choice)
    choice["post_number"] = len(state["history"]) + 1
    return choice
