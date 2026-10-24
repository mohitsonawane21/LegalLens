"""Calculation + validation engine for OIML R 76-1 type-evaluation tests (pure Python).

Every test type = validator + evaluator + methodology text, registered in TESTS.
To add a new test (tilting, voltage, creep, warm-up ...) write the two functions and
add one entry to TESTS - API, report, dashboard and compliance pick it up automatically.
"""
import math
from .rules import mpe, TOL, UNIT_TO_KG, Spec


# ----------------------------------------------------------------- helpers
def r6(x):
    return round(float(x), 6)


def within(value, limit):
    return round(abs(value), 6) <= round(limit, 6)


def is_multiple(value, step):
    q = value / step
    return abs(q - round(q)) < 1e-6


def err_from_reading(indication, extra_load, load, d):
    """E = I + 0.5 d - dL - L (changeover-point method). If dL not recorded: E = I - L."""
    if extra_load is None:
        return indication - load
    return indication + 0.5 * d - extra_load - load


def _num(obj, key, path, errors, required=True, minimum=None, exclusive_min=False):
    v = obj.get(key) if isinstance(obj, dict) else None
    if v is None:
        if required:
            errors.append(f"{path}.{key} is required")
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        errors.append(f"{path}.{key} must be a finite number")
        return None
    if minimum is not None and (v < minimum or (exclusive_min and v <= minimum)):
        errors.append(f"{path}.{key} must be {'>' if exclusive_min else '>='} {minimum}")
        return None
    return float(v)


def _items(obj, key, path, errors, min_len):
    items = obj.get(key) if isinstance(obj, dict) else None
    if not isinstance(items, list):
        errors.append(f"{path}.{key} must be a list")
        return []
    if len(items) < min_len:
        errors.append(f"{path}.{key} needs at least {min_len} entries (got {len(items)})")
    return items


def _load_ok(load, s, path, errors):
    if load > s.max_capacity + TOL:
        errors.append(f"{path}.load {load:g} exceeds Max capacity {s.max_capacity:g}")


def _resolution_warn(value, s, path, warnings):
    if value is not None and not is_multiple(value, s.d):
        warnings.append(f"{path} = {value:g} is not a multiple of d = {s.d:g}")


# ----------------------------------------------------------------- weighing test
def v_weighing(raw, s, std):
    errors, warnings, pts = [], [], []
    zero_error = _num(raw, "zero_error", "data", errors, required=False) or 0.0
    for i, p in enumerate(_items(raw, "points", "data", errors, 5)):
        path = f"points[{i}]"
        if not isinstance(p, dict):
            errors.append(f"{path} must be an object")
            continue
        load = _num(p, "load", path, errors, minimum=0)
        up = _num(p, "indication_up", path, errors)
        ex_up = _num(p, "extra_load_up", path, errors, required=False, minimum=0)
        dn = _num(p, "indication_down", path, errors, required=False)
        ex_dn = _num(p, "extra_load_down", path, errors, required=False, minimum=0)
        if load is None or up is None:
            continue
        _load_ok(load, s, path, errors)
        _resolution_warn(up, s, f"{path}.indication_up", warnings)
        _resolution_warn(dn, s, f"{path}.indication_down", warnings)
        pts.append({"load": load, "indication_up": up, "extra_load_up": ex_up,
                    "indication_down": dn, "extra_load_down": ex_dn if dn is not None else None})
    loads = [p["load"] for p in pts]
    if len(set(loads)) != len(loads):
        errors.append("Duplicate load values in 'points'")
    if pts and max(loads) < s.max_capacity - TOL:
        warnings.append("No weighing point at Max capacity (R 76 requires tests up to Max)")
    return {"zero_error": zero_error, "points": pts}, errors, warnings


