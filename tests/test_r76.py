"""Unit tests for the OIML R 76 calculation engine (run: python -m pytest)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import r76  # noqa: E402

INST = dict(accuracy_class="III", capacity=30.0, division=0.005, verification_interval=0.005, unit="kg",
            manufacturer="Essae", model="ED-H", serial_number="X1")


def test_mpe_bands_class_iii():
    e = 0.005
    assert r76.mpe("III", 2.5, e) == 0.5 * e          # m = 500  -> 0.5 e
    assert r76.mpe("III", 2.505, e) == 1.0 * e        # m = 501  -> 1.0 e
    assert r76.mpe("III", 10.0, e) == 1.0 * e         # m = 2000 -> 1.0 e
    assert abs(r76.mpe("III", 10.005, e) - 1.5 * e) < 1e-12   # m = 2001 -> 1.5 e
    assert abs(r76.mpe("III", 30.0, e) - 1.5 * e) < 1e-12


def test_mpe_other_classes():
    assert r76.mpe_factor("II", 5000 * 0.001, 0.001) == 0.5
    assert r76.mpe_factor("II", 20001 * 0.001, 0.001) == 1.5
    assert r76.mpe_factor("IIII", 200 * 1.0, 1.0) == 1.0


def test_error_rounding_correction():
    # I=10.005, dL=0.002, e=0.005 -> P = 10.005+0.0025-0.002 = 10.0055 ; E = 0.0055
    err, corrected = r76.error_of(10.0, 10.005, 0.005, 0.002)
    assert corrected and abs(err - 0.0055) < 1e-9
    err, corrected = r76.error_of(10.0, 10.005, 0.005, None)
    assert not corrected and abs(err - 0.005) < 1e-9


def test_instrument_validation():
    assert not [i for i in r76.validate_instrument(INST) if i["level"] == "error"]
    bad = dict(INST, capacity=1.0)                       # n = 200 < 500 for class III, e >= 5 g
    assert any("minimum" in i["message"] for i in r76.validate_instrument(bad))
    assert any(i["field"] == "verification_interval" for i in r76.validate_instrument(dict(INST, division=0.01, verification_interval=0.005)))
    assert any(i["field"] == "manufacturer" for i in r76.validate_instrument(dict(INST, manufacturer="")))


def test_environment_validation():
    assert any("outside" in i["message"] for i in r76.validate_environment({"temperature_c": 40, "humidity_pct": 50, "pressure_hpa": 1000}))
    assert not r76.validate_environment({"temperature_c": 22, "humidity_pct": 50, "pressure_hpa": 1000})


def test_weighing_pass_and_fail():
    rows = [{"load": l, "ind_up": l, "dl_up": 0.002, "ind_down": l, "dl_down": 0.002} for l in (0.1, 2.5, 10, 20, 30)]
    assert r76.evaluate_weighing(INST, rows)["pass"]
    rows[-1].update(ind_up=30.02)                         # error 0.0205 > 0.0075
    res = r76.evaluate_weighing(INST, rows)
    assert not res["pass"] and not res["rows"][-1]["up"]["pass"]


def test_weighing_requires_five_loads_and_flags_over_max():
    res = r76.evaluate_weighing(INST, [{"load": 10, "ind_up": 10}])
    assert not res["pass"] and any("At least 5" in i["message"] for i in res["issues"])
    res = r76.evaluate_weighing(INST, [{"load": 31, "ind_up": 31}] * 5)
    assert any("exceeds Max" in i["message"] for i in res["issues"])


def test_eccentricity_repeatability_discrimination():
    ecc = r76.evaluate_eccentricity(INST, 10, [{"position": "C", "indication": 10.0, "delta_l": 0.002}, {"position": "FL", "indication": 10.02}])
    assert not ecc["pass"]
    rep = r76.evaluate_repeatability(INST, [{"load": 30, "readings": [{"indication": 30.0}, {"indication": 30.005}, {"indication": 30.01}]}])
    assert rep["series"][0]["spread"] > 0.0074 and not rep["pass"]                 # 0.010 > 1.5e = 0.0075
    rep_ok = r76.evaluate_repeatability(INST, [{"load": 30, "readings": [{"indication": 30.0}] * 3}])
    assert rep_ok["pass"]
    assert r76.evaluate_discrimination(INST, [{"load": 10, "ind_before": 10.0, "ind_after": 10.005}])["pass"]
    assert not r76.evaluate_discrimination(INST, [{"load": 10, "ind_before": 10.0, "ind_after": 10.0}])["pass"]


def test_overall_states():
    assert r76.evaluate_all(INST, {}, {})["overall"] == "INCOMPLETE"
    assert r76.evaluate_all(dict(INST, manufacturer=""), {}, {})["overall"] == "INVALID"


def test_round_to_division():
    assert r76.round_to_division(25.162626, 0.005) == 25.165
