"""End-to-end API test: login -> instrument -> test -> observations -> finalize -> PDF/DOCX -> sign -> verify."""
import os

import pytest

from app.standards.examples import DEMO_ENVIRONMENT, DEMO_INSTRUMENT, DEMO_OBSERVATIONS


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("legal_lens")
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp / 'test.db'}"
    os.environ["STORAGE_DIR"] = str(tmp / "storage")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


def _login(client, user, pw):
    r = client.post("/api/auth/login", data={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_full_flow(client):
    officer = _login(client, "officer", "officer123")
    viewer = _login(client, "viewer", "viewer123")

    assert client.get("/api/instruments").status_code == 401
    r = client.post("/api/instruments", json=DEMO_INSTRUMENT, headers=viewer)
    assert r.status_code == 403  # role-based access
    r = client.post("/api/instruments", json=DEMO_INSTRUMENT, headers=officer)
    assert r.status_code == 201, r.text

    bad = {**DEMO_INSTRUMENT, "instrument_id": "NAWI-BAD", "division_d": 0.005, "verification_interval_e": 0.01}
    assert client.post("/api/instruments", json=bad, headers=officer).status_code == 422

    r = client.post("/api/tests", json={"instrument_id": "NAWI-0018", "test_date": "2026-09-17",
                                        "environment": DEMO_ENVIRONMENT}, headers=officer)
    assert r.status_code == 201, r.text
    test_id = r.json()["test_id"]

    # incomplete -> cannot finalize
    assert client.post(f"/api/tests/{test_id}/finalize", headers=officer).status_code == 409

    # invalid data is rejected, dry run does not save
    r = client.put(f"/api/tests/{test_id}/observations/weighing", json={"data": {"points": []}}, headers=officer)
    assert r.status_code == 422
    r = client.put(f"/api/tests/{test_id}/observations/weighing?dry_run=true",
                   json={"data": DEMO_OBSERVATIONS["weighing"]}, headers=officer)
    assert r.status_code == 200 and r.json()["passed"] is True
    assert client.get(f"/api/tests/{test_id}/observations", headers=officer).json() == []

    for tt in ("weighing", "eccentricity", "repeatability", "discrimination"):
        r = client.put(f"/api/tests/{test_id}/observations/{tt}", json={"data": DEMO_OBSERVATIONS[tt]}, headers=officer)
        assert r.status_code == 200, r.text
        assert r.json()["passed"] is True

    comp = client.get(f"/api/tests/{test_id}/compliance", headers=officer).json()
    assert comp["overall_result"] == "PASS" and not comp["missing_required"]

    r = client.post(f"/api/tests/{test_id}/finalize", headers=officer)
    assert r.status_code == 200, r.text
    report_no = r.json()["report"]["report_no"]
    assert r.json()["test"]["overall_result"] == "PASS"

    # locked after finalize
    r = client.put(f"/api/tests/{test_id}/observations/weighing", json={"data": DEMO_OBSERVATIONS["weighing"]}, headers=officer)
    assert r.status_code == 409

    pdf = client.get(f"/api/reports/{report_no}/pdf", headers=officer)
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    docx = client.get(f"/api/reports/{report_no}/docx", headers=officer)
    assert docx.status_code == 200 and docx.content[:2] == b"PK"

    r = client.post(f"/api/reports/{report_no}/sign", headers=officer)
    assert r.status_code == 200 and r.json()["signed"] is True

    v = client.get(f"/api/verify/{report_no}").json()   # public, no token
    assert v["valid"] is True and v["hash_matches_database"] is True and v["signature_valid"] is True
    assert client.get(f"/api/verify/{report_no}?hash=deadbeef").json()["valid"] is False

    assert client.get("/api/reports?q=NAWI-0018", headers=viewer).json()["total"] == 1
    assert client.get("/api/dashboard/summary", headers=viewer).json()["tests"]["completed"] == 1
    assert len(client.get(f"/api/tests/{test_id}/audit", headers=viewer).json()) >= 6
    admin = _login(client, "admin", "admin123")
    assert client.get("/api/audit/verify", headers=admin).json()["valid"] is True
