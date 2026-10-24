from .config import PUBLIC_BASE_URL
from .standards.engine import TESTS


def iso(dt):
    return dt.isoformat() + "Z" if dt else None


def ser_user(u):
    return {"username": u.username, "full_name": u.full_name, "designation": u.designation, "role": u.role,
            "is_active": u.is_active}


def ser_instrument(i):
    return {"instrument_id": i.instrument_id, "instrument_type": i.instrument_type, "manufacturer": i.manufacturer,
            "model": i.model, "serial_number": i.serial_number, "accuracy_class": i.accuracy_class,
            "max_capacity": i.max_capacity, "min_capacity": i.min_capacity, "division_d": i.division_d,
            "verification_interval_e": i.verification_interval_e, "unit": i.unit,
            "intervals_n": round(i.max_capacity / i.verification_interval_e, 3), "location": i.location,
            "manufacturer_details": i.manufacturer_details, "technical_parameters": i.technical_parameters,
            "created_by": i.created_by, "created_at": iso(i.created_at)}


def ser_obs(o):
    return {"test_type": o.test_type, "label": TESTS[o.test_type]["label"], "passed": o.passed, "data": o.data,
            "result": o.result, "warnings": o.warnings, "updated_by": o.updated_by, "updated_at": iso(o.updated_at)}


def ser_attachment(a, test_id=None):
    return {"id": a.id, "test_id": test_id, "filename": a.filename, "kind": a.kind, "caption": a.caption,
            "content_type": a.content_type, "size": a.size, "sha256": a.sha256, "uploaded_by": a.uploaded_by,
            "uploaded_at": iso(a.uploaded_at), "download_url": f"/api/attachments/{a.id}/download"}


def ser_video(v, test=None):
    return {"id": v.id, "test_id": test.test_id if test else None,
            "instrument_id": test.instrument.instrument_id if test else None, "filename": v.filename,
            "size": v.size, "duration_s": v.duration_s, "sha256": v.sha256, "purpose": v.purpose,
            "extracted_reading": v.extracted_reading, "extraction_method": v.extraction_method,
            "extraction_confidence": v.extraction_confidence, "extraction_note": v.extraction_note,
            "reading_detected": v.extracted_reading is not None, "confirmed_reading": v.confirmed_reading,
            "confirmed_by": v.confirmed_by, "confirmed_at": iso(v.confirmed_at), "uploaded_by": v.uploaded_by,
            "uploaded_at": iso(v.uploaded_at), "stream_url": f"/api/videos/{v.id}/file"}


def ser_report(r):
    t = r.test
    return {"report_no": r.report_no, "test_id": t.test_id, "instrument_id": t.instrument.instrument_id,
            "overall_result": t.overall_result, "version": r.version, "generated_at": iso(r.generated_at),
            "generated_by": r.generated_by, "signed": r.signed_at is not None, "signed_by": r.signed_by_name or None,
            "signed_at": iso(r.signed_at), "content_hash": r.content_hash, "pdf_sha256": r.pdf_sha256,
            "docx_sha256": r.docx_sha256, "verify_url": f"{PUBLIC_BASE_URL}/api/verify/{r.report_no}",
            "pdf_url": f"/api/reports/{r.report_no}/pdf", "docx_url": f"/api/reports/{r.report_no}/docx"}


def ser_test(t, detail=False):
    i = t.instrument
    out = {"test_id": t.test_id, "instrument_id": i.instrument_id, "instrument_type": i.instrument_type,
           "manufacturer": i.manufacturer, "model": i.model, "serial_number": i.serial_number,
           "standard": t.standard, "lab_name": t.lab_name, "test_date": t.test_date.isoformat(),
           "status": t.status, "overall_result": t.overall_result, "created_by": t.created_by,
           "prepared_by": t.prepared_by, "created_at": iso(t.created_at), "completed_at": iso(t.completed_at),
           "report_no": t.report.report_no if t.report else None,
           "tests_recorded": [o.test_type for o in t.observations]}
    if detail:
        out.update({"instrument": ser_instrument(i), "environment": t.environment, "remarks": t.remarks,
                    "observations": [ser_obs(o) for o in t.observations],
                    "attachments": [ser_attachment(a, t.test_id) for a in t.attachments],
                    "videos": [ser_video(v, t) for v in t.videos],
                    "report": ser_report(t.report) if t.report else None})
    return out


def ser_audit(a):
    return {"id": a.id, "ts": iso(a.ts), "user": a.username, "action": a.action, "entity": a.entity,
            "entity_id": a.entity_id, "detail": a.detail, "hash": a.hash, "prev_hash": a.prev_hash}
