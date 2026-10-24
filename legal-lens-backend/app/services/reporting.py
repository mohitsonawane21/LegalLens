"""Builds report context from the DB, renders PDF + DOCX, hashes and signs reports."""
import hashlib
import hmac
import json
from pathlib import Path

from ..common import IST
from ..config import PUBLIC_BASE_URL, REPORTS_DIR, SECRET_KEY
from ..models import Report, TestRun, utcnow
from ..standards.engine import TESTS, compile_compliance
from ..standards.rules import get_standard
from .report_builder import build_docx, build_pdf, fmt

ENV_LABELS = [("temperature_start_c", "Temperature at start (C)"), ("temperature_end_c", "Temperature at end (C)"),
              ("humidity_pct", "Relative humidity (%)"), ("pressure_hpa", "Atmospheric pressure (hPa)"),
              ("notes", "Notes")]
INSTR_FIELDS = ["instrument_id", "instrument_type", "manufacturer", "model", "serial_number", "accuracy_class",
                "max_capacity", "min_capacity", "division_d", "verification_interval_e", "unit", "location",
                "manufacturer_details", "technical_parameters"]


def to_ist(dt) -> str:
    from datetime import timezone
    return dt.replace(tzinfo=timezone.utc).astimezone(IST).strftime("%d %b %Y, %I:%M %p")


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


NUMERIC_FIELDS = {"max_capacity", "min_capacity", "division_d", "verification_interval_e"}


def _instrument_fields(i) -> dict:
    # numeric columns are normalised to float so 30 (python int) and 30.0 (value read back from the DB) hash identically
    out = {}
    for k in INSTR_FIELDS:
        v = getattr(i, k)
        out[k] = float(v) if k in NUMERIC_FIELDS and v is not None else v
    return out


def compute_content_hash(t: TestRun) -> str:
    """SHA-256 over the canonical test data. Printed on the report / QR; /api/verify recomputes it from the DB."""
    payload = {
        "test_id": t.test_id, "standard": t.standard, "test_date": t.test_date.isoformat(), "lab": t.lab_name,
        "environment": t.environment, "overall_result": t.overall_result,
        "instrument": _instrument_fields(t.instrument),
        "observations": [{"test_type": o.test_type, "data": o.data, "result": o.result}
                         for o in sorted(t.observations, key=lambda o: o.test_type)],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def make_signature(report_no: str, content_hash: str, username: str, ts) -> str:
    msg = f"{report_no}|{content_hash}|{username}|{ts.isoformat()}".encode()
    return hmac.new(SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


def signature_valid(rep: Report) -> bool:
    if not rep.signed_at:
        return False
    return hmac.compare_digest(rep.signature, make_signature(rep.report_no, rep.content_hash, rep.signed_by, rep.signed_at))


def build_context(t: TestRun, rep: Report) -> dict:
    std = get_standard(t.standard)
    i = t.instrument
    order = list(TESTS)
    obs = sorted(t.observations, key=lambda o: order.index(o.test_type))
    comp = compile_compliance(std, {o.test_type: o.passed for o in obs})
    unit = i.unit
    n = i.max_capacity / i.verification_interval_e
    instrument = [("Instrument ID", i.instrument_id), ("Instrument Type", i.instrument_type),
                  ("Make / Manufacturer", i.manufacturer), ("Model", i.model), ("Serial Number", i.serial_number),
                  ("Accuracy Class", i.accuracy_class), ("Capacity (Max)", f"{fmt(i.max_capacity)} {unit}"),
                  ("Minimum Capacity (Min)", f"{fmt(i.min_capacity)} {unit}" if i.min_capacity is not None else "-"),
                  ("Division (d)", f"{fmt(i.division_d)} {unit}"),
                  ("Verification Interval (e)", f"{fmt(i.verification_interval_e)} {unit}"),
                  ("Number of intervals n = Max / e", f"{n:g}"), ("Location / Laboratory", i.location or t.lab_name)]
    for k, v in (i.manufacturer_details or {}).items():
        instrument.append((k.replace("_", " ").title(), v))
    for k, v in (i.technical_parameters or {}).items():
        instrument.append((k.replace("_", " ").title(), v))
    env = t.environment or {}
    environment = [(label, env.get(k)) for k, label in ENV_LABELS if env.get(k) not in (None, "")]
    environment.insert(0, ("Laboratory", t.lab_name))
    sig = None
    if rep.signed_at:
        sig = {"name": rep.signed_by_name, "designation": rep.signed_by_designation,
               "signed_at": to_ist(rep.signed_at), "signature": rep.signature}
    return {
        "report_no": rep.report_no, "generated_at": to_ist(rep.generated_at), "lab_name": t.lab_name,
        "test_id": t.test_id, "test_date": t.test_date.strftime("%d %b %Y"), "standard": t.standard,
        "overall_result": t.overall_result or comp["overall"], "prepared_by": t.prepared_by or t.created_by,
        "remarks": t.remarks, "instrument": instrument, "environment": environment,
        "observations": [{"label": TESTS[o.test_type]["label"], "methodology": o.result.get("methodology", ""),
                          "passed": o.passed, "columns": o.result["columns"], "rows": o.result["rows"],
                          "warnings": o.warnings} for o in obs],
        "compliance": comp["tests"],
        "attachments": [{"filename": a.filename, "kind": a.kind, "caption": a.caption, "path": a.stored_path,
                         "sha256": a.sha256} for a in t.attachments],
        "videos": [{"filename": v.filename, "sha256": v.sha256, "uploaded_at": to_ist(v.uploaded_at),
                    "extracted_reading": v.extracted_reading, "confirmed_reading": v.confirmed_reading,
                    "method": v.extraction_method} for v in t.videos],
        "content_hash": rep.content_hash, "verify_url": f"{PUBLIC_BASE_URL}/api/verify/{rep.report_no}",
        "signature": sig,
    }


def render_files(rep: Report, t: TestRun) -> None:
    ctx = build_context(t, rep)
    pdf, docx = Path(REPORTS_DIR) / f"{rep.report_no}.pdf", Path(REPORTS_DIR) / f"{rep.report_no}.docx"
    build_pdf(ctx, pdf)
    build_docx(ctx, docx)
    rep.pdf_path, rep.docx_path = str(pdf), str(docx)
    rep.pdf_sha256, rep.docx_sha256 = sha256_file(pdf), sha256_file(docx)


def generate_report(db, t: TestRun, user) -> Report:
    """Create (or regenerate) the report for a finalized test."""
    rep = t.report
    if rep is None:
        rep = Report(report_no="RPT-" + t.test_id.replace("TST-", "", 1), version=0)
        t.report = rep
    rep.content_hash = compute_content_hash(t)
    rep.version += 1
    rep.generated_by, rep.generated_at = user.username, utcnow()
    render_files(rep, t)
    return rep


def sign_report(db, rep: Report, t: TestRun, user) -> None:
    rep.signed_by, rep.signed_by_name, rep.signed_by_designation = user.username, user.full_name, user.designation
    rep.signed_at = utcnow()
    rep.signature = make_signature(rep.report_no, rep.content_hash, user.username, rep.signed_at)
    render_files(rep, t)


def finalize_test(db, t: TestRun, user):
    """Lock the test, compute overall result, generate report. Returns (report | None, compliance)."""
    comp = compile_compliance(get_standard(t.standard), {o.test_type: o.passed for o in t.observations})
    if comp["missing"]:
        return None, comp
    t.status, t.overall_result, t.completed_at = "completed", comp["overall"], utcnow()
    return generate_report(db, t, user), comp
