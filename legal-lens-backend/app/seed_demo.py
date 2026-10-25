"""Creates the demo data shown in the UI mock-ups: instrument NAWI-0018, test TST-2026-00123 (PASS),
signed report RPT-2026-00123.   Run:  python -m app.seed_demo"""
from datetime import date

from . import audit
from .bootstrap import init_db
from .common import spec_from_instrument
from .database import SessionLocal
from .models import Instrument, Observation, TestRun, User, utcnow
from .services.reporting import finalize_test, sign_report
from .standards.engine import evaluate_observation
from .standards.examples import DEMO_ENVIRONMENT, DEMO_INSTRUMENT, DEMO_OBSERVATIONS
from .standards.rules import get_standard


def run() -> None:
    init_db()
    with SessionLocal() as db:
        officer = db.query(User).filter(User.username == "officer").first()
        if officer is None:
            raise SystemExit("User 'officer' not found - run once with SEED_DEMO_USERS=true")
        if db.query(Instrument).filter(Instrument.instrument_id == DEMO_INSTRUMENT["instrument_id"]).first():
            print("Demo data already present.")
            return
        inst = Instrument(**DEMO_INSTRUMENT, created_by=officer.username)
        db.add(inst)
        db.flush()
        std = get_standard()
        t = TestRun(test_id="TST-2026-00123", instrument_pk=inst.id, standard=std["id"],
                    lab_name=DEMO_INSTRUMENT["location"], test_date=date(2026, 9, 17), environment=DEMO_ENVIRONMENT,
                    remarks="Demo type-evaluation run.", status="in_progress", created_by=officer.username,
                    prepared_by=f"{officer.full_name} ({officer.designation})")
        db.add(t)
        db.flush()
        audit.log(db, officer.username, "test.created", "test", t.test_id, {"instrument_id": inst.instrument_id})
        for test_type, data in DEMO_OBSERVATIONS.items():
            out = evaluate_observation(test_type, data, spec_from_instrument(inst), std)
            assert not out["errors"], out["errors"]
            t.observations.append(Observation(test_type=test_type, data=out["data"], result=out["result"],
                                              passed=out["result"]["passed"], warnings=out["warnings"],
                                              updated_by=officer.username, updated_at=utcnow()))
            audit.log(db, officer.username, "observation.saved", "test", t.test_id,
                      {"test_type": test_type, "passed": out["result"]["passed"]})
        rep, comp = finalize_test(db, t, officer)
        audit.log(db, officer.username, "test.finalized", "test", t.test_id,
                  {"overall_result": t.overall_result, "report_no": rep.report_no})
        sign_report(db, rep, t, officer)
        audit.log(db, officer.username, "report.signed", "test", t.test_id, {"report_no": rep.report_no})
        db.commit()
        print(f"Seeded {inst.instrument_id}, {t.test_id} ({t.overall_result}), {rep.report_no} (signed).")


if __name__ == "__main__":
    run()
