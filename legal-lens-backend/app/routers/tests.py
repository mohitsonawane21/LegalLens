from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import audit
from ..common import (ensure_editable, get_instrument_or_404, get_test_or_404, next_test_id, spec_from_instrument,
                      std_or_422, today_ist)
from ..config import LAB_NAME
from ..database import get_db
from ..models import AuditLog, Instrument, Observation, TestRun, User, utcnow
from ..security import current_user, require_roles
from ..serializers import ser_audit, ser_obs, ser_report, ser_test
from ..services.reporting import finalize_test
from ..standards.engine import TESTS, compile_compliance, evaluate_observation
from ..standards.examples import DEMO_OBSERVATIONS
from ..standards.rules import DEFAULT_STANDARD, available_standards, mpe, validate_instrument

router = APIRouter(prefix="/api/tests", tags=["Tests"])
standards_router = APIRouter(prefix="/api/standards", tags=["Standards"])
WRITERS = ("admin", "officer")


# ------------------------------------------------------------------ standards
@standards_router.get("")
def list_standards(user: User = Depends(current_user)):
    return available_standards()


@standards_router.get("/mpe")
def mpe_calculator(accuracy_class: str = Query(..., pattern="^(I|II|III|IIII)$"), e: float = Query(..., gt=0),
                   load: float = Query(..., ge=0), in_service: bool = False, standard: str = DEFAULT_STANDARD,
                   user: User = Depends(current_user)):
    """Maximum permissible error for a load (handy for live validation in the UI)."""
    std = std_or_422(standard)
    return {"standard": std["id"], "accuracy_class": accuracy_class, "load": load, "e": e,
            "load_in_e": round(load / e, 3), "mpe": mpe(std, accuracy_class, load, e, in_service),
            "in_service": in_service}


# ------------------------------------------------------------------ schemas
class Environment(BaseModel):
    temperature_start_c: Optional[float] = Field(default=None, ge=-30, le=60)
    temperature_end_c: Optional[float] = Field(default=None, ge=-30, le=60)
    humidity_pct: Optional[float] = Field(default=None, ge=0, le=100)
    pressure_hpa: Optional[float] = Field(default=None, ge=800, le=1100)
    notes: str = ""


class TestCreate(BaseModel):
    instrument_id: str
    test_date: Optional[date] = None
    standard: str = DEFAULT_STANDARD
    lab_name: Optional[str] = None
    environment: Environment = Field(default_factory=Environment)
    remarks: str = ""


class TestPatch(BaseModel):
    lab_name: Optional[str] = None
    environment: Optional[Environment] = None
    remarks: Optional[str] = None


class ObservationIn(BaseModel):
    data: dict


# ------------------------------------------------------------------ tests
@router.get("/catalog")
def catalog(standard: str = DEFAULT_STANDARD, user: User = Depends(current_user)):
    """Test types supported for a standard, with methodology and a sample request body for each."""
    std = std_or_422(standard)
    return {"standard": std["id"], "tests": [
        {"test_type": k, "label": v["label"], "required": k in std["required_tests"],
         "methodology": v["methodology"], "example_body": {"data": DEMO_OBSERVATIONS.get(k)}}
        for k, v in TESTS.items()]}


@router.post("", status_code=201)
def create_test(body: TestCreate, db: Session = Depends(get_db), user: User = Depends(require_roles(*WRITERS))):
    inst = get_instrument_or_404(db, body.instrument_id)
    std = std_or_422(body.standard)
    test_date = body.test_date or today_ist()
    t = TestRun(test_id=next_test_id(db, test_date.year), instrument_pk=inst.id, standard=std["id"],
                lab_name=body.lab_name or LAB_NAME, test_date=test_date, environment=body.environment.model_dump(),
                remarks=body.remarks, status="draft", created_by=user.username,
                prepared_by=f"{user.full_name} ({user.designation})" if user.designation else user.full_name)
    db.add(t)
    audit.log(db, user.username, "test.created", "test", t.test_id, {"instrument_id": inst.instrument_id})
    db.commit()
    return ser_test(t, detail=True)


@router.get("")
def list_tests(q: Optional[str] = Query(None, description="test id / instrument id / serial / make / model"),
               status: Optional[str] = Query(None, pattern="^(draft|in_progress|completed)$"),
               result: Optional[str] = Query(None, pattern="^(PASS|FAIL)$"),
               instrument_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None,
               page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
               db: Session = Depends(get_db), user: User = Depends(current_user)):
    qry = db.query(TestRun).join(Instrument, TestRun.instrument_pk == Instrument.id)
    if q:
        like = f"%{q}%"
        qry = qry.filter(or_(TestRun.test_id.ilike(like), Instrument.instrument_id.ilike(like),
                             Instrument.serial_number.ilike(like), Instrument.manufacturer.ilike(like),
                             Instrument.model.ilike(like)))
    if status:
        qry = qry.filter(TestRun.status == status)
    if result:
        qry = qry.filter(TestRun.overall_result == result)
    if instrument_id:
        qry = qry.filter(Instrument.instrument_id == instrument_id)
    if date_from:
        qry = qry.filter(TestRun.test_date >= date_from)
    if date_to:
        qry = qry.filter(TestRun.test_date <= date_to)
    total = qry.count()
    rows = qry.order_by(TestRun.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size, "items": [ser_test(t) for t in rows]}


