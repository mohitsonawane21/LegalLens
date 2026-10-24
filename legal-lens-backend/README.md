# Legal Lens – NAWI Test Report Backend

Backend for **SIH problem statement 26035** – *software to generate test reports for Non-Automatic Weighing Instruments (NAWI) as per OIML R 76* (Dept. of Consumer Affairs).
FastAPI + SQLite. The calculation engine is plain Python, so it is easy to read, test and audit.

## What it does (mapped to the problem statement)

| Requirement | Where |
|---|---|
| Manufacturer details, specifications, model, technical parameters | `POST /api/instruments` (checks Max / d / e / n against the accuracy-class limits) |
| Laboratory + environmental conditions | `environment`, `lab_name` on `POST /api/tests` |
| Observations for the prescribed tests | `PUT /api/tests/{id}/observations/{type}` – weighing, eccentricity, repeatability, discrimination, temperature effect on zero |
| Automatic validation + calculation (E, Ec, MPE, pass/fail) | `app/standards/engine.py` (`?dry_run=true` previews without saving) |
| Compliance determination as per OIML R 76 | `GET /api/tests/{id}/compliance`, overall PASS / FAIL / INCOMPLETE |
| Standard report, auto-populated | `POST /api/tests/{id}/finalize` → PDF **and** editable DOCX with QR + SHA-256 |
| Photos / supporting documents | `POST /api/tests/{id}/attachments` (embedded in the report) |
| Test video → automatic weight reading → operator confirms | `POST /api/tests/{id}/videos`, `POST /api/videos/{id}/confirm` |
| Digital signature (optional) | `POST /api/reports/{no}/sign` |
| Verify with QR / share | public `GET /api/verify/{no}`, `POST /api/reports/{no}/share` |
| Dashboard (completed / in process / history) | `GET /api/dashboard/summary`, `GET /api/tests`, `GET /api/instruments/{id}/history` |
| Search & retrieval of reports | `GET /api/reports?q=&result=&signed=` |
| Role-based access | JWT login; roles `admin`, `officer`, `reviewer`, `viewer` |
| Audit trail | hash-chained log, `GET /api/tests/{id}/audit`, `GET /api/audit/verify` |
| Future revisions of OIML R 76 | rule sets are JSON files in `app/standards/` (add a file, no code change) |

## Run it

```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m app.seed_demo                                  # demo data: NAWI-0018 / TST-2026-00123 / signed report
uvicorn app.main:app --reload
```

Open **http://localhost:8000/docs** (interactive API). Click *Authorize* and log in with a demo user:

| user | password | role |
|---|---|---|
| officer | officer123 | create instruments / tests, enter observations, finalize, sign |
| reviewer | reviewer123 | read + sign |
| admin | admin123 | everything + user management |
| viewer | viewer123 | read-only |

Docker: `docker build -t legal-lens . && docker run -p 8000:8000 -v ll-data:/data legal-lens`
Tests: `pip install -r requirements-dev.txt && pytest`
Config: copy `.env.example` → `.env` values into the environment (`SECRET_KEY`, `PUBLIC_BASE_URL` – used inside the QR code, `CORS_ORIGINS`).
Optional branding: put `emblem.png` and `logo.png` in `assets/` – they are used in the PDF header.

## Typical flow (what the UI calls)

```text
POST /api/auth/login                      (form: username, password)  -> access_token
POST /api/instruments                     body: see app/standards/examples.py DEMO_INSTRUMENT
POST /api/tests                           {"instrument_id": "NAWI-0018", "environment": {...}}   -> TST-2026-00001
POST /api/tests/{id}/videos               multipart file  -> extracted_reading (or null)
POST /api/videos/{vid}/confirm            {"reading": 25.162626}          # "Confirm Reading"
PUT  /api/tests/{id}/observations/weighing   {"data": {...}}              # GET /api/tests/catalog has a sample body per test
POST /api/tests/{id}/finalize             -> locks the test, returns the report (PDF + DOCX created)
POST /api/reports/{no}/sign
GET  /api/reports/{no}/pdf | /docx        (needs Authorization header – fetch as blob in the UI)
GET  /api/verify/{no}                     public – this URL is what the QR code contains
```

