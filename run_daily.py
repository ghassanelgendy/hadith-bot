import argparse
import json
import sys
import traceback

from hadith_picker import load_state, pick_today
from image_gen import generate_image, generate_layers, pick_palette
from openclaw_notify import send_whatsapp
from tiktok_poster import build_caption, login, post_video
from video_gen import generate_video

from config import OUTPUT_DIR, SESSION_FILE, STATE_FILE


def main():
    parser = argparse.ArgumentParser(description="Post a daily hadith to TikTok")
    parser.add_argument("--login", action="store_true", help="log in to TikTok in a browser and save the session")
    parser.add_argument("--no-post", action="store_true", help="generate the video but do not post")
    args = parser.parse_args()

    if args.login:
        login()
        return

    if not SESSION_FILE.exists() and not args.no_post:
        msg = "No TikTok session found. Run: python run_daily.py --login"
        print(msg)
        if not args.no_post:
            send_whatsapp("❌ TikTok post FAILED\nNo TikTok session saved on the server. Log in via the dashboard (Login to TikTok -> QR).")
        sys.exit(1)

    try:
        hadith = pick_today()
        if hadith is None:
            print("No unused hadiths left. Delete state.json to start over.")
            sys.exit(1)

        state = load_state()
        palette = pick_palette(avoid=state.get("last_palette"))
        state["last_palette"] = palette
        json.dump(state, open(STATE_FILE, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        image_path = generate_image(hadith, OUTPUT_DIR / "hadith.png", palette=palette)
        bg, txt = generate_layers(
            hadith, OUTPUT_DIR / "hadith_bg.png", OUTPUT_DIR / "hadith_text.png", palette=palette
        )
        video_path = generate_video(bg, txt, OUTPUT_DIR / "hadith.mp4")
        print("Created:", video_path)
        caption = build_caption(hadith)
        print("Caption:", caption.replace("\n", " | "))

        if args.no_post:
            print("Skipping upload (--no-post).")
            return

        post_video(video_path, caption, post_number=hadith.get("post_number", 1))
    except Exception as exc:
        traceback.print_exc()
        ref = ""
        try:
            h = pick_today()
            if h:
                ref = f"\n📖 {h['collection']} #{h.get('hadith_number')} (post {h.get('post_number', '?')})"
        except Exception:
            pass
        send_whatsapp(f"❌ TikTok post FAILED\n{str(exc)[:280]}{ref}")
        sys.exit(1)

    send_whatsapp(
        f"{hadith['arabic']}\n"
        f"✅ Posted • Hadith {hadith.get('hadith_number')}"
    )


if __name__ == "__main__":
    main()
