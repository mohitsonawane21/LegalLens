"""Sample observations for NAWI-0018 (Essae ED-H, 30 kg, d = e = 0.005 kg, class III).
Used by the demo seeder and exposed through GET /api/tests/catalog as ready-made request bodies."""

DEMO_INSTRUMENT = {
    "instrument_id": "NAWI-0018", "instrument_type": "Weighing Scale", "manufacturer": "Essae", "model": "ED-H",
    "serial_number": "ES-45872", "accuracy_class": "III", "max_capacity": 30, "min_capacity": 0.1,
    "division_d": 0.005, "verification_interval_e": 0.005, "unit": "kg", "location": "SIEM Lab - Testing Section",
    "manufacturer_details": {"address": "Essae Digitronics, Bengaluru", "model_approval_no": "IND/09/XX/000"},
    "technical_parameters": {"power_supply": "230 V AC, 50 Hz", "display": "LCD, 6 digits"},
}

DEMO_ENVIRONMENT = {"temperature_start_c": 23.4, "temperature_end_c": 23.8, "humidity_pct": 52, "pressure_hpa": 1008,
                    "notes": "Air-conditioned laboratory, no draught"}

DEMO_OBSERVATIONS = {
    "weighing": {"zero_error": 0, "points": [
        {"load": 0.25, "indication_up": 0.25, "indication_down": 0.25},
        {"load": 2.5, "indication_up": 2.5, "extra_load_up": 0.002, "indication_down": 2.5, "extra_load_down": 0.002},
        {"load": 10, "indication_up": 10.0, "extra_load_up": 0.0015, "indication_down": 10.005, "extra_load_down": 0.003},
        {"load": 20, "indication_up": 20.005, "extra_load_up": 0.002, "indication_down": 20.005, "extra_load_down": 0.001},
        {"load": 30, "indication_up": 30.005, "extra_load_up": 0.0035, "indication_down": 30.005, "extra_load_down": 0.0015},
    ]},
    "eccentricity": {"load": 10, "zero_error": 0, "positions": [
        {"position": "Centre", "indication": 10.0}, {"position": "Front-left", "indication": 10.0},
        {"position": "Front-right", "indication": 10.005}, {"position": "Rear-left", "indication": 10.0},
        {"position": "Rear-right", "indication": 10.0},
    ]},
    "repeatability": {"series": [
        {"load": 15, "readings": [15.0, 15.005, 15.0, 15.0, 15.005, 15.0, 15.0, 15.005, 15.0, 15.0]},
        {"load": 30, "readings": [30.0, 30.005, 30.005, 30.0, 30.005, 30.0, 30.0, 30.005, 30.005, 30.0]},
    ]},
    "discrimination": {"points": [
        {"load": 10, "added_weight": 0.007, "indication_before": 10.0, "indication_after": 10.005},
        {"load": 30, "added_weight": 0.007, "indication_before": 30.0, "indication_after": 30.005},
    ]},
    "temperature_zero": {"points": [
        {"temperature_c": 20, "zero_indication": 0.0}, {"temperature_c": 40, "zero_indication": 0.005},
        {"temperature_c": 20, "zero_indication": 0.0}, {"temperature_c": 5, "zero_indication": 0.005},
        {"temperature_c": 20, "zero_indication": 0.0},
    ]},
}
