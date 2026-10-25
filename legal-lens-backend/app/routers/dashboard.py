from collections import Counter
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import audit
from ..database import get_db
from ..models import AuditLog, Instrument, Report, TestRun, User
from ..security import current_user, require_roles
from ..serializers import ser_test

router = APIRouter(prefix="/api", tags=["Dashboard"])


@router.get("/dashboard/summary")
def summary(db: Session = Depends(get_db), user: User = Depends(current_user)):
    by_status = dict(db.query(TestRun.status, func.count(TestRun.id)).group_by(TestRun.status).all())
    by_result = dict(db.query(TestRun.overall_result, func.count(TestRun.id))
                     .filter(TestRun.status == "completed").group_by(TestRun.overall_result).all())
    months = Counter(d.strftime("%Y-%m") for (d,) in db.query(TestRun.test_date).all())
    recent = db.query(TestRun).order_by(TestRun.id.desc()).limit(5).all()
    return {
        "tests": {"total": sum(by_status.values()), "draft": by_status.get("draft", 0),
                  "in_progress": by_status.get("in_progress", 0), "completed": by_status.get("completed", 0)},
        "results": {"PASS": by_result.get("PASS", 0), "FAIL": by_result.get("FAIL", 0)},
        "instruments": db.query(func.count(Instrument.id)).scalar(),
        "reports": {"total": db.query(func.count(Report.id)).scalar(),
                    "signed": db.query(func.count(Report.id)).filter(Report.signed_at.isnot(None)).scalar()},
        "tests_per_month": [{"month": m, "count": months[m]} for m in sorted(months)[-6:]],
        "recent_tests": [ser_test(t) for t in recent],
    }


@router.get("/audit/verify")
def verify_audit_chain(db: Session = Depends(get_db), admin: User = Depends(require_roles("admin"))):
    """Recompute the hash chain of the whole audit log - detects edited or deleted rows."""
    prev, n = "GENESIS", 0
    for a in db.query(AuditLog).order_by(AuditLog.id).all():
        if a.prev_hash != prev or a.hash != audit.compute_hash(prev, a.payload):
            return {"valid": False, "broken_at_id": a.id, "entries_checked": n}
        prev, n = a.hash, n + 1
    return {"valid": True, "entries_checked": n}
