"""Legal Lens – NAWI test-report generator (OIML R 76).  Run:  python app.py  ->  http://127.0.0.1:5000"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template, request, send_file, send_from_directory, session,
                   url_for)
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

import db
import ocr
import r76
import report as rpt

BASE = Path(__file__).parent
UPLOADS = db.DATA_DIR / "uploads"
GENERATED = db.DATA_DIR / "generated"
SAMPLE_DIR = BASE / "sample"
SAMPLE_VIDEO = "sample_scale_reading.mp4"
VIDEO_EXT = {".mp4", ".mov", ".avi", ".webm"}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp"}
DOC_EXT = {".pdf", ".docx", ".xlsx", ".txt", ".csv"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024          # "Max size 500 MB"
app.config["TEMPLATES_AUTO_RELOAD"] = True

def _secret() -> str:
    env = os.environ.get("LEGAL_LENS_SECRET")
    if env:
        return env
    return secrets.token_hex(32)


app.secret_key = _secret()
db.init_db()
for d in (UPLOADS, GENERATED):
    d.mkdir(parents=True, exist_ok=True)

ROLES = {"admin": 3, "tester": 2, "viewer": 1}


# ----------------------------------------------------------------------------- auth / RBAC
@app.before_request
def load_user():
    g.user = None
    if "uid" in session:
        row = db.q("SELECT * FROM users WHERE id=?", (session["uid"],), one=True)
        g.user = dict(row) if row else None
    open_ep = {"login", "static", "verify", "verify_json", "healthz"}
    if g.user is None and request.endpoint not in open_ep:
        if request.path.startswith("/api/"):
            return jsonify(error="Login required"), 401
        return redirect(url_for("login", next=request.path))


def role_required(min_role: str):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if ROLES[g.user["role"]] < ROLES[min_role]:
                if request.path.startswith("/api/"):
                    return jsonify(error=f"Your role ({g.user['role']}) is not permitted to do this."), 403
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


def can_edit(t: dict) -> bool:
    return ROLES[g.user["role"]] >= ROLES["tester"] and t["status"] != "completed" or g.user["role"] == "admin"


@app.context_processor
def inject():
    return {"user": g.get("user"), "ROLES": ROLES, "lab": lab_settings(), "nav": request.endpoint or "",
            "fmt": rpt.fmt, "now_year": datetime.now().year}


@app.template_filter("dt")
def _dt(v, mode="long"):
    if not v:
        return "–"
    try:
        d = datetime.strptime(v[:19], "%Y-%m-%d %H:%M:%S") if len(v) > 10 else datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        return v
    if mode == "date":
        return d.strftime("%d %b %Y")
    if mode == "pdate":
        return d.strftime("%d %b %Y")
    return d.strftime("%d %b %Y, %I:%M %p")


@app.template_filter("num")
def _num(v, nd=3):
    return rpt.fmt(v, nd)


def lab_settings() -> dict:
    try:
        return {k: db.get_setting(k) for k in ("lab_name", "lab_section", "signatory_name", "signatory_designation", "rules_file")}
    except Exception:
        return {}


@app.route("/healthz")
def healthz():
    return "ok"


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        row = db.q("SELECT * FROM users WHERE username=?", (request.form.get("username", "").strip(),), one=True)
        if row and check_password_hash(row["password_hash"], request.form.get("password", "")):
            session.clear()
            session["uid"] = row["id"]
            nxt = request.args.get("next") or url_for("home")
            return redirect(nxt if nxt.startswith("/") else url_for("home"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ----------------------------------------------------------------------------- helpers
def load_test(test_id: str) -> dict:
    t = db.get_test(test_id)
    if not t:
        abort(404)
    return t


def attachments_of(test_id: str) -> list[dict]:
    return [dict(r) for r in db.q("SELECT * FROM attachments WHERE test_id=? ORDER BY id", (test_id,))]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def video_path(t: dict) -> Path | None:
    if not t.get("video_file"):
        return None
    p = SAMPLE_DIR / t["video_file"] if t["video_file"] == SAMPLE_VIDEO else UPLOADS / t["id"] / t["video_file"]
    return p if p.exists() else None


def recompute(t: dict) -> dict:
    res = r76.evaluate_all(t, t["env"], t["obs"], r76.load_rules(lab_settings().get("rules_file") or r76.DEFAULT_RULES))
    db.save_test_fields(t["id"], result=res)
    t["result"] = res
    return res


def public_base() -> str:
    return (os.environ.get("PUBLIC_BASE_URL") or request.host_url).rstrip("/")


def lab_for_report() -> dict:
    return lab_settings()


def build_payload(t: dict) -> dict:
    return rpt.report_payload(t, lab_for_report(), attachments_of(t["id"]))


def generate_report(t: dict, keep_time: bool = False) -> dict:
    """(Re)compute results, hash, and write PDF + DOCX into generated/<test-id>/."""
    recompute(t)
    ts = t["report_generated_at"] if (keep_time and t.get("report_generated_at")) else db.now()
    rhash = rpt.payload_hash(build_payload(t))
    verify_url = f"{public_base()}/verify/{t['id']}?h={rhash[:16]}"
    sig = None
    if t.get("signed_by"):
        sig = {"name": t["signed_by"], "designation": t["signed_designation"], "at": t["signed_at"], "mac": t["signature_mac"]}
        if rpt.sign(app.secret_key, rhash, t["signed_by"], t["signed_at"]) != t["signature_mac"]:   # data changed after signing
            sig = None
            db.save_test_fields(t["id"], signed_by=None, signed_designation=None, signed_at=None, signature_mac=None)
            t["signed_by"] = None
    atts = attachments_of(t["id"])
    adir = UPLOADS / t["id"]
    out = GENERATED / t["id"]
    out.mkdir(parents=True, exist_ok=True)
    pdf = rpt.build_pdf(t, lab_for_report(), atts, rhash, verify_url, sig, db.now() if not keep_time else ts, adir)
    docx = rpt.build_docx(t, lab_for_report(), atts, rhash, verify_url, sig, ts, adir)
    (out / f"{t['id']}.pdf").write_bytes(pdf)
    (out / f"{t['id']}.docx").write_bytes(docx)
    for old in out.glob("page-*.png"):
        old.unlink()
    db.save_test_fields(t["id"], report_generated_at=ts, report_hash=rhash, verify_url=verify_url, report_version=(t.get("report_version") or 0) + 1,
                        status="completed")
    t.update(report_generated_at=ts, report_hash=rhash, verify_url=verify_url, status="completed")
    return t


def ensure_report(t: dict) -> dict:
    pdf = GENERATED / t["id"] / f"{t['id']}.pdf"
    if t["status"] == "completed" and (not pdf.exists() or not t.get("report_hash")):
        generate_report(t, keep_time=True)
        t = load_test(t["id"])
    return t


def page_images(t: dict) -> list[str]:
    """Rasterise PDF pages for the preview pane (needs poppler's pdftoppm; falls back to <iframe> otherwise)."""
    out = GENERATED / t["id"]
    pdf = out / f"{t['id']}.pdf"
    if not pdf.exists() or not shutil.which("pdftoppm"):
        return []
    pages = sorted(out.glob("page-*.png"))
    if not pages or pages[0].stat().st_mtime < pdf.stat().st_mtime:
        for old in pages:
            old.unlink()
        subprocess.run(["pdftoppm", "-png", "-r", "110", str(pdf), str(out / "page")], check=False, timeout=60)
        pages = sorted(out.glob("page-*.png"))
    return [p.name for p in pages]


def chk_hash_valid(t: dict) -> bool:
    return bool(t.get("report_hash")) and rpt.payload_hash(build_payload(t)) == t["report_hash"]


# ----------------------------------------------------------------------------- pages
@app.route("/")
def home():
    rows = [db.test_dict(r) for r in db.q("SELECT * FROM tests ORDER BY created_at DESC")]
    stats = {"total": len(rows), "completed": sum(r["status"] == "completed" for r in rows),
             "in_process": sum(r["status"] != "completed" for r in rows),
             "pass": sum(r["result"].get("overall") == "PASS" and r["status"] == "completed" for r in rows),
             "fail": sum(r["result"].get("overall") == "FAIL" and r["status"] == "completed" for r in rows)}
    return render_template("home.html", tests=rows[:6], stats=stats)


@app.route("/features")
def features():
    return render_template("features.html", ocr_ok=ocr.ocr_available(), pdftoppm=bool(shutil.which("pdftoppm")))


def _filtered_tests(only_completed: bool):
    q = request.args.get("q", "").strip().lower()
    status = request.args.get("status", "")
    result = request.args.get("result", "")
    inst = request.args.get("instrument", "")
    rows = [db.test_dict(r) for r in db.q("SELECT * FROM tests ORDER BY test_date DESC, id DESC")]
    out = []
    for r in rows:
        if only_completed and r["status"] != "completed":
            continue
        hay = " ".join(str(r.get(k, "")) for k in ("id", "instrument_id", "manufacturer", "model", "serial_number", "instrument_type",
                                                     "location", "created_by")).lower()
        if q and q not in hay:
            continue
        if status and ((status == "completed") != (r["status"] == "completed")):
            continue
        if result and r["result"].get("overall") != result:
            continue
        if inst and r["instrument_id"] != inst:
            continue
        out.append(r)
    return out


@app.route("/tests")
def tests():
    return render_template("list.html", mode="tests", rows=_filtered_tests(False), args=request.args)


@app.route("/reports")
def reports():
    return render_template("list.html", mode="reports", rows=_filtered_tests(True), args=request.args)


@app.route("/tests/new", methods=["GET", "POST"])
@role_required("tester")
def new_test():
    cfg = lab_settings()
    if request.method == "POST":
        f = request.form
        inst = {k: f.get(k, "").strip() for k in ("instrument_id", "instrument_type", "manufacturer", "model", "serial_number", "unit",
                                                   "accuracy_class", "location", "test_date", "test_standard")}
        for k in ("capacity", "division", "verification_interval"):
            inst[k] = r76._num(f.get(k))
        inst["verification_interval"] = inst["verification_interval"] or inst["division"]
        issues = [i for i in r76.validate_instrument(inst) if i["level"] == "error"]
        if issues:
            for i in issues:
                flash(i["message"], "error")
            return render_template("new_test.html", f=f, cfg=cfg, next_id=f.get("instrument_id"), today=f.get("test_date")), 400
        tid = db.next_test_id()
        cols = ["id", "created_by", "created_at", "status"] + list(inst)
        vals = [tid, g.user["username"], db.now(), "draft"] + list(inst.values())
        db.execute(f"INSERT INTO tests({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals)
        db.audit(tid, g.user["username"], "Test created", f"Instrument {inst['instrument_id']}")
        return redirect(url_for("upload_page", test_id=tid))
    n = db.q("SELECT COUNT(DISTINCT instrument_id) c FROM tests", one=True)["c"]
    return render_template("new_test.html", f={}, cfg=cfg, next_id=f"NAWI-{n + 18:04d}", today=datetime.now().strftime("%Y-%m-%d"))


@app.route("/tests/<test_id>/upload")
def upload_page(test_id):
    t = load_test(test_id)
    return render_template("upload.html", t=t, ocr_ok=ocr.ocr_available(), editable=can_edit(t), sample=SAMPLE_VIDEO)


@app.route("/tests/<test_id>/observations")
def observations_page(test_id):
    t = load_test(test_id)
    rules = r76.load_rules(lab_settings().get("rules_file") or r76.DEFAULT_RULES)
    return render_template("observations.html", t=t, editable=can_edit(t), attachments=attachments_of(test_id), rules=rules,
                           snapped=r76.round_to_division(t["reading_confirmed"], t["division"]) if t.get("reading_confirmed") else None)


@app.route("/reports/<test_id>")
def report_page(test_id):
    t = ensure_report(load_test(test_id))
    if t["status"] != "completed":
        flash("Report not generated yet. Complete the observations first.", "error")
        return redirect(url_for("observations_page", test_id=test_id))
    pages = page_images(t)
    audit_rows = [dict(r) for r in db.q("SELECT * FROM audit WHERE test_id=? ORDER BY id DESC", (test_id,))]
    issues_all = list(t["result"].get("instrument_issues", [])) + list(t["result"].get("environment_issues", []))
    for x in t["result"].get("tests", {}).values():
        issues_all += x.get("issues", [])
    return render_template("report.html", t=t, pages=pages, issues_all=issues_all, atts=attachments_of(test_id), audit_rows=audit_rows,
                           qr=rpt.qr_svg(t["verify_url"], 118), hash_ok=chk_hash_valid(t), tab=request.args.get("tab", "overview"),
                           editable=can_edit(t))


@app.route("/reports/<test_id>/file/<kind>")
def report_file(test_id, kind):
    t = ensure_report(load_test(test_id))
    if kind not in ("pdf", "docx", "preview"):
        abort(404)
    if t["status"] != "completed":
        abort(404)
    f = GENERATED / test_id / f"{test_id}.{'pdf' if kind == 'preview' else kind}"
    if not f.exists():
        abort(404)
    db.audit(test_id, g.user["username"], f"Downloaded {kind.upper()}" if kind != "preview" else "Viewed report")
    return send_file(f, as_attachment=(kind != "preview"), download_name=f"Test_Report_{test_id}.{kind if kind != 'preview' else 'pdf'}",
                     mimetype="application/pdf" if kind != "docx" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@app.route("/reports/<test_id>/page/<name>")
def report_page_img(test_id, name):
    if not re.fullmatch(r"page-\d+\.png", name):
        abort(404)
    return send_from_directory(GENERATED / test_id, name)


@app.route("/verify/<test_id>")
def verify(test_id):
    t = db.get_test(test_id)
    status, detail = "unknown", None
    if t and t.get("report_hash"):
        recomputed = rpt.payload_hash(build_payload(t))
        ok = recomputed == t["report_hash"] and request.args.get("h", t["report_hash"][:16]) == t["report_hash"][:16]
        status = "authentic" if ok else "tampered"
        detail = {"stored": t["report_hash"], "recomputed": recomputed}
        if t.get("signed_by"):
            detail["signature_ok"] = rpt.sign(app.secret_key, t["report_hash"], t["signed_by"], t["signed_at"]) == t["signature_mac"]
    return render_template("verify.html", t=t, status=status, detail=detail), (200 if t else 404)


@app.route("/verify/<test_id>.json")
def verify_json(test_id):
    t = db.get_test(test_id)
    if not t or not t.get("report_hash"):
        return jsonify(status="unknown"), 404
    return jsonify(status="authentic" if rpt.payload_hash(build_payload(t)) == t["report_hash"] else "tampered", test_id=test_id,
                   hash=t["report_hash"], result=t["result"].get("overall"))


@app.route("/settings", methods=["GET", "POST"])
@role_required("admin")
def settings():
    if request.method == "POST":
        for k in ("lab_name", "lab_section", "signatory_name", "signatory_designation", "rules_file"):
            if k in request.form:
                if k == "rules_file" and not (r76.RULES_DIR / request.form[k]).exists():
                    flash("Rules file not found in /rules.", "error")
                    continue
                db.set_setting(k, request.form[k].strip())
        flash("Settings saved.", "ok")
        return redirect(url_for("settings"))
    users = [dict(r) for r in db.q("SELECT username, role, full_name, designation FROM users ORDER BY id")]
    rules_files = sorted(p.name for p in r76.RULES_DIR.glob("*.json"))
    return render_template("settings.html", users=users, rules_files=rules_files, rules=r76.load_rules(lab_settings()["rules_file"]))


# ----------------------------------------------------------------------------- API : video + OCR
@app.post("/api/tests/<test_id>/video")
@role_required("tester")
def api_video(test_id):
    t = load_test(test_id)
    if request.form.get("sample") == "1":
        name, path = SAMPLE_VIDEO, SAMPLE_DIR / SAMPLE_VIDEO
        stored = SAMPLE_VIDEO
    else:
        f = request.files.get("video")
        if not f or not f.filename:
            return jsonify(error="No video selected."), 400
        ext = Path(f.filename).suffix.lower()
        if ext not in VIDEO_EXT:
            return jsonify(error="Unsupported format. Use MP4, MOV or AVI."), 400
        d = UPLOADS / test_id
        d.mkdir(parents=True, exist_ok=True)
        stored = f"{uuid.uuid4().hex[:8]}_{secure_filename(f.filename) or 'video' + ext}"
        path = d / stored
        f.save(path)
        name = f.filename
    try:
        info = ocr.video_info(str(path))
    except ValueError as e:
        path.unlink(missing_ok=True)
        return jsonify(error=str(e)), 400
    db.save_test_fields(test_id, video_file=stored, video_name=name, video_duration=info["duration"], video_uploaded_at=db.now(),
                        video_sha256=sha256_file(path), reading_raw=None, reading_confirmed=None, reading_confirmed_at=None,
                        status="in_process" if t["status"] == "draft" else t["status"])
    db.audit(test_id, g.user["username"], "Video uploaded", name)
    return jsonify(ok=True, name=name, duration=info["duration"], url=url_for("serve_video", test_id=test_id), uploaded_at=db.now())


@app.route("/media/video/<test_id>")
def serve_video(test_id):
    t = load_test(test_id)
    p = video_path(t)
    if not p:
        abort(404)
    return send_file(p, conditional=True)


@app.post("/api/tests/<test_id>/extract")
@role_required("tester")
def api_extract(test_id):
    t = load_test(test_id)
    p = video_path(t)
    if not p:
        return jsonify(error="Upload a video first."), 400
    try:
        res = ocr.extract_reading(str(p))
    except ocr.OcrUnavailable as e:
        return jsonify(error=str(e), manual=True), 200
    except ValueError as e:
        return jsonify(error=str(e), manual=True), 200
    db.save_test_fields(test_id, reading_raw=res["reading"])
    db.audit(test_id, g.user["username"], "Reading extracted", f"{res['text']} kg (confidence {res['confidence']})" if res["reading"] is not None else "No reading detected")
    return jsonify(ok=True, **res)


@app.post("/api/tests/<test_id>/confirm")
@role_required("tester")
def api_confirm(test_id):
    t = load_test(test_id)
    val = r76._num((request.get_json(silent=True) or {}).get("reading"))
    if val is None or val < 0:
        return jsonify(error="Enter a valid numeric reading."), 400
    manual = t.get("reading_raw") is None or abs(t["reading_raw"] - val) > 1e-9
    db.save_test_fields(test_id, reading_confirmed=val, reading_confirmed_at=db.now(), status="in_process" if t["status"] == "draft" else t["status"])
    db.audit(test_id, g.user["username"], "Reading confirmed", f"{val:g} kg" + (" (edited / manual)" if manual else ""))
    return jsonify(ok=True, reading=val, edited=manual)


# ----------------------------------------------------------------------------- API : observations, evaluation, attachments
@app.post("/api/tests/<test_id>/evaluate")
def api_evaluate(test_id):
    """Live validation / calculation preview. Does not save."""
    t = load_test(test_id)
    body = request.get_json(silent=True) or {}
    rules = r76.load_rules(lab_settings().get("rules_file") or r76.DEFAULT_RULES)
    return jsonify(r76.evaluate_all(t, body.get("env", t["env"]), body.get("obs", t["obs"]), rules))


@app.post("/api/tests/<test_id>/observations")
@role_required("tester")
def api_save_obs(test_id):
    t = load_test(test_id)
    if not can_edit(t):
        return jsonify(error="Completed reports are locked. Ask an admin to reopen."), 409
    body = request.get_json(silent=True) or {}
    db.save_test_fields(test_id, env=body.get("env", {}), obs=body.get("obs", {}), status="in_process")
    t = load_test(test_id)
    res = recompute(t)
    db.audit(test_id, g.user["username"], "Observations saved", f"Overall: {res['overall']}")
    return jsonify(ok=True, result=res)


@app.post("/api/tests/<test_id>/generate")
@role_required("tester")
def api_generate(test_id):
    t = load_test(test_id)
    body = request.get_json(silent=True) or {}
    if "env" in body or "obs" in body:
        db.save_test_fields(test_id, env=body.get("env", t["env"]), obs=body.get("obs", t["obs"]))
        t = load_test(test_id)
    res = recompute(t)
    if res["overall"] == "INVALID":
        return jsonify(error="Instrument data is invalid: " + "; ".join(i["message"] for i in res["instrument_issues"] if i["level"] == "error")), 400
    if res["overall"] == "INCOMPLETE" and not body.get("allow_incomplete"):
        return jsonify(error=res["summary"], incomplete=True), 409
    generate_report(t)
    db.audit(test_id, g.user["username"], "Report generated", f"Result {res['overall']}; SHA-256 {t['report_hash'][:16]}…")
    return jsonify(ok=True, url=url_for("report_page", test_id=test_id))


@app.post("/api/tests/<test_id>/attachments")
@role_required("tester")
def api_attach(test_id):
    t = load_test(test_id)
    if not can_edit(t):
        return jsonify(error="Report is locked."), 409
    saved = []
    d = UPLOADS / test_id
    d.mkdir(parents=True, exist_ok=True)
    for f in request.files.getlist("files"):
        ext = Path(f.filename).suffix.lower()
        if ext not in PHOTO_EXT | DOC_EXT:
            return jsonify(error=f"{f.filename}: unsupported type."), 400
        stored = f"{uuid.uuid4().hex[:8]}_{secure_filename(f.filename)}"
        f.save(d / stored)
        kind = "photo" if ext in PHOTO_EXT else "document"
        aid = db.execute("INSERT INTO attachments(test_id,filename,stored,kind,sha256,size,uploaded_at,uploaded_by) VALUES(?,?,?,?,?,?,?,?)",
                         (test_id, f.filename, stored, kind, sha256_file(d / stored), (d / stored).stat().st_size, db.now(), g.user["username"]))
        db.audit(test_id, g.user["username"], "Attachment added", f.filename)
        saved.append(aid)
    return jsonify(ok=True, ids=saved)


@app.delete("/api/attachments/<int:aid>")
@role_required("tester")
def api_attach_delete(aid):
    a = db.q("SELECT * FROM attachments WHERE id=?", (aid,), one=True)
    if not a:
        abort(404)
    if not can_edit(load_test(a["test_id"])):
        return jsonify(error="Report is locked."), 409
    (UPLOADS / a["test_id"] / a["stored"]).unlink(missing_ok=True)
    db.execute("DELETE FROM attachments WHERE id=?", (aid,))
    db.audit(a["test_id"], g.user["username"], "Attachment removed", a["filename"])
    return jsonify(ok=True)


@app.route("/media/attachment/<int:aid>")
def serve_attachment(aid):
    a = db.q("SELECT * FROM attachments WHERE id=?", (aid,), one=True)
    if not a:
        abort(404)
    return send_from_directory(UPLOADS / a["test_id"], a["stored"], download_name=a["filename"])


@app.post("/api/tests/<test_id>/sign")
@role_required("tester")
def api_sign(test_id):
    t = ensure_report(load_test(test_id))
    if t["status"] != "completed":
        return jsonify(error="Generate the report first."), 400
    ts = db.now()
    name, desig = g.user["full_name"] or g.user["username"], g.user["designation"] or g.user["role"]
    mac = rpt.sign(app.secret_key, t["report_hash"], name, ts)
    db.save_test_fields(test_id, signed_by=name, signed_designation=desig, signed_at=ts, signature_mac=mac)
    t = load_test(test_id)
    generate_report(t, keep_time=True)
    db.audit(test_id, g.user["username"], "Report digitally signed", f"{name} ({desig})")
    return jsonify(ok=True, signed_by=name, at=ts)


@app.post("/api/tests/<test_id>/reopen")
@role_required("admin")
def api_reopen(test_id):
    t = load_test(test_id)
    db.save_test_fields(test_id, status="in_process", signed_by=None, signed_designation=None, signed_at=None, signature_mac=None)
    db.audit(test_id, g.user["username"], "Report reopened for editing", f"previous hash {str(t.get('report_hash'))[:16]}…")
    return jsonify(ok=True, url=url_for("observations_page", test_id=test_id))


@app.delete("/api/tests/<test_id>")
@role_required("admin")
def api_delete(test_id):
    load_test(test_id)
    for tbl in ("attachments", "audit"):
        db.execute(f"DELETE FROM {tbl} WHERE test_id=?", (test_id,))
    db.execute("DELETE FROM tests WHERE id=?", (test_id,))
    shutil.rmtree(UPLOADS / test_id, ignore_errors=True)
    shutil.rmtree(GENERATED / test_id, ignore_errors=True)
    return jsonify(ok=True)


@app.post("/api/validate-instrument")
def api_validate_instrument():
    body = request.get_json(silent=True) or {}
    for k in ("capacity", "division", "verification_interval"):
        body[k] = r76._num(body.get(k))
    body["verification_interval"] = body["verification_interval"] or body["division"]
    issues = r76.validate_instrument(body)
    n = body["capacity"] / body["verification_interval"] if body["capacity"] and body["verification_interval"] else None
    return jsonify(issues=issues, n=round(n) if n else None)


@app.errorhandler(403)
def e403(e):
    return render_template("error.html", code=403, msg="Your role does not permit this action."), 403


@app.errorhandler(404)
def e404(e):
    return render_template("error.html", code=404, msg="That page or record was not found."), 404


@app.errorhandler(413)
def e413(e):
    return jsonify(error="File too large (max 500 MB)."), 413


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)), debug=bool(os.environ.get("DEBUG")), threaded=True)
