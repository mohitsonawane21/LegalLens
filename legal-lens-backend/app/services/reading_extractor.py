"""Best-effort automatic reading of the weighing-instrument display from a test video.

Pipeline: sample frames -> grayscale/upscale/threshold -> Tesseract digits-only OCR -> majority vote.
Needs the optional packages opencv-python-headless + pytesseract and the Tesseract binary
(see requirements-ocr.txt). Without them the API still works: it returns reading=None and the
operator types the value in the 'Verify detected reading' box. The operator ALWAYS confirms.
"""
import re
from collections import Counter

UNAVAILABLE = {"reading": None, "confidence": 0.0, "method": "unavailable", "duration_s": None,
               "note": "OCR not installed (opencv-python-headless, pytesseract, Tesseract binary). Enter the reading manually."}


def extract_reading(path: str, roi=None) -> dict:
    """roi = (x, y, w, h) as fractions 0-1 of the frame, to crop to the display (improves accuracy)."""
    try:
        import cv2
        import pytesseract
    except ImportError:
        return dict(UNAVAILABLE)

    duration = None
    cap = cv2.VideoCapture(path)
    try:
        if not cap.isOpened():
            return {**UNAVAILABLE, "method": "failed", "note": "Could not open the video file."}
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        duration = round(frames / fps, 2) if fps and frames else None
        if frames <= 0:
            return {**UNAVAILABLE, "method": "failed", "duration_s": duration, "note": "Video has no readable frames."}

        votes = Counter()
        for idx in sorted({int(frames * (0.3 + 0.7 * k / 11)) for k in range(12)}):  # later frames = settled reading
            cap.set(cv2.CAP_PROP_POS_FRAMES, min(idx, frames - 1))
            ok, frame = cap.read()
            if not ok:
                continue
            if roi:
                H, W = frame.shape[:2]
                x, y, w, h = roi
                frame = frame[int(y * H):int((y + h) * H), int(x * W):int((x + w) * W)]
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC), (3, 3), 0)
            _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            for img in (otsu, cv2.bitwise_not(otsu)):
                text = pytesseract.image_to_string(img, config="--psm 11 -c tessedit_char_whitelist=0123456789.")
                for m in re.findall(r"\d{1,4}\.\d{1,6}", text):
                    votes[m] += 1
    except pytesseract.TesseractNotFoundError:
        return {**UNAVAILABLE, "duration_s": duration}
    except Exception as exc:  # never fail the upload because OCR failed
        return {**UNAVAILABLE, "method": "failed", "note": f"OCR error: {exc}"}
    finally:
        cap.release()

    if not votes:
        return {"reading": None, "confidence": 0.0, "method": "ocr-tesseract", "duration_s": duration,
                "note": "No numeric reading detected. Enter the reading manually."}
    value, count = votes.most_common(1)[0]
    return {"reading": float(value), "confidence": round(count / sum(votes.values()), 2), "method": "ocr-tesseract",
            "duration_s": duration, "note": "Automatic reading is best effort - operator must confirm."}