def ev_weighing(d, s, std):
    rows, ok_all = [], True
    for p in sorted(d["points"], key=lambda x: x["load"]):
        limit = mpe(std, s.accuracy_class, p["load"], s.e)
        for direction, ind, extra in (
            ("Increasing", p["indication_up"], p["extra_load_up"]),
            ("Decreasing", p["indication_down"], p["extra_load_down"]),
        ):
            if ind is None:
                continue
            E = err_from_reading(ind, extra, p["load"], s.d)
            Ec = E - d["zero_error"]
            ok = within(Ec, limit)
            ok_all = ok_all and ok
            rows.append({"load": p["load"], "direction": direction, "indication": ind,
                         "extra_load": extra, "error": r6(E), "corrected_error": r6(Ec),
                         "mpe": r6(limit), "passed": ok})
    return {
        "passed": ok_all,
        "columns": [["load", f"Load ({s.unit})"], ["direction", "Direction"], ["indication", "Indication I"],
                    ["extra_load", "Extra load dL"], ["error", "Error E"],
                    ["corrected_error", "Corrected error Ec"], ["mpe", "MPE (+/-)"], ["passed", "Result"]],
        "rows": rows,
        "summary": {"points_evaluated": len(rows),
                    "max_abs_corrected_error": max((abs(r["corrected_error"]) for r in rows), default=0),
                    "failed": sum(1 for r in rows if not r["passed"]), "unit": s.unit},
    }


# ----------------------------------------------------------------- eccentricity
def v_ecc(raw, s, std):
    errors, warnings, pos = [], [], []
    load = _num(raw, "load", "data", errors, minimum=0, exclusive_min=True)
    zero_error = _num(raw, "zero_error", "data", errors, required=False) or 0.0
    if load is not None:
        _load_ok(load, s, "data", errors)
        if load < s.max_capacity / 3 - TOL:
            warnings.append("Eccentricity load is below 1/3 of Max")
    for i, p in enumerate(_items(raw, "positions", "data", errors, 4)):
        path = f"positions[{i}]"
        if not isinstance(p, dict):
            errors.append(f"{path} must be an object")
            continue
        ind = _num(p, "indication", path, errors)
        ex = _num(p, "extra_load", path, errors, required=False, minimum=0)
        _resolution_warn(ind, s, f"{path}.indication", warnings)
        if ind is not None:
            pos.append({"position": str(p.get("position", i + 1)), "indication": ind, "extra_load": ex})
    return {"load": load, "zero_error": zero_error, "positions": pos}, errors, warnings


def ev_ecc(d, s, std):
    limit = mpe(std, s.accuracy_class, d["load"], s.e)
    rows = []
    for p in d["positions"]:
        E = err_from_reading(p["indication"], p["extra_load"], d["load"], s.d)
        Ec = E - d["zero_error"]
        rows.append({"position": p["position"], "load": d["load"], "indication": p["indication"],
                     "error": r6(E), "corrected_error": r6(Ec), "mpe": r6(limit), "passed": within(Ec, limit)})
    return {
        "passed": all(r["passed"] for r in rows),
        "columns": [["position", "Position"], ["load", f"Load ({s.unit})"], ["indication", "Indication I"],
                    ["error", "Error E"], ["corrected_error", "Corrected error Ec"],
                    ["mpe", "MPE (+/-)"], ["passed", "Result"]],
        "rows": rows,
        "summary": {"max_abs_corrected_error": max(abs(r["corrected_error"]) for r in rows), "unit": s.unit},
    }


# ----------------------------------------------------------------- repeatability
def v_rep(raw, s, std):
    errors, warnings, series = [], [], []
    cfg = std["repeatability"]
    max_kg = s.max_capacity * UNIT_TO_KG[s.unit]
    recommended = cfg["readings_if_at_or_below"] if max_kg <= cfg["max_kg_threshold"] else cfg["readings_if_above"]
    for i, ser in enumerate(_items(raw, "series", "data", errors, 1)):
        path = f"series[{i}]"
        if not isinstance(ser, dict):
            errors.append(f"{path} must be an object")
            continue
        load = _num(ser, "load", path, errors, minimum=0, exclusive_min=True)
        if load is not None:
            _load_ok(load, s, path, errors)
        readings = []
        for j, rd in enumerate(_items(ser, "readings", path, errors, cfg["minimum_readings"])):
            if isinstance(rd, (int, float)) and not isinstance(rd, bool):
                rd = {"indication": rd}
            ind = _num(rd, "indication", f"{path}.readings[{j}]", errors)
            ex = _num(rd, "extra_load", f"{path}.readings[{j}]", errors, required=False, minimum=0)
            if ind is not None:
                _resolution_warn(ind, s, f"{path}.readings[{j}]", warnings)
                readings.append({"indication": ind, "extra_load": ex})
        if len(readings) < recommended:
            warnings.append(f"{path}: {len(readings)} readings given; {recommended} recommended for Max = {max_kg:g} kg")
        if load is not None:
            series.append({"load": load, "readings": readings})
    if len(series) < 2:
        warnings.append("Repeatability is normally tested at about 50 % of Max and at about Max (two series)")
    return {"series": series}, errors, warnings