UI tab → endpoint: Report Overview/Test Details `GET /tests/{id}` · Observations `GET /tests/{id}/observations` · Calculations = `result.rows` in each observation · Compliance `GET /tests/{id}/compliance` · Attachments `GET /tests/{id}/attachments` · Audit Trail `GET /tests/{id}/audit`.

## Calculation methodology (`app/standards/engine.py`)

* **Error:** `E = I + ½d − ΔL − L` (changeover-point method; `E = I − L` when ΔL is not recorded). **Corrected error** `Ec = E − E0`, where E0 is the error at zero.
* **MPE** (`rules.mpe`): load expressed in verification intervals `m = L / e`; bands per accuracy class from `oiml_r76_2006.json` (class III: ±0.5 e up to 500 e, ±1 e up to 2 000 e, ±1.5 e up to 10 000 e). In-service MPE = 2 × verification MPE.
* **Weighing test:** pass if `|Ec| ≤ MPE(L)` for every load, increasing and decreasing.
* **Eccentricity:** same load at every position; `|Ec| ≤ MPE(L)`.
* **Repeatability:** spread `max(E) − min(E) ≤ MPE(L)` for each series (10 readings recommended for Max ≤ 1 000 kg, else 3).
* **Discrimination:** indication must rise by at least `d` after adding 1.4 d.
* **Temperature effect on zero:** zero change per temperature step (1/2/5 °C for class I/II/III-IIII) must be ≤ e.
* **Instrument checks:** `e ≥ d`; `e = d` for classes III/IIII; number of intervals `n = Max / e` within the class limits.
* **Overall result:** FAIL if any test fails; INCOMPLETE if a required test is missing; otherwise PASS. Floating point comparisons are done after rounding to 6 decimals.

## Architecture

```text
app/
  main.py            FastAPI app, CORS, router registration
  routers/           auth, instruments, tests (+observations, compliance, finalize, audit), evidence (photos, video), reports, dashboard
  standards/         rules.py (MPE, n-limits), engine.py (validators + evaluators, TEST registry), oiml_r76_2006.json, examples.py
  services/          reporting.py (context, SHA-256, signature, finalize), report_builder.py (PDF + DOCX), reading_extractor.py (OCR)
  models.py          SQLAlchemy models: users, instruments, test_runs, observations, attachments, videos, reports, audit_log
  security.py        PBKDF2 password hashing, JWT, role dependency      audit.py   hash-chained audit log
```
Adding a new test (tilting, voltage, creep, warm-up …): write a `validate` + `evaluate` function in `engine.py` and add one entry to `TESTS`; list it in the standard's JSON (`required_tests` / `optional_tests`). The API, report and compliance logic pick it up automatically.

**Integrity model.** `content_hash` = SHA-256 of the canonical instrument + test data + results. It is printed on the report and recomputed from the database by `/api/verify/{no}`, so a modified record shows as `TAMPERED_OR_MISMATCH`. Finalized tests are locked.

## Deployment

Single container (`Dockerfile`) behind any reverse proxy with TLS. Set `SECRET_KEY`, `PUBLIC_BASE_URL` (public https URL, goes into the QR code) and `CORS_ORIGINS`; mount `/data` for the SQLite DB and the report/evidence files. For production switch `DATABASE_URL` to PostgreSQL (SQLAlchemy – no code change) and put `storage/` on durable storage.

## Known limits of this prototype (please read before the evaluation)

* **Verify the rule tables.** MPE bands, n-limits, temperature steps and repeatability reading counts were entered from knowledge of OIML R 76-1:2006, not copied from the official document. Check `app/standards/oiml_r76_2006.json` against your licensed copy (or the Legal Metrology rules) before claiming compliance. Clause references in the methodology text are intentionally general.
* **Digital signature** is an HMAC-SHA256 demo signature (proves the server signed this content hash). A production system would use DSC / Aadhaar eSign with X.509 certificates.
* **Video reading** is best-effort Tesseract OCR (install `requirements-ocr.txt` + the Tesseract binary). Seven-segment displays are hard for OCR; pass `roi` to crop to the display. Without OCR installed the endpoint returns `reading: null` and the operator types the value. The operator always confirms and both values are logged.
* Report text uses plain ASCII symbols (built-in PDF fonts). Auth is a demo setup (seeded users, no password reset).
