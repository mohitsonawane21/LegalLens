"""Reading extraction from a video of a weighing-instrument display.

Pipeline: sample frames -> locate the bright LCD / LED display region -> pre-process
(upscale, CLAHE, Otsu in both polarities) -> Tesseract digit OCR -> take the most frequent
stable reading across frames (late frames weigh more, because the display settles at the end).

Requires `tesseract` on PATH (apt install tesseract-ocr / brew install tesseract).
If it is missing the caller gets a clear error and the UI falls back to manual entry.
"""
from __future__ import annotations

import re
import shutil
from collections import Counter

import cv2
import numpy as np

try:
    import pytesseract
except ImportError:  # pragma: no cover
    pytesseract = None

NUM_RE = re.compile(r"\d{1,6}\.\d{1,6}")


class OcrUnavailable(RuntimeError):
    pass


def ocr_available() -> bool:
    return pytesseract is not None and shutil.which("tesseract") is not None


def video_info(path: str) -> dict:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError("Could not open the video file.")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    cap.release()
    return {"fps": fps, "frames": int(frames), "duration": round(frames / fps, 1) if fps else 0}


def _display_regions(frame: np.ndarray) -> list[np.ndarray]:
    """Return candidate crops of the display (bright, low-texture, large rectangles) plus the full frame."""
    h, w = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    # light blue / cyan / green backlight, or very bright white-ish panels
    mask = cv2.inRange(hsv, (80, 25, 150), (120, 255, 255)) | cv2.inRange(hsv, (35, 40, 140), (80, 255, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:3]:
        x, y, bw, bh = cv2.boundingRect(c)
        if bw * bh < 0.01 * w * h or bw < bh * 1.2:
            continue
        pad = int(0.03 * bw)
        out.append(frame[max(0, y - pad): y + bh + pad, max(0, x - pad): x + bw + pad])
    out.append(frame)
    return out


def _rectify(crop: np.ndarray) -> np.ndarray:
    """Find the bright display panel inside the crop, warp it to an upright rectangle."""
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    g = cv2.GaussianBlur(g, (5, 5), 0)
    _, bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))  # bridge the dark digits
    cnts, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return crop
    c = max(cnts, key=cv2.contourArea)
    if cv2.contourArea(c) < 0.15 * crop.shape[0] * crop.shape[1]:
        return crop
    (cx, cy), (rw, rh), ang = cv2.minAreaRect(c)
    if rw < rh:
        rw, rh, ang = rh, rw, ang + 90
    if ang > 45:
        ang -= 90
    M = cv2.getRotationMatrix2D((cx, cy), ang, 1.0)
    rot = cv2.warpAffine(crop, M, (crop.shape[1], crop.shape[0]), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    x0, y0 = int(cx - rw / 2 + 0.05 * rw), int(cy - rh / 2 + 0.08 * rh)
    x1, y1 = int(cx + rw / 2 - 0.04 * rw), int(cy + rh / 2 - 0.08 * rh)
    out = rot[max(y0, 0):max(y1, 1), max(x0, 0):max(x1, 1)]
    return out if out.size else crop


def _prep_variants(crop: np.ndarray) -> list[np.ndarray]:
    crop = _rectify(crop)
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    scale = 700 / max(g.shape[1], 1)
    if scale > 1 or scale < 0.5:
        g = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    g = cv2.createCLAHE(2.0, (8, 8)).apply(g)
    g = cv2.GaussianBlur(g, (3, 3), 0)
    _, otsu = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if otsu.mean() < 127:               # make text dark on light background
        otsu = cv2.bitwise_not(otsu)
    otsu = _drop_border_blobs(otsu)
    pad = lambda im: cv2.copyMakeBorder(im, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    return [pad(otsu)]


def _drop_border_blobs(bw: np.ndarray) -> np.ndarray:
    """bw = dark glyphs on white. Erase dark components that touch the border or are far taller than the median glyph."""
    inv = cv2.bitwise_not(bw)
    n, lab, st, _ = cv2.connectedComponentsWithStats(inv, connectivity=8)
    H, W = bw.shape
    comps = [(i, *st[i]) for i in range(1, n) if st[i, cv2.CC_STAT_AREA] > 12]
    if not comps:
        return bw
    heights = sorted(c[4] for c in comps if c[4] > 0.12 * H and c[3] < W * 0.3)
    med = heights[len(heights) // 2] if heights else H
    out = bw.copy()
    for i, x, y, w, h, a in comps:
        touches = x <= 1 or y <= 1 or x + w >= W - 1 or y + h >= H - 1
        if touches or h > 1.6 * med:      # small blobs are kept: they are the decimal point
            out[lab == i] = 255
    return out


def _glyphs(bw: np.ndarray):
    """Split a cleaned binary image (dark glyphs on white) into digit boxes and decimal-point boxes."""
    inv = cv2.bitwise_not(bw)
    n, _, st, _ = cv2.connectedComponentsWithStats(inv, connectivity=8)
    boxes = [tuple(st[i][:4]) + (st[i][4],) for i in range(1, n) if st[i][4] > 12]   # x, y, w, h, area
    if not boxes:
        return [], []
    tall = max(b[3] for b in boxes)
    digits = [b for b in boxes if b[3] >= 0.6 * tall]
    if not digits:
        return [], []
    base = max(b[1] + b[3] for b in digits)
    top = min(b[1] for b in digits)
    dots = [b for b in boxes if b[3] < 0.3 * tall and b[2] < 0.3 * tall and abs((b[1] + b[3]) - base) < 0.2 * tall
            and b[1] > top + 0.5 * tall]
    return sorted(digits), dots


def _ocr_digits(row: np.ndarray, expected: int) -> str:
    """OCR one display row several ways (scale x morphology) and vote per digit position.
    Only results with the expected digit count (from glyph segmentation) take part."""
    cfg = "--psm 7 -c tessedit_char_whitelist=0123456789"
    reads: list[str] = []
    for h in (60, 80, 100, 140):
        r0 = cv2.resize(row, None, fx=h / row.shape[0], fy=h / row.shape[0],
                        interpolation=cv2.INTER_AREA if h < row.shape[0] else cv2.INTER_CUBIC)
        for r in (r0, cv2.dilate(r0, np.ones((2, 2), np.uint8)), cv2.GaussianBlur(r0, (3, 3), 0)):
            r = cv2.copyMakeBorder(r, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
            t = re.sub(r"\D", "", pytesseract.image_to_string(r, config=cfg))
            if len(t) == expected:
                reads.append(t)
    if not reads:
        return ""
    return "".join(Counter(t[i] for t in reads).most_common(1)[0][0] for i in range(expected))


def read_frame(frame: np.ndarray) -> list[str]:
    """Return candidate readings found in one frame (digits via OCR, decimal point via glyph geometry)."""
    found: list[str] = []
    for crop in _display_regions(frame):
        for img in _prep_variants(crop):
            digits, dots = _glyphs(img[20:-20, 20:-20])
            if len(digits) < 2:
                continue
            x0, y0 = min(d[0] for d in digits), min(d[1] for d in digits)
            x1 = max(d[0] + d[2] for d in digits)
            y1 = max(d[1] + d[3] for d in digits)
            row = img[max(y0 + 20 - 12, 0): y1 + 20 + 12, max(x0 + 20 - 25, 0): x1 + 20 + 25]
            row = cv2.copyMakeBorder(row, 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=255)
            txt = _ocr_digits(row, len(digits))
            if len(txt) != len(digits):                      # fall back to plain OCR incl. decimal point
                raw = pytesseract.image_to_string(row, config="--psm 7 -c tessedit_char_whitelist=0123456789.")
                found += NUM_RE.findall(raw.replace(",", "."))
                continue
            if dots:
                dx = dots[0][0]
                k = sum(1 for d in digits if d[0] < dx)
                txt = txt[:k] + "." + txt[k:] if 0 < k < len(txt) else txt
            else:
                txt = txt                                    # integer display
            if NUM_RE.fullmatch(txt):
                found.append(txt)
        if found:
            break
    return found


def extract_reading(path: str, max_frames: int = 8) -> dict:
    """Return {'reading': float|None, 'text': str|None, 'confidence': float, 'frames_read': int, 'duration': s}."""
    if not ocr_available():
        raise OcrUnavailable("Tesseract OCR is not installed on this machine. Enter the reading manually below "
                             "(install it with `apt install tesseract-ocr` or `brew install tesseract`).")
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError("Could not open the video file (unsupported codec?). Try MP4/H.264.")
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    idxs = np.linspace(max(total * 0.35, 0), max(total - 2, 0), max_frames).astype(int) if total else range(0, 300, 25)
    votes: Counter[str] = Counter()
    n_read = 0
    for rank, i in enumerate(idxs):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, frame = cap.read()
        if not ok:
            continue
        nums = read_frame(frame)
        if nums:
            n_read += 1
        weight = 1 + rank / len(idxs)          # later frames matter more
        for s in set(nums):
            votes[s] += weight
    cap.release()
    if not votes:
        return {"reading": None, "text": None, "confidence": 0.0, "frames_read": 0,
                "duration": round(total / fps, 1) if total else 0}
    # prefer the longest decimal string among the top candidates (OCR often drops trailing digits)
    top = votes.most_common(3)
    best, score = top[0]
    for cand, sc in top[1:]:
        if cand.startswith(best) and sc >= 0.6 * score:
            best = cand
    conf = min(0.99, votes[best] / sum(votes.values()) + 0.15 * n_read / max(len(idxs), 1))
    return {"reading": float(best), "text": best, "confidence": round(conf, 2), "frames_read": n_read,
            "duration": round(total / fps, 1) if total else 0}
