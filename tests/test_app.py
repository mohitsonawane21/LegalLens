"""End-to-end tests through the Flask app (temporary database). Run: python tests/run_all.py  or  pytest"""
import io
import os
import sys
import tempfile
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="legal_lens_test_")
os.environ["LEGAL_LENS_DATA"] = TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
import db  # noqa: E402

app = appmod.app
app.config["TESTING"] = True


def client(user="admin"):
    c = app.test_client()
    c.post("/login", data={"username": user, "password": f"{user}123"})
    return c


def test_login_required_and_rbac():
    anon = app.test_client()
    assert anon.get("/").status_code == 302
    assert anon.get("/api/tests/TST-2026-00123/evaluate").status_code in (401, 405)
    v = client("viewer")
    assert v.get("/reports/TST-2026-00123").status_code == 200           # can read
    assert v.get("/tests/new").status_code == 403                        # cannot create
    assert v.get("/settings").status_code == 403
    assert v.post("/api/tests/TST-2026-00123/sign").status_code == 403
    assert client("tester").get("/settings").status_code == 403


def test_seed_results():
    t = db.get_test("TST-2026-00123")
    assert t["result"]["overall"] == "PASS" and t["status"] == "completed"
    assert db.get_test("TST-2026-00122")["result"]["overall"] == "FAIL"
    assert db.get_test("TST-2026-00124")["result"]["overall"] == "INCOMPLETE"


def test_report_pdf_docx_and_verify():
    c = client()
    r = c.get("/reports/TST-2026-00123")
    assert r.status_code == 200 and b"Report Generated Successfully" in r.data and b"NAWI-0018" in r.data
    pdf = c.get("/reports/TST-2026-00123/file/pdf")
    assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF"
    docx = c.get("/reports/TST-2026-00123/file/docx")
    assert docx.status_code == 200 and docx.data[:2] == b"PK"
    t = db.get_test("TST-2026-00123")
    assert len(t["report_hash"]) == 64
    v = app.test_client().get(f"/verify/TST-2026-00123?h={t['report_hash'][:16]}")      # public page
    assert v.status_code == 200 and b"Authentic" in v.data
    assert app.test_client().get("/verify/TST-2026-00123.json").get_json()["status"] == "authentic"


def test_tamper_detection():
    c = client()
    c.get("/reports/TST-2026-00122")
    t = db.get_test("TST-2026-00122")
    assert app.test_client().get(f"/verify/{t['id']}.json").get_json()["status"] == "authentic"
    db.save_test_fields(t["id"], manufacturer="Changed Co")                  # record edited behind the report's back
    assert app.test_client().get(f"/verify/{t['id']}.json").get_json()["status"] == "tampered"
    assert b"Integrity warning" in c.get(f"/reports/{t['id']}").data
    db.save_test_fields(t["id"], manufacturer="Avery India")


def test_full_workflow_new_test_to_signed_report():
    c = client("tester")
    r = c.post("/tests/new", data=dict(instrument_id="NAWI-0099", instrument_type="Weighing Scale", manufacturer="Acme", model="A1",
                                       serial_number="S-1", unit="kg", accuracy_class="III", capacity="15", division="0.005",
                                       verification_interval="", location="Lab", test_date="2026-10-22", test_standard="OIML R-76"))
    assert r.status_code == 302
    tid = r.headers["Location"].split("/")[2]
    assert tid.startswith("TST-")
    # invalid instrument data is rejected
    bad = c.post("/tests/new", data=dict(instrument_id="X", manufacturer="A", model="B", serial_number="C", accuracy_class="III", capacity="1",
                                         division="0.005", unit="kg"))
    assert bad.status_code == 400
    # video: bad extension rejected, sample accepted
    assert c.post(f"/api/tests/{tid}/video", data={"video": (io.BytesIO(b"x"), "a.txt")}).status_code == 400
    j = c.post(f"/api/tests/{tid}/video", data={"sample": "1"}).get_json()
    assert j["ok"] and abs(j["duration"] - 12) < 0.5
    assert c.get(f"/media/video/{tid}").status_code == 200
    # confirm reading (manual)
    assert c.post(f"/api/tests/{tid}/confirm", json={"reading": "abc"}).status_code == 400
    assert c.post(f"/api/tests/{tid}/confirm", json={"reading": "25.162626"}).get_json()["ok"]
    # generating before observations -> incomplete warning
    assert c.post(f"/api/tests/{tid}/generate", json={}).status_code == 409
    # observations
    obs = {"weighing": {"rows": [{"load": l, "ind_up": l, "dl_up": 0.002, "ind_down": l, "dl_down": 0.002} for l in (0.1, 2.5, 5, 10, 15)]},
           "eccentricity": {"load": 5, "rows": [{"position": "C", "indication": 5.0, "delta_l": 0.002}]},
           "repeatability": {"series": [{"load": 15, "readings": [{"indication": 15.0, "delta_l": 0.002}] * 3}]},
           "discrimination": {"rows": [{"load": 5, "ind_before": 5.0, "ind_after": 5.005}]}}
    ev = c.post(f"/api/tests/{tid}/evaluate", json={"env": {"temperature_c": 22, "humidity_pct": 50, "pressure_hpa": 1000}, "obs": obs}).get_json()
    assert ev["overall"] == "PASS"
    # attachment (photo)
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (80, 60), "red").save(buf, "PNG")
    buf.seek(0)
    assert c.post(f"/api/tests/{tid}/attachments", data={"files": (buf, "photo.png")}).get_json()["ok"]
    g = c.post(f"/api/tests/{tid}/generate", json={"env": {"temperature_c": 22, "humidity_pct": 50, "pressure_hpa": 1000, "tested_by": "T"}, "obs": obs}).get_json()
    assert g["ok"]
    t = db.get_test(tid)
    assert t["status"] == "completed" and t["result"]["overall"] == "PASS"
    assert c.get(f"/reports/{tid}/file/pdf").data[:4] == b"%PDF"
    # locked after completion for non-admin
    assert c.post(f"/api/tests/{tid}/observations", json={"env": {}, "obs": {}}).status_code == 409
    # sign
    assert c.post(f"/api/tests/{tid}/sign").get_json()["ok"]
    t = db.get_test(tid)
    assert t["signed_by"] and t["signature_mac"]
    assert client("viewer").get(f"/verify/{tid}").status_code == 200
    assert app.test_client().get(f"/verify/{tid}.json").get_json()["status"] == "authentic"
    # audit trail captured the workflow
    actions = [r["action"] for r in db.q("SELECT action FROM audit WHERE test_id=?", (tid,))]
    for a in ("Test created", "Video uploaded", "Reading confirmed", "Attachment added", "Report generated", "Report digitally signed"):
        assert a in actions, a
    # admin can reopen -> signature removed
    assert client("admin").post(f"/api/tests/{tid}/reopen").get_json()["ok"]
    assert db.get_test(tid)["signed_by"] is None


def test_search_and_history():
    c = client()
    assert b"TST-2026-00122" in c.get("/reports?q=avery").data
    assert b"TST-2026-00123" not in c.get("/reports?q=avery").data
    assert b"TST-2026-00123" in c.get("/reports?instrument=NAWI-0018").data
    assert b"TST-2026-00122" in c.get("/reports?result=FAIL").data


def test_ocr_reads_sample_video():
    import ocr
    if not ocr.ocr_available():
        return
    res = ocr.extract_reading(str(Path(__file__).resolve().parent.parent / "sample" / "sample_scale_reading.mp4"))
    assert res["reading"] == 25.162626, res
