"""Versioned OIML R 76 rule sets + classification / MPE helpers.

All numeric requirements live in JSON files in this folder. To support a revised
recommendation, add a new JSON file with a new "id" - no code change needed.
"""
import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

STD_DIR = Path(__file__).parent
DEFAULT_STANDARD = "OIML R 76-1:2006"
UNIT_TO_KG = {"g": 0.001, "kg": 1.0, "t": 1000.0}
TOL = 1e-9


@lru_cache(maxsize=None)
def _all() -> dict:
    out = {}
    for f in sorted(STD_DIR.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        out[data["id"]] = data
    return out


def available_standards() -> list:
    return [
        {"id": s["id"], "title": s["title"], "required_tests": s["required_tests"],
         "optional_tests": s.get("optional_tests", [])}
        for s in _all().values()
    ]


def get_standard(std_id: Optional[str] = None) -> dict:
    std_id = std_id or DEFAULT_STANDARD
    try:
        return _all()[std_id]
    except KeyError:
        raise ValueError(f"Unknown standard '{std_id}'. Available: {', '.join(_all())}")


@dataclass
class Spec:
    accuracy_class: str
    max_capacity: float
    min_capacity: float
    d: float
    e: float
    unit: str = "kg"


def mpe(std: dict, accuracy_class: str, load: float, e: float, in_service: bool = False) -> float:
    """Maximum permissible error (same unit as load) for a given load."""
    bands = std["mpe_verification"][accuracy_class]
    m = round(load / e, 6)  # load expressed in verification scale intervals
    factor = bands[-1][1]
    for limit, f in bands:
        if limit is None or m <= limit:
            factor = f
            break
    value = factor * e
    if in_service:
        value *= std.get("in_service_factor", 2)
    return round(value, 9)


def validate_instrument(std: dict, s: Spec):
    """Checks Max / d / e / n against the accuracy-class limits. Returns (errors, warnings)."""
    errors, warnings = [], []
    if s.accuracy_class not in std["mpe_verification"]:
        return [f"Unknown accuracy class '{s.accuracy_class}'"], warnings
    if s.d <= 0 or s.e <= 0 or s.max_capacity <= 0:
        return ["Max, d and e must be positive"], warnings
    if s.unit not in UNIT_TO_KG:
        return [f"Unit must be one of {list(UNIT_TO_KG)}"], warnings
    if s.e < s.d - TOL:
        errors.append("Verification scale interval e must be >= actual scale interval d")
    if s.accuracy_class in ("III", "IIII") and abs(s.e - s.d) > TOL:
        errors.append("For classes III and IIII, e must be equal to d")
    if s.accuracy_class in ("I", "II") and s.e > 10 * s.d + TOL:
        warnings.append("e is more than 10 x d; check the declared intervals")
    if s.min_capacity is not None and s.min_capacity >= s.max_capacity:
        errors.append("Min capacity must be smaller than Max")
    mant = s.e / 10 ** math.floor(math.log10(s.e))
    if not any(abs(mant - x) < 1e-6 for x in (1, 2, 5, 10)):
        warnings.append("e is not of the form 1, 2 or 5 x 10^k")

    n = s.max_capacity / s.e
    e_g = s.e * UNIT_TO_KG[s.unit] * 1000.0
    band = next(
        (b for b in std["n_limits"]
         if b["class"] == s.accuracy_class and e_g >= b["e_min_g"] - TOL
         and (b["e_max_g"] is None or e_g < b["e_max_g"] - TOL)),
        None,
    )
    if band is None:
        errors.append(f"e = {e_g:g} g is outside the range permitted for class {s.accuracy_class}")
    else:
        if n < band["n_min"]:
            errors.append(f"Number of verification intervals n = {n:g} is below the minimum {band['n_min']} for class {s.accuracy_class}")
        if band["n_max"] is not None and n > band["n_max"]:
            errors.append(f"Number of verification intervals n = {n:g} exceeds the maximum {band['n_max']} for class {s.accuracy_class}")
    return errors, warnings
