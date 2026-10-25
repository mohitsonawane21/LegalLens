from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import audit
from ..common import get_test_or_404
from ..config import PUBLIC_BASE_URL, SECRET_KEY
from ..database import get_db
from ..models import Instrument, Report, TestRun, User
from ..security import current_user, require_roles
from ..serializers import iso, ser_report
from ..services.reporting import compute_content_hash, generate_report, sign_report, signature_valid

router = APIRouter(prefix="/api", tags=["Reports"])
PDF, DOCX = "application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _report_or_404(db: Session, report_no: str) -> Report:
    r = db.query(Report).filter(Report.report_no == report_no).first()
    if not r:
        raise HTTPException(404, f"Report '{report_no}' not found")
    return r


def _file(path: str, media: str, name: str, inline: bool):
    if not path or not Path(path).exists():
        raise HTTPException(404, "Report file missing - call POST /api/reports/{report_no}/regenerate")
    disp = "inline" if inline else "attachment"
    return FileResponse(path, media_type=media, headers={"Content-Disposition": f'{disp}; filename="{name}"'})


@router.get("/reports", tags=["Reports"])
def list_reports(q: Optional[str] = Query(None, description="report no / test id / instrument id / serial / make / model"),
                 result: Optional[str] = Query(None, pattern="^(PASS|FAIL)$"), signed: Optional[bool] = None,
                 page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                 db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Report repository with search + filters."""
    qry = (db.query(Report).join(TestRun, Report.test_pk == TestRun.id)
           .join(Instrument, TestRun.instrument_pk == Instrument.id))
    if q:
        like = f"%{q}%"
        qry = qry.filter(or_(Report.report_no.ilike(like), TestRun.test_id.ilike(like),
                             Instrument.instrument_id.ilike(like), Instrument.serial_number.ilike(like),
                             Instrument.manufacturer.ilike(like), Instrument.model.ilike(like)))
    if result:
        qry = qry.filter(TestRun.overall_result == result)
    if signed is not None:
        qry = qry.filter(Report.signed_at.isnot(None) if signed else Report.signed_at.is_(None))
    total = qry.count()
    rows = qry.order_by(Report.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size, "items": [ser_report(r) for r in rows]}


@router.get("/reports/{report_no}")
def get_report(report_no: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return ser_report(_report_or_404(db, report_no))


@router.get("/reports/{report_no}/pdf")
def download_pdf(report_no: str, inline: bool = False, db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    r = _report_or_404(db, report_no)
    return _file(r.pdf_path, PDF, f"{r.report_no}.pdf", inline)


@router.get("/reports/{report_no}/docx")
def download_docx(report_no: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    r = _report_or_404(db, report_no)
    return _file(r.docx_path, DOCX, f"{r.report_no}.docx", False)


@router.post("/reports/{report_no}/regenerate")
def regenerate(report_no: str, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "officer"))):
    """Re-render files from the stored data (e.g. after adding a logo). Clears any signature - sign again."""
    r = _report_or_404(db, report_no)
    r.signed_at, r.signed_by, r.signed_by_name, r.signed_by_designation, r.signature = None, "", "", "", ""
    generate_report(db, r.test, user)
    audit.log(db, user.username, "report.regenerated", "test", r.test.test_id, {"report_no": r.report_no})
    db.commit()
    return ser_report(r)


@router.post("/reports/{report_no}/sign")
def sign(report_no: str, db: Session = Depends(get_db),
         user: User = Depends(require_roles("admin", "officer", "reviewer"))):
    """Optional digital signature (HMAC-SHA256 over report no + content hash + signer + time). Re-renders the files."""
    r = _report_or_404(db, report_no)
    if r.signed_at:
        raise HTTPException(409, f"Already signed by {r.signed_by_name}")
    sign_report(db, r, r.test, user)
    audit.log(db, user.username, "report.signed", "test", r.test.test_id, {"report_no": r.report_no})
    db.commit()
    return ser_report(r)


@router.post("/reports/{report_no}/share")
def share(report_no: str, hours: int = Query(24, ge=1, le=720), db: Session = Depends(get_db),
          user: User = Depends(current_user)):
    """Time-limited public link to the PDF (no login needed)."""
    r = _report_or_404(db, report_no)
    exp = datetime.now(timezone.utc) + timedelta(hours=hours)
    token = jwt.encode({"rn": r.report_no, "typ": "share", "exp": exp}, SECRET_KEY, algorithm="HS256")
    audit.log(db, user.username, "report.shared", "test", r.test.test_id, {"report_no": r.report_no, "hours": hours})
    db.commit()
    return {"url": f"{PUBLIC_BASE_URL}/api/shared/{token}", "expires_at": exp.isoformat()}


@router.get("/shared/{token}", tags=["Public"])
def shared_pdf(token: str, db: Session = Depends(get_db)):
    try:
        claims = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        assert claims.get("typ") == "share"
    except Exception:
        raise HTTPException(410, "Link invalid or expired")
    r = _report_or_404(db, claims["rn"])
    return _file(r.pdf_path, PDF, f"{r.report_no}.pdf", True)


@router.get("/verify/{report_no}", tags=["Public"])
def verify(report_no: str, hash: Optional[str] = Query(None, description="Hash printed on the report (optional)"),
           db: Session = Depends(get_db)):
    """Public endpoint behind the QR code. Recomputes the content hash from the database and compares."""
    r = db.query(Report).filter(Report.report_no == report_no).first()
    if not r:
        return {"report_no": report_no, "valid": False, "status": "NOT_FOUND"}
    t = r.test
    recomputed = compute_content_hash(t)
    matches = recomputed == r.content_hash
    printed_ok = True if hash is None else hash.lower() == r.content_hash
    return {"report_no": r.report_no, "valid": matches and printed_ok, "status": "AUTHENTIC" if matches and printed_ok else "TAMPERED_OR_MISMATCH",
            "test_id": t.test_id, "instrument_id": t.instrument.instrument_id, "overall_result": t.overall_result,
            "test_date": t.test_date.isoformat(), "generated_at": iso(r.generated_at), "content_hash": r.content_hash,
            "hash_matches_database": matches, "printed_hash_matches": printed_ok if hash is not None else None,
            "signed": r.signed_at is not None, "signed_by": r.signed_by_name or None,
            "signature_valid": signature_valid(r) if r.signed_at else None}
