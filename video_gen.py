import shutil
import subprocess

from PIL import Image

import imageio_ffmpeg

from config import IMAGE_HEIGHT, IMAGE_WIDTH, OUTPUT_DIR

FPS = 30
DURATION = 7
ZOOM_TEXT_START = 0.93
ZOOM_TEXT_END = 1.0


def generate_video(bg_path, text_path, output_path):
    bg = Image.open(bg_path).convert("RGB")
    text = Image.open(text_path).convert("RGBA")

    frames_dir = OUTPUT_DIR / "frames"
    # Clear any leftover frames from a previous failed run
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)
    total = FPS * DURATION
    for i in range(total):
        t = i / (total - 1)
        scale = ZOOM_TEXT_START + (ZOOM_TEXT_END - ZOOM_TEXT_START) * t
        w = int(IMAGE_WIDTH * scale)
        h = int(IMAGE_HEIGHT * scale)
        frame = bg.copy()
        txt = text.resize((w, h), Image.LANCZOS)
        frame.paste(txt, ((IMAGE_WIDTH - w) // 2, (IMAGE_HEIGHT - h) // 2), txt)
        frame.save(frames_dir / f"f{i:04d}.png")
    frames = [f"f{i:04d}.png" for i in range(total)]
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg, "-y",
        "-framerate", str(FPS),
        "-i", str(frames_dir / "f%04d.png"),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", "20",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    for f in frames:
        (frames_dir / f).unlink()
    frames_dir.rmdir()
    return output_path


def main(bg_path, text_path):
    output_path = OUTPUT_DIR / "hadith.mp4"
    return generate_video(bg_path, text_path, output_path)


if __name__ == "__main__":
    print(main(OUTPUT_DIR / "hadith_bg.png", OUTPUT_DIR / "hadith_text.png"))
