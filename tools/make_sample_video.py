"""Generate the bundled demo video (sample/sample_scale_reading.mp4) and the UI poster.

The video shows a weighing-instrument display whose reading settles on 25.162626 kg,
exactly like the Upload Test Video mock-up. It is only used to demo the reading-extraction
pipeline; run `python tools/make_sample_video.py` to regenerate it (needs ffmpeg).
"""
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "static" / "img" / "poster_raw.png"
OUT_VIDEO = ROOT / "sample" / "sample_scale_reading.mp4"
OUT_POSTER = ROOT / "static" / "img" / "poster.jpg"

SCALE = 3                      # upscale factor of the 358x254 mock-up photo
FPS = 12
SECONDS = 12
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

# LCD quad in the 358x254 source photo (TL, TR, BR, BL)
LCD_QUAD = np.float32([[123.5, 135.5], [244, 125], [255.5, 161.5], [137.5, 175.5]])


def clean_background() -> np.ndarray:
    img = cv2.imread(str(SRC))
    img = cv2.resize(img, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_CUBIC)
    mask = np.zeros(img.shape[:2], np.uint8)
    s = SCALE
    cv2.rectangle(mask, (268 * s, 2 * s), (356 * s, 40 * s), 255, -1)        # time chip
    cv2.circle(mask, (178 * s, 121 * s), 34 * s, 255, -1)                    # play button
    cv2.rectangle(mask, (0, 216 * s), (358 * s, 254 * s), 255, -1)           # control bar
    return cv2.inpaint(img, mask, 7, cv2.INPAINT_TELEA)


def lcd_text_image(text: str, unit: str = "kg", w: int = 900, h: int = 190) -> np.ndarray:
    im = Image.new("RGB", (w, h), (168, 214, 244))
    d = ImageDraw.Draw(im)
    big = ImageFont.truetype(FONT, 138)
    small = ImageFont.truetype(FONT, 70)
    tw = d.textlength(text, font=big)
    d.text((30, 18), text, font=big, fill=(18, 38, 78))
    d.text((30 + tw + 22, 92), unit, font=small, fill=(18, 38, 78))
    return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)


def paint_lcd(bg: np.ndarray, text: str) -> np.ndarray:
    frame = bg.copy()
    quad = LCD_QUAD * SCALE
    txt = lcd_text_image(text)
    h, w = txt.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    M = cv2.getPerspectiveTransform(src, quad)
    warped = cv2.warpPerspective(txt, M, (frame.shape[1], frame.shape[0]))
    m = cv2.warpPerspective(np.full((h, w), 255, np.uint8), M, (frame.shape[1], frame.shape[0]))
    m = cv2.GaussianBlur(m, (3, 3), 0)[..., None] / 255.0
    out = frame * (1 - m) + warped * m
    return cv2.GaussianBlur(out.astype(np.uint8), (3, 3), 0)


def reading_at(t: float) -> str:
    if t < 3:
        return "0.000"
    if t < 4:
        return "12.7"
    if t < 5:
        return "25.1"
    return "25.162626"


def main() -> None:
    bg = clean_background()
    OUT_VIDEO.parent.mkdir(exist_ok=True)
    h, w = bg.shape[:2]
    h -= h % 2
    w -= w % 2
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "raw.mp4"
        vw = cv2.VideoWriter(str(raw), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (w, h))
        cache: dict[str, np.ndarray] = {}
        for i in range(FPS * SECONDS):
            txt = reading_at(i / FPS)
            if txt not in cache:
                cache[txt] = paint_lcd(bg, txt)[:h, :w]
            vw.write(cache[txt])
        vw.release()
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-c:v", "libx264",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-crf", "23", str(OUT_VIDEO)],
            check=True)
    cv2.imwrite(str(OUT_POSTER), cache["25.162626"][:h, :w], [cv2.IMWRITE_JPEG_QUALITY, 90])
    print("wrote", OUT_VIDEO, OUT_POSTER)


if __name__ == "__main__":
    main()
