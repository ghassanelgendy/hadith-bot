# Hadith Bot

Fully automated daily hadith posts on TikTok, sourced from **Sahih al-Bukhari** and
**Sahih al-Muslim**. Each day one short hadith is picked, rendered as an Arabic
calligraphy-style card, converted to a short video with a background recitation
sound, and posted to TikTok — all without manual intervention.

## Data credit

The hadith corpora in `data/` (`bukhari.json`, `muslim.json`) come from the
**[sunnah.com API](https://sunnah.com)** — the largest open repository of
authenticated Islamic texts. The datasets follow the sunnah.com API schema
(`metadata`, `chapters`, `hadiths`) and include Arabic matn, English translation,
isnad, and book/chapter references. We are grateful to the sunnah.com project for
making these public-domain texts freely available. All other content (images,
video, audio mixing) is generated locally.

## How it works

1. **Pick** — `hadith_picker.py` selects the shortest not-yet-used hadith from a
   pool of 10,000+. Long narrations are skipped (Arabic matn over 400 chars or
   English over 600 chars), and narrations are deduplicated by a fingerprint of
   the Arabic text so the same hadith is never posted twice. Every pick is logged
   to `hadiths_reference.json` (`skipped` entries include the reason; `shared`
   entries include the date).
2. **Render** — `image_gen.py` draws the matn, isnad, translation, and reference
   on a 1080×1920 card. Backgrounds use one of 18 gradient palettes, chosen
   randomly without repeating the previous day's.
3. **Convert** — `video_gen.py` turns the image into a 7-second slow-zoom MP4
   (TikTok's web upload only accepts videos).
4. **Sound** — `tiktok_poster.py` searches TikTok for an ابتهال (Islamic vocal)
   recording, picks a random result (never the same one twice in a row), and
   mixes it into the video with ffmpeg.
5. **Post** — a Playwright session with your saved TikTok login uploads the video
   through TikTok Studio, sets the cover, fills the caption (translation +
   reference + hashtags), and clicks Post. The bot verifies the video actually
   appears on the profile before ever retrying, preventing duplicate posts.
   New posts may show as "under review" for a few minutes before going public.
6. **Notify** — after posting, a WhatsApp confirmation is sent (Arabic matn +
   blank line + "Posted • Hadith N" + video URL, without emojis) via the optional OpenClaw WhatsApp gateway.

## Requirements

- Python 3.11+
- Google Chrome installed (the bot launches it via Playwright's `channel="chrome"`)
- Docker (only for the optional WhatsApp notifications)

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Log in to TikTok once so the bot can reuse the session:

```bash
.venv/bin/python run_daily.py --login
```

A Chrome window opens — log in manually, then the session is saved to
`tiktok_session.json` (git-ignored, never committed). Re-run whenever TikTok
invalidates the session.

## Usage

```bash
# Generate today's post without uploading (test)
.venv/bin/python run_daily.py --no-post

# Generate and upload today's post
.venv/bin/python run_daily.py
```

## Scheduling with cron

Add a line like this to `crontab -e` (adjust the project path):

```
0 3 * * * TZ=Africa/Cairo cd /path/to/project && .venv/bin/python run_daily.py >> output/cron.log 2>&1
```

The `TZ=Africa/Cairo` prefix sets the timezone for the cron job (the example
above runs daily at 06:00 Cairo). The `CRON_TZ` variable is unreliable on some
systems, which is why the timezone is applied as a command prefix.

## Web dashboard

A local web UI (`web_ui.py`) controls the whole bot from any device on your
network — run it with `.venv/bin/python web_ui.py` (default port 1517).

- **Auth**: password via the `DASHBOARD_PASSWORD` environment variable
- **Features**: today's post preview and caption, Generate/Post buttons, TikTok
  login (password or QR code scanned with the TikTok app — works remotely),
  posting history, live log viewer
- **Systemd** (survives reboots): a `hadith-dashboard` user service unit

## WhatsApp notifications (optional)

`openclaw_notify.py` sends a message after each post through an OpenClaw
WhatsApp gateway container. Set the recipient with the `WHATSAPP_TARGET`
environment variable (e.g. `WHATSAPP_TARGET=+15551234567`); if it is unset,
notifications are skipped.

## Operations

| File | Purpose |
|---|---|
| `state.json` | Posted hadith IDs, matn hashes, last palette/sound, today's pick (git-ignored) |
| `hadiths_reference.json` | Log of every skipped and shared hadith with dates (git-ignored) |
| `output/` | Generated videos, images, logs, and failure screenshots (git-ignored) |
| `tiktok_session.json` | Saved TikTok login session (git-ignored) |

Delete `state.json` to reset the rotation. Logs live in `output/bot.log` and
`output/cron.log`.

## Caveats

- **Unofficial automation**: browser automation may violate TikTok's Terms of
  Service and the account can be flagged. Keep posts at 1/day and use a genuine
  account.
- **Bot detection**: if uploads fail silently, delete `tiktok_session.json` and
  re-run `--login`.
- **UI changes**: if TikTok Studio's page structure changes, update the selectors
  in `tiktok_poster.py`; failed runs save screenshots to `output/`.
