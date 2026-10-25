# Legal Lens – NAWI Test Report Generator (OIML R 76)
SIH 2026 · Problem Statement 26035 · Team LegalLens

Web prototype: record NAWI test data, auto-calculate OIML R 76 errors / MPE / compliance, extract the display reading
from a video, and generate PDF + editable DOCX reports with QR + SHA-256 verification.

## Run
```bash
pip install -r requirements.txt
# optional: apt install tesseract-ocr poppler-utils   (video OCR + in-app page preview)
python app.py            # http://127.0.0.1:5000
```
Demo logins: `admin/admin123` · `tester/tester123` · `viewer/viewer123`. Seeded tests: `TST-2026-00123` (PASS, the UI mock-up record), `00122` (FAIL), `00124` (in process).

## Screens (match the supplied designs)
| Route | Screen |
|---|---|
| `/tests/TST-2026-00123/upload` | **Upload Test Video** – upload/record, OCR extracted reading, verify & confirm |
| `/reports/TST-2026-00123` | **Test Report** – overview, PDF preview, download PDF/DOCX, share, QR verify, digital signature |
| `/`, `/tests`, `/reports`, `/features`, `/settings`, `/tests/new`, `/tests/<id>/observations` | dashboard, lists/search, features, settings, new test, observation entry |

## Frontend layout
`templates/` (Jinja pages, `_icons.html` shared icons) · `static/css/app.css` (design tokens at top) · `static/img/` (logo, emblem, art cropped from the mock-ups).
The UI talks to the server only through the JSON endpoints in `app.py` (`/api/...`), so the backend can be swapped without touching the templates.

## Tests
`python tests/run_all.py` (or `pytest`).
