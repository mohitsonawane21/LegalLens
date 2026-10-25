"""OIML R 76 calculation & compliance engine for Non-Automatic Weighing Instruments (NAWI).

All limits come from a JSON rule-set (rules/oiml_r76_*.json) so the engine can follow future
revisions of the recommendation without code changes.

Core formulas (R 76-1 / R 76-2):
    n          = Max / e                                   number of verification scale intervals
    m          = load / e                                  load expressed in scale intervals
    MPE(m)     = 0.5e | 1.0e | 1.5e                        band depends on accuracy class and m
    P          = I + 0.5e - dL                             indication before digital rounding
    E          = P - L                                     error corrected for rounding
    pass       = |E| <= MPE(L)

dL is the additional load needed to make the indication flash to the next changeover point.
If dL was not recorded, E = I - L is used and a warning is raised.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

RULES_DIR = Path(__file__).parent / "rules"
DEFAULT_RULES = "oiml_r76_2006.json"
EPS = 1e-9


@lru_cache(maxsize=4)
def load_rules(name: str = DEFAULT_RULES) -> dict:
    with open(RULES_DIR / name, encoding="utf-8") as fh:
        return json.load(fh)


# ----------------------------------------------------------------------------- helpers
def _num(v, default=None):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _r(x: float, nd: int = 6) -> float:
    return round(x + 0.0, nd)


def mpe(accuracy_class: str, load: float, e: float, rules: dict | None = None) -> float:
    """Maximum permissible error (same unit as `load`) for the given load."""
    rules = rules or load_rules()
    bands = rules["classes"][accuracy_class]["mpe_bands"]
    m = load / e
    for upper, factor in bands:
        if upper is None or m <= upper + EPS:
            return factor * e
    return bands[-1][1] * e  # beyond last band -> largest factor (flagged by validation)


def mpe_factor(accuracy_class: str, load: float, e: float, rules: dict | None = None) -> float:
    return mpe(accuracy_class, load, e, rules) / e


def error_of(load: float, indication: float, e: float, delta_l: float | None) -> tuple[float, bool]:
    """Return (error, rounding_corrected)."""
    if delta_l is None:
        return indication - load, False
    return (indication + 0.5 * e - delta_l) - load, True


# ----------------------------------------------------------------------------- validation
def validate_instrument(inst: dict, rules: dict | None = None) -> list[dict]:
    """Input validation of manufacturer / instrument data. Returns list of issues."""
    rules = rules or load_rules()
    issues: list[dict] = []

    def add(level, field, msg):
        issues.append({"level": level, "field": field, "message": msg})

    cls = inst.get("accuracy_class")
    cap, d, e = _num(inst.get("capacity")), _num(inst.get("division")), _num(inst.get("verification_interval"))
    if cls not in rules["classes"]:
        add("error", "accuracy_class", "Accuracy class must be one of I, II, III, IIII.")
    for key, val, label in (("capacity", cap, "Maximum capacity (Max)"), ("division", d, "Scale interval (d)")):
        if val is None or val <= 0:
            add("error", key, f"{label} must be a positive number.")
    if e is None:
        e = d
    if e is not None and d is not None and e + EPS < d:
        add("error", "verification_interval", "Verification scale interval e must be >= actual scale interval d.")
    if cap and e and cap / e < 1:
        add("error", "capacity", "Max must be larger than e.")
    if cls in rules["classes"] and cap and e and e > 0:
        n = cap / e
        unit_to_g = 1000.0 if inst.get("unit", "kg") == "kg" else 1.0
        e_g = e * unit_to_g
        limits = rules["scale_interval_limits"][cls]
        matched = False
        for lim in limits:
            lo = lim["e_min_g"]
            hi = lim["e_max_g"]
            if e_g + EPS >= lo and (hi is None or e_g <= hi + EPS):
                matched = True
                if n + EPS < lim["n_min"]:
                    add("error", "capacity", f"n = Max/e = {n:.0f} is below the minimum {lim['n_min']} allowed for class {cls}.")
                if lim["n_max"] and n > lim["n_max"] + EPS:
                    add("error", "capacity", f"n = Max/e = {n:.0f} exceeds the maximum {lim['n_max']} allowed for class {cls}.")
        if not matched:
            add("warning", "verification_interval", f"e = {e_g:g} g is outside the usual range for class {cls}; check class / interval.")
    for key, label in (("manufacturer", "Make / Manufacturer"), ("model", "Model"), ("serial_number", "Serial number")):
        if not str(inst.get(key, "")).strip():
            add("error", key, f"{label} is required.")
    return issues


def validate_environment(env: dict, rules: dict | None = None) -> list[dict]:
    rules = rules or load_rules()
    ref = rules["reference_conditions"]
    out = []
    for key, label, unit in (("temperature_c", "Temperature", "°C"), ("humidity_pct", "Relative humidity", "%"),
                             ("pressure_hpa", "Pressure", "hPa")):
        v = _num(env.get(key))
        if v is None:
            out.append({"level": "warning", "field": key, "message": f"{label} not recorded."})
            continue
        lo, hi = ref[key]
        if not lo <= v <= hi:
            out.append({"level": "warning", "field": key,
                        "message": f"{label} {v:g} {unit} is outside the reference range {lo}-{hi} {unit}."})
    return out


# ----------------------------------------------------------------------------- tests
def _row_result(cls, load, ind, dl, e, rules):
    err, corrected = error_of(load, ind, e, dl)
    lim = mpe(cls, load, e, rules)
    return {"load": _r(load), "indication": _r(ind), "delta_l": None if dl is None else _r(dl),
            "error": _r(err), "mpe": _r(lim), "mpe_factor": _r(lim / e, 2), "m": _r(load / e, 1),
            "rounding_corrected": corrected, "pass": abs(err) <= lim + EPS}


def evaluate_weighing(inst: dict, rows: list[dict], rules: dict | None = None) -> dict:
    """rows: [{load, ind_up, dl_up, ind_down, dl_down}] - increasing and decreasing load series."""
    rules = rules or load_rules()
    cls, e, cap = inst["accuracy_class"], inst["verification_interval"], inst["capacity"]
    d = inst["division"]
    issues, out_rows = [], []
    loads = [_num(r.get("load")) for r in rows]
    for i, r in enumerate(rows):
        L = _num(r.get("load"))
        if L is None:
            continue
        up = _num(r.get("ind_up"))
        down = _num(r.get("ind_down"))
        if L > cap + EPS:
            issues.append({"level": "error", "message": f"Row {i+1}: load {L:g} exceeds Max {cap:g}."})
            continue
        row = {"idx": i, "load": _r(L), "mpe": _r(mpe(cls, L, e, rules)), "up": None, "down": None}
        for key, ind, dl in (("up", up, _num(r.get("dl_up"))), ("down", down, _num(r.get("dl_down")))):
            if ind is None:
                continue
            if dl is not None and not (-EPS <= dl <= d + EPS):
                issues.append({"level": "warning", "message": f"Row {i+1} ({key}): additional load dL = {dl:g} should lie between 0 and d ({d:g})."})
            if abs(ind / d - round(ind / d)) > 1e-6 and abs(ind / d - round(ind / d)) > 0.01:
                issues.append({"level": "warning", "message": f"Row {i+1} ({key}): indication {ind:g} is not a multiple of d ({d:g}); it was used as entered."})
            if dl is None:
                issues.append({"level": "info", "message": f"Row {i+1} ({key}): dL not recorded - error computed as I - L (no rounding correction)."})
            row[key] = _row_result(cls, L, ind, dl, e, rules)
        out_rows.append(row)
    valid_loads = sorted(l for l in loads if l is not None)
    need = rules["tests"]["weighing"]["min_loads"]
    if len(valid_loads) < need:
        issues.append({"level": "error", "message": f"At least {need} test loads are required (found {len(valid_loads)})."})
    if valid_loads and valid_loads[-1] < 0.9 * cap:
        issues.append({"level": "warning", "message": "No test load close to Max (>= 90 % of Max) was recorded."})
    if valid_loads and valid_loads[0] > 0.1 * cap:
        issues.append({"level": "warning", "message": "No test load near Min / low range (<= 10 % of Max) was recorded."})
    checks = [c["pass"] for r in out_rows for c in (r["up"], r["down"]) if c]
    ok = bool(checks) and all(checks) and not any(i["level"] == "error" for i in issues)
    return {"rows": out_rows, "issues": issues, "pass": ok, "evaluated": bool(checks),
            "max_abs_error": _r(max([abs(c["error"]) for r in out_rows for c in (r["up"], r["down"]) if c] or [0]))}


def evaluate_eccentricity(inst: dict, load: float | None, rows: list[dict], rules: dict | None = None) -> dict:
    rules = rules or load_rules()
    cls, e, cap = inst["accuracy_class"], inst["verification_interval"], inst["capacity"]
    issues, out = [], []
    load = _num(load)
    if load is None or not rows:
        return {"rows": [], "issues": [{"level": "error", "message": "Eccentricity load and positions are required."}],
                "pass": False, "evaluated": False}
    if load < cap / 3 - EPS:
        issues.append({"level": "warning", "message": f"Eccentricity load {load:g} is below Max/3 ({cap/3:.3f})."})
    for i, r in enumerate(rows):
        ind = _num(r.get("indication"))
        if ind is None:
            continue
        res = _row_result(cls, load, ind, _num(r.get("delta_l")), e, rules)
        res["idx"] = i
        res["position"] = r.get("position", "")
        out.append(res)
    ok = bool(out) and all(r["pass"] for r in out)
    return {"load": _r(load), "rows": out, "issues": issues, "pass": ok, "evaluated": bool(out),
            "max_abs_error": _r(max([abs(r["error"]) for r in out] or [0]))}


def evaluate_repeatability(inst: dict, series: list[dict], rules: dict | None = None) -> dict:
    """series: [{load, readings:[{indication, delta_l}, ...]}] - normally ~50 % Max and ~Max, 3 weighings each."""
    rules = rules or load_rules()
    cls, e = inst["accuracy_class"], inst["verification_interval"]
    need = rules["tests"]["repeatability"]["weighings"]
    out, issues = [], []
    for si, s in enumerate(series):
        L = _num(s.get("load"))
        if L is None:
            continue
        errs = []
        for rd in s.get("readings", []):
            ind = _num(rd.get("indication"))
            if ind is None:
                continue
            errs.append(error_of(L, ind, e, _num(rd.get("delta_l")))[0])
        if len(errs) < need:
            issues.append({"level": "error", "message": f"Load {L:g}: {need} weighings are required (found {len(errs)})."})
            continue
        spread = max(errs) - min(errs)
        lim = mpe(cls, L, e, rules)
        out.append({"idx": si, "load": _r(L), "errors": [_r(x) for x in errs], "spread": _r(spread), "mpe": _r(lim),
                    "pass": spread <= lim + EPS})
    ok = bool(out) and all(r["pass"] for r in out) and not any(i["level"] == "error" for i in issues)
    return {"series": out, "issues": issues, "pass": ok, "evaluated": bool(out)}


def evaluate_discrimination(inst: dict, rows: list[dict], rules: dict | None = None) -> dict:
    """rows: [{load, ind_before, ind_after}] - after adding 1.4 d the indication must increase by at least d."""
    rules = rules or load_rules()
    d = inst["division"]
    extra = rules["tests"]["discrimination"]["extra_load_in_d"]
    out = []
    for i, r in enumerate(rows):
        L, a, b = _num(r.get("load")), _num(r.get("ind_before")), _num(r.get("ind_after"))
        if None in (L, a, b):
            continue
        change = b - a
        out.append({"idx": i, "load": _r(L), "ind_before": _r(a), "ind_after": _r(b), "added_load": _r(extra * d),
                    "change": _r(change), "required": _r(d), "pass": change + EPS >= d})
    ok = bool(out) and all(r["pass"] for r in out)
    return {"rows": out, "issues": [], "pass": ok, "evaluated": bool(out)}


# ----------------------------------------------------------------------------- orchestration
TEST_KEYS = ["weighing", "eccentricity", "repeatability", "discrimination"]


def evaluate_all(inst: dict, env: dict, obs: dict, rules: dict | None = None) -> dict:
    """Evaluate everything entered for a test and determine overall compliance."""
    rules = rules or load_rules()
    inst = dict(inst)
    inst["capacity"] = _num(inst.get("capacity"))
    inst["division"] = _num(inst.get("division"))
    inst["verification_interval"] = _num(inst.get("verification_interval")) or inst["division"]
    inst_issues = validate_instrument(inst, rules)
    env_issues = validate_environment(env or {}, rules)
    blocking = any(i["level"] == "error" for i in inst_issues)
    res: dict = {"edition": rules["edition"], "instrument_issues": inst_issues, "environment_issues": env_issues,
                 "n": _r(inst["capacity"] / inst["verification_interval"], 0) if not blocking else None}
    if blocking:
        res.update({"tests": {}, "overall": "INVALID",
                    "summary": "Instrument data failed validation; compliance cannot be determined."})
        return res
    obs = obs or {}
    tests = {}
    tests["weighing"] = evaluate_weighing(inst, obs.get("weighing", {}).get("rows", []), rules)
    ecc = obs.get("eccentricity", {})
    tests["eccentricity"] = evaluate_eccentricity(inst, ecc.get("load"), ecc.get("rows", []), rules)
    tests["repeatability"] = evaluate_repeatability(inst, obs.get("repeatability", {}).get("series", []), rules)
    tests["discrimination"] = evaluate_discrimination(inst, obs.get("discrimination", {}).get("rows", []), rules)
    for k, t in tests.items():
        t["title"] = rules["tests"][k]["title"]
        t["clause"] = rules["tests"][k]["clause"]
    res["tests"] = tests
    evaluated = [t for t in tests.values() if t["evaluated"]]
    if len(evaluated) < len(tests):
        res["overall"] = "INCOMPLETE"
        missing = [t["title"] for t in tests.values() if not t["evaluated"]]
        res["summary"] = "Observations missing for: " + ", ".join(missing) + "."
    elif all(t["pass"] for t in evaluated):
        res["overall"] = "PASS"
        res["summary"] = "Instrument is within permissible limits as per OIML R-76."
    else:
        res["overall"] = "FAIL"
        failed = [t["title"] for t in tests.values() if not t["pass"]]
        res["summary"] = "Instrument exceeds permissible limits in: " + ", ".join(failed) + "."
    return res


def round_to_division(value: float, d: float) -> float:
    """Snap a raw extracted display reading to the instrument's scale interval."""
    if not d:
        return value
    return round(round(value / d) * d, 10)