@router.get("/{test_id}")
def get_test(test_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return ser_test(get_test_or_404(db, test_id), detail=True)


@router.patch("/{test_id}")
def patch_test(test_id: str, body: TestPatch, db: Session = Depends(get_db),
               user: User = Depends(require_roles(*WRITERS))):
    t = get_test_or_404(db, test_id)
    ensure_editable(t)
    if body.lab_name is not None:
        t.lab_name = body.lab_name
    if body.environment is not None:
        t.environment = body.environment.model_dump()
    if body.remarks is not None:
        t.remarks = body.remarks
    audit.log(db, user.username, "test.updated", "test", t.test_id)
    db.commit()
    return ser_test(t, detail=True)


# ------------------------------------------------------------------ observations
@router.put("/{test_id}/observations/{test_type}")
def save_observation(test_id: str, test_type: str, body: ObservationIn,
                     dry_run: bool = Query(False, description="Validate + calculate without saving"),
                     db: Session = Depends(get_db), user: User = Depends(require_roles(*WRITERS))):
    """Enter / replace the observations of one test. Validates the data, calculates errors, MPE and pass/fail."""
    t = get_test_or_404(db, test_id)
    ensure_editable(t)
    if test_type not in TESTS:
        raise HTTPException(404, f"Unknown test type '{test_type}'. Valid: {list(TESTS)}")
    out = evaluate_observation(test_type, body.data, spec_from_instrument(t.instrument), std_or_422(t.standard))
    if out["errors"]:
        raise HTTPException(422, {"errors": out["errors"], "warnings": out["warnings"]})
    if dry_run:
        return {"dry_run": True, "test_type": test_type, "label": TESTS[test_type]["label"],
                "passed": out["result"]["passed"], "warnings": out["warnings"], "result": out["result"]}
    obs = next((o for o in t.observations if o.test_type == test_type), None)
    if obs is None:
        obs = Observation(test_type=test_type)
        t.observations.append(obs)
    obs.data, obs.result, obs.passed = out["data"], out["result"], out["result"]["passed"]
    obs.warnings, obs.updated_by, obs.updated_at = out["warnings"], user.username, utcnow()
    if t.status == "draft":
        t.status = "in_progress"
    audit.log(db, user.username, "observation.saved", "test", t.test_id,
              {"test_type": test_type, "passed": obs.passed})
    db.commit()
    return ser_obs(obs)


@router.get("/{test_id}/observations")
def list_observations(test_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return [ser_obs(o) for o in get_test_or_404(db, test_id).observations]


@router.delete("/{test_id}/observations/{test_type}", status_code=204)
def delete_observation(test_id: str, test_type: str, db: Session = Depends(get_db),
                       user: User = Depends(require_roles(*WRITERS))):
    t = get_test_or_404(db, test_id)
    ensure_editable(t)
    obs = next((o for o in t.observations if o.test_type == test_type), None)
    if obs is None:
        raise HTTPException(404, "No observation of that type")
    t.observations.remove(obs)
    audit.log(db, user.username, "observation.deleted", "test", t.test_id, {"test_type": test_type})
    db.commit()


# ------------------------------------------------------------------ compliance / finalize / audit
@router.get("/{test_id}/compliance")
def compliance(test_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    t = get_test_or_404(db, test_id)
    std = std_or_422(t.standard)
    comp = compile_compliance(std, {o.test_type: o.passed for o in t.observations})
    errors, warnings = validate_instrument(std, spec_from_instrument(t.instrument))
    return {"test_id": t.test_id, "standard": std["id"], "status": t.status, "overall_result": comp["overall"],
            "tests": comp["tests"], "missing_required": comp["missing"], "failed": comp["failed"],
            "instrument_checks": {"errors": errors, "warnings": warnings}}


@router.post("/{test_id}/finalize")
def finalize(test_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles(*WRITERS))):
    """Lock the test, determine the overall PASS/FAIL, and generate the PDF + DOCX report."""
    t = get_test_or_404(db, test_id)
    ensure_editable(t)
    rep, comp = finalize_test(db, t, user)
    if rep is None:
        raise HTTPException(409, {"message": "Required tests are missing", "missing_required": comp["missing"]})
    audit.log(db, user.username, "test.finalized", "test", t.test_id,
              {"overall_result": t.overall_result, "report_no": rep.report_no})
    db.commit()
    return {"test": ser_test(t), "report": ser_report(rep)}


@router.get("/{test_id}/audit")
def audit_trail(test_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    get_test_or_404(db, test_id)
    rows = db.query(AuditLog).filter(AuditLog.entity == "test", AuditLog.entity_id == test_id).order_by(AuditLog.id).all()
    return [ser_audit(a) for a in rows]