def ev_rep(d, s, std):
    rows = []
    for ser in d["series"]:
        errs = [err_from_reading(r["indication"], r["extra_load"], ser["load"], s.d) for r in ser["readings"]]
        limit = mpe(std, s.accuracy_class, ser["load"], s.e)
        spread = max(errs) - min(errs)
        rows.append({"load": ser["load"], "readings": len(errs), "min_error": r6(min(errs)),
                     "max_error": r6(max(errs)), "spread": r6(spread), "mpe": r6(limit),
                     "passed": within(spread, limit)})
    return {
        "passed": all(r["passed"] for r in rows),
        "columns": [["load", f"Load ({s.unit})"], ["readings", "No. of readings"], ["min_error", "Min error"],
                    ["max_error", "Max error"], ["spread", "Spread (max-min)"], ["mpe", "MPE (+/-)"],
                    ["passed", "Result"]],
        "rows": rows,
        "summary": {"series": len(rows), "unit": s.unit},
    }


# ----------------------------------------------------------------- discrimination
def v_disc(raw, s, std):
    errors, warnings, pts = [], [], []
    factor = std["discrimination_added_factor"]
    for i, p in enumerate(_items(raw, "points", "data", errors, 1)):
        path = f"points[{i}]"
        if not isinstance(p, dict):
            errors.append(f"{path} must be an object")
            continue
        load = _num(p, "load", path, errors, minimum=0)
        added = _num(p, "added_weight", path, errors, minimum=0, exclusive_min=True)
        before = _num(p, "indication_before", path, errors)
        after = _num(p, "indication_after", path, errors)
        if None in (load, added, before, after):
            continue
        _load_ok(load, s, path, errors)
        if abs(added - factor * s.d) > 0.1 * s.d:
            warnings.append(f"{path}.added_weight = {added:g}; the test uses {factor} x d = {factor * s.d:g}")
        pts.append({"load": load, "added_weight": added, "indication_before": before, "indication_after": after})
    return {"points": pts}, errors, warnings


def ev_disc(d, s, std):
    rows = []
    for p in d["points"]:
        change = p["indication_after"] - p["indication_before"]
        rows.append({"load": p["load"], "added_weight": p["added_weight"], "indication_before": p["indication_before"],
                     "indication_after": p["indication_after"], "change": r6(change), "required_change": s.d,
                     "passed": round(change, 6) >= round(s.d, 6)})
    return {
        "passed": all(r["passed"] for r in rows),
        "columns": [["load", f"Load ({s.unit})"], ["added_weight", "Added weight"],
                    ["indication_before", "Indication before"], ["indication_after", "Indication after"],
                    ["change", "Change"], ["required_change", "Required (>= d)"], ["passed", "Result"]],
        "rows": rows,
        "summary": {"points": len(rows), "unit": s.unit},
    }


# ----------------------------------------------------------------- temperature effect on zero
def v_temp(raw, s, std):
    errors, warnings, pts = [], [], []
    for i, p in enumerate(_items(raw, "points", "data", errors, 2)):
        path = f"points[{i}]"
        if not isinstance(p, dict):
            errors.append(f"{path} must be an object")
            continue
        t = _num(p, "temperature_c", path, errors)
        z = _num(p, "zero_indication", path, errors)
        if t is not None and not -50 <= t <= 100:
            errors.append(f"{path}.temperature_c out of plausible range")
        if t is not None and z is not None:
            _resolution_warn(z, s, f"{path}.zero_indication", warnings)
            pts.append({"temperature_c": t, "zero_indication": z})
    if len(pts) >= 2 and all(abs(p["temperature_c"] - pts[0]["temperature_c"]) < TOL for p in pts):
        errors.append("At least two different temperatures are required")
    return {"points": pts}, errors, warnings


