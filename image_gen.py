import random

from PIL import Image, ImageDraw, ImageFont

from config import (
    ARABIC_FONT,
    ARABIC_FONT_BOLD,
    IMAGE_HEIGHT,
    IMAGE_WIDTH,
    OUTPUT_DIR,
)

PALETTES = [
    ((96, 165, 250), (21, 46, 92)),
    ((52, 168, 83), (13, 48, 32)),
    ((147, 97, 224), (48, 22, 90)),
    ((45, 190, 190), (10, 55, 62)),
    ((203, 92, 112), (70, 18, 30)),
    ((96, 120, 170), (24, 34, 58)),
    ((38, 166, 154), (9, 44, 45)),
    ((120, 90, 180), (30, 16, 54)),
    ((222, 146, 66), (74, 38, 8)),
    ((70, 130, 180), (12, 30, 48)),
    ((158, 106, 138), (48, 20, 40)),
    ((63, 139, 111), (14, 40, 30)),
    ((110, 76, 132), (26, 14, 34)),
    ((205, 127, 50), (62, 28, 6)),
    ((46, 134, 171), (10, 36, 52)),
    ((142, 68, 90), (40, 12, 22)),
    ((88, 110, 94), (20, 30, 24)),
    ((178, 116, 54), (60, 30, 8)),
]

TEXT_MAIN = (245, 246, 250)
TEXT_MUTED = (200, 210, 225)

DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")

SIDE = 90
SAFE_SIDE = 150
TOP = 170
BOTTOM = 430

COLLECTIONS = {"bukhari": "صحيح البخاري", "muslim": "صحيح مسلم"}


def arabic_digits(n):
    return str(n).translate(DIGITS)


def wrap_pil(draw, text, font, max_width):
    words, lines, current = text.split(), [], ""
    for word in words:
        test = f"{current} {word}".strip()
        if draw.textlength(test, font=font) <= max_width:
            current = test
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def fit_font(draw, text, path, max_width, max_height, start=46, min_size=20):
    size = start
    while size > min_size:
        font = ImageFont.truetype(path, size)
        lines = wrap_pil(draw, text, font, max_width)
        if len(lines) * (size + 16) <= max_height:
            return font, lines
        size -= 2
    font = ImageFont.truetype(path, min_size)
    return font, wrap_pil(draw, text, font, max_width)


def draw_centered(draw, text, y, font, fill, width, side):
    w = draw.textlength(text, font=font)
    draw.text((side + (width - w) / 2, y), text, font=font, fill=fill)


def draw_text_block(draw, hadith, side):
    text_w = IMAGE_WIDTH - 2 * side

    title = ImageFont.truetype(ARABIC_FONT_BOLD, 58)
    draw_centered(draw, "كل يوم حديث", TOP + 40, title, TEXT_MAIN, text_w, side)

    post_no = hadith.get("post_number", 0)
    number = ImageFont.truetype(ARABIC_FONT_BOLD, 130)
    draw_centered(draw, arabic_digits(post_no), TOP + 130, number, TEXT_MAIN, text_w, side)

    collection = COLLECTIONS.get(hadith["collection"], hadith["collection"])
    ref = ImageFont.truetype(ARABIC_FONT, 34)
    draw_centered(
        draw,
        f"من {collection} • حديث رقم {arabic_digits(hadith['hadith_number'])}",
        TOP + 315,
        ref,
        TEXT_MUTED,
        text_w,
        side,
    )

    flex_top = TOP + 410
    flex_height = (IMAGE_HEIGHT - BOTTOM) - flex_top
    gap = 24

    if hadith.get("isnad"):
        f_isnad, lines_isnad = fit_font(
            draw, hadith["isnad"], ARABIC_FONT, text_w, 200, start=30, min_size=18
        )
        isnad_h = len(lines_isnad) * (f_isnad.size + 10)
    else:
        f_isnad, lines_isnad, isnad_h = None, [], 0

    matn_max = max(flex_height - isnad_h - gap, 250)
    f_ar, lines_ar = fit_font(
        draw, hadith["arabic"], ARABIC_FONT_BOLD, text_w, matn_max, start=52, min_size=28
    )
    matn_h = len(lines_ar) * (f_ar.size + 16)

    total_h = isnad_h + gap + matn_h
    y = flex_top + max((flex_height - total_h) / 2, 10)

    if lines_isnad:
        for line in lines_isnad:
            draw_centered(draw, line, y, f_isnad, (215, 222, 235), text_w, side)
            y += f_isnad.size + 10
        y += gap

    for line in lines_ar:
        draw_centered(draw, line, y, f_ar, TEXT_MAIN, text_w, side)
        y += f_ar.size + 16


def _gradient(light, dark):
    img = Image.new("RGB", (IMAGE_WIDTH, IMAGE_HEIGHT))
    for y in range(IMAGE_HEIGHT):
        t = y / (IMAGE_HEIGHT - 1)
        row = tuple(int(light[i] + (dark[i] - light[i]) * t) for i in range(3))
        img.paste(row, (0, y, IMAGE_WIDTH, y + 1))
    return img


def pick_palette(avoid=None):
    """Pick a random gradient palette, avoiding the one used by the previous
    post so consecutive posts don't repeat the same background."""
    pool = [p for p in PALETTES if p != avoid] or PALETTES
    return random.choice(pool)


def generate_image(hadith, output_path, palette=None):
    light, dark = palette or random.choice(PALETTES)
    img = _gradient(light, dark)
    draw = ImageDraw.Draw(img)
    draw_text_block(draw, hadith, SIDE)
    img.save(output_path)
    return output_path


def generate_layers(hadith, bg_path, text_path, palette=None):
    light, dark = palette or random.choice(PALETTES)
    bg = _gradient(light, dark)
    bg.save(bg_path)
    text = Image.new("RGBA", (IMAGE_WIDTH, IMAGE_HEIGHT), (0, 0, 0, 0))
    draw_text_block(ImageDraw.Draw(text), hadith, SAFE_SIDE)
    text.save(text_path)
    return bg_path, text_path


def main(hadith):
    OUTPUT_DIR.mkdir(exist_ok=True)
    return generate_image(hadith, OUTPUT_DIR / "hadith.png")


if __name__ == "__main__":
    from hadith_picker import pick_today

    print(main(pick_today()))
