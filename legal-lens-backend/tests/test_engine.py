"""Pure-python tests of the OIML R 76 engine (no web framework needed)."""
from app.standards.engine import TESTS, compile_compliance, evaluate_observation
from app.standards.examples import DEMO_INSTRUMENT, DEMO_OBSERVATIONS
from app.standards.rules import Spec, get_standard, mpe, validate_instrument

STD = get_standard()
I = DEMO_INSTRUMENT
SPEC = Spec(I["accuracy_class"], I["max_capacity"], I["min_capacity"], I["division_d"], I["verification_interval_e"], "kg")


def test_mpe_class_iii_bands():
    assert mpe(STD, "III", 2.5, 0.005) == 0.0025      # 500 e  -> 0.5 e
    assert mpe(STD, "III", 2.51, 0.005) == 0.005      # >500 e -> 1 e
    assert mpe(STD, "III", 10, 0.005) == 0.005        # 2000 e -> 1 e
    assert mpe(STD, "III", 10.5, 0.005) == 0.0075     # >2000 e -> 1.5 e
    assert mpe(STD, "III", 10, 0.005, in_service=True) == 0.01


def test_instrument_validation():
    assert validate_instrument(STD, SPEC) == ([], [])
    errors, _ = validate_instrument(STD, Spec("III", 30, 0.1, 0.005, 0.01, "kg"))
    assert any("equal to d" in e for e in errors)
    errors, _ = validate_instrument(STD, Spec("III", 300000, 0.1, 0.005, 0.005, "kg"))
    assert any("exceeds the maximum" in e for e in errors)


def test_demo_observations_all_pass():
    passed = {}
    for test_type, data in DEMO_OBSERVATIONS.items():
        out = evaluate_observation(test_type, data, SPEC, STD)
        assert out["errors"] == [], out["errors"]
        passed[test_type] = out["result"]["passed"]
    assert all(passed.values())
    assert compile_compliance(STD, passed)["overall"] == "PASS"


def test_error_beyond_mpe_fails():
    pts = [{"load": l, "indication_up": l} for l in (1, 2, 3, 4, 30)]
    pts[-1]["indication_up"] = 30.02
    out = evaluate_observation("weighing", {"points": pts}, SPEC, STD)
    assert out["result"]["passed"] is False


def test_validation_errors():
    out = evaluate_observation("weighing", {"points": [{"load": 99, "indication_up": "x"}]}, SPEC, STD)
    assert out["errors"] and out["result"] is None
    out = evaluate_observation("eccentricity", {"load": 50, "positions": []}, SPEC, STD)
    assert any("exceeds Max" in e for e in out["errors"])


def test_compliance_logic():
    assert compile_compliance(STD, {"weighing": True})["overall"] == "INCOMPLETE"
    assert compile_compliance(STD, {"weighing": False})["overall"] == "FAIL"
    assert set(STD["required_tests"]) <= set(TESTS)