def ev_temp(d, s, std):
    step = std["temperature_step_c"][s.accuracy_class]
    rows = []
    for a, b in zip(d["points"], d["points"][1:]):
        dT = abs(b["temperature_c"] - a["temperature_c"])
        if dT < TOL:
            continue
        dI = abs(b["zero_indication"] - a["zero_indication"])
        per_step = dI * step / max(dT, step)
        rows.append({"from_c": a["temperature_c"], "to_c": b["temperature_c"], "delta_t": r6(dT),
                     "zero_from": a["zero_indication"], "zero_to": b["zero_indication"], "zero_change": r6(dI),
                     "change_per_step": r6(per_step), "limit": s.e, "passed": within(per_step, s.e)})
    return {
        "passed": all(r["passed"] for r in rows),
        "columns": [["from_c", "From (C)"], ["to_c", "To (C)"], ["delta_t", "Delta T"], ["zero_from", "Zero at start"],
                    ["zero_to", "Zero at end"], ["zero_change", "Zero change"],
                    ["change_per_step", f"Change per {step:g} C"], ["limit", "Limit (e)"], ["passed", "Result"]],
        "rows": rows,
        "summary": {"step_c": step, "unit": s.unit},
    }


# ----------------------------------------------------------------- registry
TESTS = {
    "weighing": {
        "label": "Weighing (accuracy) test", "validate": v_weighing, "evaluate": ev_weighing,
        "methodology": "Error E = I + 0.5d - dL - L (changeover-point method; E = I - L if dL is not recorded). "
                       "Corrected error Ec = E - E0 (E0 = error at zero). Pass if |Ec| <= MPE for the load L "
                       "(OIML R 76-1 clause 3.5, MPE bands by accuracy class, expressed in e)."},
    "eccentricity": {
        "label": "Eccentricity test", "validate": v_ecc, "evaluate": ev_ecc,
        "methodology": "Same load placed on each load-receptor position. Error computed as for the weighing test "
                       "and corrected for the error at zero. Pass if |Ec| <= MPE for the test load at every position."},
    "repeatability": {
        "label": "Repeatability test", "validate": v_rep, "evaluate": ev_rep,
        "methodology": "Repeated weighings of the same load. Spread = max(E) - min(E). "
                       "Pass if spread <= |MPE| for that load."},
    "discrimination": {
        "label": "Discrimination test", "validate": v_disc, "evaluate": ev_disc,
        "methodology": "At equilibrium the load is increased by 1.4 d. Pass if the indication increases by at least d."},
    "temperature_zero": {
        "label": "Temperature effect on zero", "validate": v_temp, "evaluate": ev_temp,
        "methodology": "Zero indication recorded at different ambient temperatures. Change per temperature step "
                       "(1 / 2 / 5 C for classes I / II / III-IIII) must not exceed e. Changes over larger "
                       "temperature differences are scaled down linearly to one step."},
}


def evaluate_observation(test_type, raw, spec: Spec, std):
    """Validate + calculate. Returns {errors, warnings, data, result}; result is None if errors."""
    if not isinstance(raw, dict):
        return {"errors": ["'data' must be an object"], "warnings": [], "data": None, "result": None}
    t = TESTS[test_type]
    clean, errors, warnings = t["validate"](raw, spec, std)
    if errors:
        return {"errors": errors, "warnings": warnings, "data": None, "result": None}
    result = t["evaluate"](clean, spec, std)
    result["methodology"] = t["methodology"]
    return {"errors": [], "warnings": warnings, "data": clean, "result": result}


def compile_compliance(std, passed_by_test: dict):
    """passed_by_test: {test_type: bool}. Overall: FAIL if any test failed, INCOMPLETE if a required test is missing."""
    required = std["required_tests"]
    optional = std.get("optional_tests", [])
    rows = []
    for tt in list(required) + [t for t in optional if t not in required]:
        rows.append({"test_type": tt, "label": TESTS[tt]["label"], "required": tt in required,
                     "performed": tt in passed_by_test, "passed": passed_by_test.get(tt)})
    missing = [t for t in required if t not in passed_by_test]
    failed = [t for t, ok in passed_by_test.items() if not ok]
    overall = "FAIL" if failed else ("INCOMPLETE" if missing else "PASS")
    return {"overall": overall, "tests": rows, "missing": missing, "failed": failed}
