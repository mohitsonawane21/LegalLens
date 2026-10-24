from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import Instrument, TestRun
from .standards.rules import Spec, get_standard

IST = timezone(timedelta(hours=5, minutes=30))


def today_ist():
    return datetime.now(IST).date()


def std_or_422(name):
    try:
        return get_standard(name)
    except ValueError as e:
        raise HTTPException(422, str(e))


def get_test_or_404(db: Session, test_id: str) -> TestRun:
    t = db.query(TestRun).filter(TestRun.test_id == test_id).first()
    if not t:
        raise HTTPException(404, f"Test '{test_id}' not found")
    return t


def get_instrument_or_404(db: Session, instrument_id: str) -> Instrument:
    i = db.query(Instrument).filter(Instrument.instrument_id == instrument_id).first()
    if not i:
        raise HTTPException(404, f"Instrument '{instrument_id}' not found")
    return i


def ensure_editable(t: TestRun) -> None:
    if t.status == "completed":
        raise HTTPException(409, "Test is finalized and locked; create a new test to record new observations")


def spec_from_instrument(i: Instrument) -> Spec:
    e = i.verification_interval_e or i.division_d
    return Spec(i.accuracy_class, i.max_capacity, i.min_capacity if i.min_capacity is not None else 20 * e,
                i.division_d, e, i.unit)


def next_test_id(db: Session, year: int) -> str:
    prefix = f"TST-{year}-"
    nums = []
    for (tid,) in db.query(TestRun.test_id).filter(TestRun.test_id.like(prefix + "%")).all():
        tail = tid.rsplit("-", 1)[1]
        if tail.isdigit():
            nums.append(int(tail))
    return f"{prefix}{max(nums or [0]) + 1:05d}"


def next_instrument_id(db: Session) -> str:
    nums = []
    for (iid,) in db.query(Instrument.instrument_id).filter(Instrument.instrument_id.like("NAWI-%")).all():
        if iid[5:].isdigit():
            nums.append(int(iid[5:]))
    return f"NAWI-{max(nums or [0]) + 1:04d}"
