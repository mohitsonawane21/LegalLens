"""SQLite persistence layer (standard library only) + demo seed data."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from werkzeug.security import generate_password_hash

BASE = Path(__file__).parent
import os
DATA_DIR = Path(os.environ.get("LEGAL_LENS_DATA") or BASE / "data")
DB_PATH = DATA_DIR / "legal_lens.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','tester','viewer')), full_name TEXT, designation TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS tests(
  id TEXT PRIMARY KEY,
  instrument_id TEXT, instrument_type TEXT, manufacturer TEXT, model TEXT, serial_number TEXT,
  capacity REAL, division REAL, verification_interval REAL, unit TEXT DEFAULT 'kg',
  accuracy_class TEXT, location TEXT, test_standard TEXT DEFAULT 'OIML R-76',
  test_date TEXT, created_by TEXT, created_at TEXT,
  status TEXT DEFAULT 'draft',                 -- draft | in_process | completed
  env TEXT DEFAULT '{}', obs TEXT DEFAULT '{}', result TEXT DEFAULT '{}',
  video_file TEXT, video_name TEXT, video_duration REAL, video_uploaded_at TEXT, video_sha256 TEXT,
  reading_raw REAL, reading_confirmed REAL, reading_confirmed_at TEXT,
  report_generated_at TEXT, report_hash TEXT, report_version INTEGER DEFAULT 0, verify_url TEXT,
  signed_by TEXT, signed_designation TEXT, signed_at TEXT, signature_mac TEXT);
CREATE TABLE IF NOT EXISTS attachments(
  id INTEGER PRIMARY KEY AUTOINCREMENT, test_id TEXT NOT NULL, filename TEXT, stored TEXT, kind TEXT,
  sha256 TEXT, size INTEGER, uploaded_at TEXT, uploaded_by TEXT);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, test_id TEXT, ts TEXT, user TEXT, action TEXT, detail TEXT);
"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def q(sql: str, args=(), one=False):
    con = connect()
    try:
        cur = con.execute(sql, args)
        rows = cur.fetchall()
        con.commit()
        return (rows[0] if rows else None) if one else rows
    finally:
        con.close()


def execute(sql: str, args=()):
    con = connect()
    try:
        cur = con.execute(sql, args)
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def get_setting(key: str, default: str = "") -> str:
    r = q("SELECT value FROM settings WHERE key=?", (key,), one=True)
    return r["value"] if r else default


def set_setting(key: str, value: str) -> None:
    execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def audit(test_id: str, user: str, action: str, detail: str = "") -> None:
    execute("INSERT INTO audit(test_id,ts,user,action,detail) VALUES(?,?,?,?,?)", (test_id, now(), user, action, detail))


def test_dict(row) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k in ("env", "obs", "result"):
        d[k] = json.loads(d[k] or "{}")
    return d


def get_test(test_id: str) -> dict | None:
    return test_dict(q("SELECT * FROM tests WHERE id=?", (test_id,), one=True))


def save_test_fields(test_id: str, **fields) -> None:
    if not fields:
        return
    for k in ("env", "obs", "result"):
        if k in fields and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k])
    cols = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE tests SET {cols} WHERE id=?", (*fields.values(), test_id))


def next_test_id(year: int | None = None) -> str:
    year = year or datetime.now().year
    r = q("SELECT id FROM tests WHERE id LIKE ? ORDER BY id DESC LIMIT 1", (f"TST-{year}-%",), one=True)
    n = int(r["id"].rsplit("-", 1)[1]) + 1 if r else 1
    return f"TST-{year}-{n:05d}"


# ----------------------------------------------------------------------------- seed
def _seed_obs_pass() -> dict:
    return {
        "weighing": {"rows": [
            {"load": 0.1, "ind_up": 0.100, "dl_up": 0.002, "ind_down": 0.100, "dl_down": 0.002},
            {"load": 2.5, "ind_up": 2.500, "dl_up": 0.001, "ind_down": 2.500, "dl_down": 0.002},
            {"load": 10, "ind_up": 10.000, "dl_up": 0.002, "ind_down": 10.005, "dl_down": 0.004},
            {"load": 20, "ind_up": 20.005, "dl_up": 0.003, "ind_down": 20.005, "dl_down": 0.002},
            {"load": 30, "ind_up": 30.005, "dl_up": 0.004, "ind_down": 30.005, "dl_down": 0.004}]},
        "eccentricity": {"load": 10, "rows": [
            {"position": "Centre", "indication": 10.000, "delta_l": 0.002},
            {"position": "Front-Left", "indication": 10.000, "delta_l": 0.001},
            {"position": "Front-Right", "indication": 10.005, "delta_l": 0.003},
            {"position": "Back-Left", "indication": 10.000, "delta_l": 0.002},
            {"position": "Back-Right", "indication": 10.005, "delta_l": 0.003}]},
        "repeatability": {"series": [
            {"load": 15, "readings": [{"indication": 15.000, "delta_l": 0.002}, {"indication": 15.005, "delta_l": 0.003},
                                       {"indication": 15.000, "delta_l": 0.002}]},
            {"load": 30, "readings": [{"indication": 30.005, "delta_l": 0.004}, {"indication": 30.005, "delta_l": 0.004},
                                       {"indication": 30.005, "delta_l": 0.003}]}]},
        "discrimination": {"rows": [
            {"load": 10, "ind_before": 10.000, "ind_after": 10.005},
            {"load": 30, "ind_before": 30.000, "ind_after": 30.005}]},
    }


def _seed_obs_fail() -> dict:
    o = _seed_obs_pass()
    o["weighing"]["rows"][4].update({"ind_up": 30.020, "dl_up": 0.002})   # 0.0205 error > 0.0075 -> FAIL
    return o


ENV_OK = {"temperature_c": 23.4, "humidity_pct": 52, "pressure_hpa": 1008, "weights_used": "F1 class set, cert. SW-2026-114",
          "tested_by": "Dr. Pallavi Baviskar", "remarks": "Instrument warmed up for 30 minutes before testing."}


def init_db(seed: bool = True) -> None:
    con = connect()
    con.executescript(SCHEMA)
    con.commit()
    empty = con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    con.close()
    if not (seed and empty):
        return
    for u, pw, role, name, desig in (
            ("admin", "admin123", "admin", "System Administrator", "Administrator"),
            ("tester", "tester123", "tester", "Dr. Pallavi Baviskar", "Test Officer"),
            ("viewer", "viewer123", "viewer", "Report Viewer", "Auditor")):
        execute("INSERT INTO users(username,password_hash,role,full_name,designation) VALUES(?,?,?,?,?)",
                (u, generate_password_hash(pw), role, name, desig))
    for k, v in (("lab_name", "SIEM Lab"), ("lab_section", "Testing Section"),
                 ("signatory_name", "Dr. Pallavi Baviskar"), ("signatory_designation", "Test Officer"),
                 ("rules_file", "oiml_r76_2006.json")):
        set_setting(k, v)
    _seed_tests()


def _seed_tests() -> None:
    import r76  # local import to avoid cycles at module load
    base = dict(test_standard="OIML R-76", created_by="admin", unit="kg")
    specs = [
        dict(id="TST-2026-00123", instrument_id="NAWI-0018", instrument_type="Weighing Scale", manufacturer="Essae",
             model="ED-H", serial_number="ES-45872", capacity=30, division=0.005, verification_interval=0.005,
             accuracy_class="III", location="SIEM Lab – Testing Section", test_date="2026-09-17",
             created_at="2026-09-17 10:02:11", status="completed", obs=_seed_obs_pass(),
             video_file="sample_scale_reading.mp4", video_name="sample_scale_reading.mp4", video_duration=12,
             video_uploaded_at="2026-09-17 10:24:00", reading_raw=25.162626, reading_confirmed=25.162626,
             reading_confirmed_at="2026-09-17 10:24:30", report_generated_at="2026-09-17 10:24:55"),
        dict(id="TST-2026-00122", instrument_id="NAWI-0011", instrument_type="Platform Scale", manufacturer="Avery India",
             model="PS-30", serial_number="AV-30921", capacity=30, division=0.005, verification_interval=0.005,
             accuracy_class="III", location="SIEM Lab – Testing Section", test_date="2026-09-12",
             created_at="2026-09-12 14:10:00", status="completed", obs=_seed_obs_fail(),
             report_generated_at="2026-09-12 15:02:10"),
        dict(id="TST-2026-00124", instrument_id="NAWI-0021", instrument_type="Counter Scale", manufacturer="Libra Weigh",
             model="LW-15", serial_number="LW-77014", capacity=15, division=0.002, verification_interval=0.005,
             accuracy_class="III", location="SIEM Lab – Testing Section", test_date="2026-10-20",
             created_at="2026-10-20 11:45:00", status="in_process",
             obs={"weighing": {"rows": [
                 {"load": 0.1, "ind_up": 0.100, "dl_up": 0.002, "ind_down": 0.100, "dl_down": 0.002},
                 {"load": 2.5, "ind_up": 2.500, "dl_up": 0.001, "ind_down": 2.500, "dl_down": 0.002},
                 {"load": 5, "ind_up": 5.000, "dl_up": 0.002, "ind_down": 5.005, "dl_down": 0.004},
                 {"load": 10, "ind_up": 10.000, "dl_up": 0.002, "ind_down": 10.005, "dl_down": 0.004},
                 {"load": 15, "ind_up": 15.005, "dl_up": 0.004, "ind_down": 15.005, "dl_down": 0.003}]}}),
    ]
    for s in specs:
        s = {**base, **s}
        obs = s.pop("obs")
        env = ENV_OK if s["status"] != "draft" else {}
        cols = list(s) + ["env", "obs"]
        vals = list(s.values()) + [json.dumps(env), json.dumps(obs)]
        execute(f"INSERT INTO tests({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals)
        t = get_test(s["id"])
        result = r76.evaluate_all(t, t["env"], t["obs"])
        save_test_fields(s["id"], result=result)
        audit(s["id"], "admin", "Test created", f"Instrument {s['instrument_id']}")
        if s.get("video_file"):
            audit(s["id"], "admin", "Video uploaded", s["video_name"])
            audit(s["id"], "admin", "Reading confirmed", f"{s['reading_confirmed']} kg")
        audit(s["id"], "tester", "Observations saved", "All prescribed tests" if s["status"] == "completed" else "Weighing test only")
