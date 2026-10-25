from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import audit
from ..common import get_instrument_or_404, next_instrument_id, std_or_422
from ..database import get_db
from ..models import Instrument, TestRun, User
from ..security import current_user, require_roles
from ..serializers import iso, ser_instrument
from ..standards.rules import Spec, validate_instrument

router = APIRouter(prefix="/api/instruments", tags=["Instruments"])


class InstrumentIn(BaseModel):
    instrument_id: Optional[str] = Field(default=None, description="Auto-generated (NAWI-0001...) if omitted")
    instrument_type: str = "Weighing Scale"
    manufacturer: str
    model: str
    serial_number: str
    accuracy_class: str = Field(pattern="^(I|II|III|IIII)$")
    max_capacity: float = Field(gt=0)
    min_capacity: Optional[float] = Field(default=None, gt=0, description="Defaults to the class minimum (e.g. 20 e)")
    division_d: float = Field(gt=0)
    verification_interval_e: Optional[float] = Field(default=None, gt=0, description="Defaults to d")
    unit: str = Field(default="kg", pattern="^(g|kg|t)$")
    location: str = ""
    manufacturer_details: dict = Field(default_factory=dict)
    technical_parameters: dict = Field(default_factory=dict)


@router.post("", status_code=201)
def create_instrument(body: InstrumentIn, db: Session = Depends(get_db),
                      user: User = Depends(require_roles("admin", "officer"))):
    std = std_or_422(None)
    e = body.verification_interval_e or body.division_d
    min_cap = body.min_capacity if body.min_capacity is not None else std["min_capacity_e"][body.accuracy_class] * e
    errors, warnings = validate_instrument(std, Spec(body.accuracy_class, body.max_capacity, min_cap,
                                                     body.division_d, e, body.unit))
    if errors:
        raise HTTPException(422, {"errors": errors, "warnings": warnings})
    iid = body.instrument_id or next_instrument_id(db)
    if db.query(Instrument).filter(Instrument.instrument_id == iid).first():
        raise HTTPException(409, f"Instrument '{iid}' already exists")
    inst = Instrument(instrument_id=iid, instrument_type=body.instrument_type, manufacturer=body.manufacturer,
                      model=body.model, serial_number=body.serial_number, accuracy_class=body.accuracy_class,
                      max_capacity=body.max_capacity, min_capacity=min_cap, division_d=body.division_d,
                      verification_interval_e=e, unit=body.unit, location=body.location,
                      manufacturer_details=body.manufacturer_details, technical_parameters=body.technical_parameters,
                      created_by=user.username)
    db.add(inst)
    audit.log(db, user.username, "instrument.created", "instrument", iid, {"model": body.model})
    db.commit()
    return {**ser_instrument(inst), "warnings": warnings}


@router.get("")
def list_instruments(q: Optional[str] = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    qry = db.query(Instrument)
    if q:
        like = f"%{q}%"
        qry = qry.filter(or_(Instrument.instrument_id.ilike(like), Instrument.manufacturer.ilike(like),
                             Instrument.model.ilike(like), Instrument.serial_number.ilike(like)))
    return [ser_instrument(i) for i in qry.order_by(Instrument.id.desc()).all()]


@router.get("/{instrument_id}")
def get_instrument(instrument_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return ser_instrument(get_instrument_or_404(db, instrument_id))


@router.get("/{instrument_id}/history")
def instrument_history(instrument_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Instrument-wise test history (all tests + report numbers)."""
    inst = get_instrument_or_404(db, instrument_id)
    tests = db.query(TestRun).filter(TestRun.instrument_pk == inst.id).order_by(TestRun.id.desc()).all()
    return {"instrument": ser_instrument(inst),
            "tests": [{"test_id": t.test_id, "test_date": t.test_date.isoformat(), "status": t.status,
                       "overall_result": t.overall_result, "standard": t.standard,
                       "report_no": t.report.report_no if t.report else None,
                       "completed_at": iso(t.completed_at)} for t in tests]}
