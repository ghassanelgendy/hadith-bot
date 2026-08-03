from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
ASSETS_DIR = BASE_DIR / "assets"
OUTPUT_DIR = BASE_DIR / "output"
STATE_FILE = BASE_DIR / "state.json"
SESSION_FILE = BASE_DIR / "tiktok_session.json"

DATASETS = {
    "bukhari": DATA_DIR / "bukhari.json",
    "muslim": DATA_DIR / "muslim.json",
}

ARABIC_FONT = ASSETS_DIR / "SA-Hazm-Regular.ttf"
ARABIC_FONT_BOLD = ASSETS_DIR / "SA-Hazm-Bold.ttf"

IMAGE_WIDTH = 1080
IMAGE_HEIGHT = 1920

MAX_ENGLISH_LEN = 600
MAX_ARABIC_LEN = 400

CAPTION_HASHTAGS = ["#hadith", "#islam", "#muslim", "#sunnah", "#dailyhadith", "#quran", "#islamicreminders"]

TIKTOK_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"

# Sound attached to the video. The Studio music-panel UI cannot be automated
# reliably (its overlay/tour blocks clicks and the add button ignores events),
# so instead we search TikTok's music API for a matching sound and MIX its
# audio track into the video with ffmpeg before uploading. The posted video
# then plays the sound as its own audio track.
SOUND_MIX_SEARCH = "ابتهال"
SOUND_MIX_ENABLED = True
SOUND_MIX_VOLUME = 0.85
