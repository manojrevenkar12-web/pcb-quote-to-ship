"""The calibration recovers what the truth hides, from the logs alone."""

from q2s.world.params import load_world


def test_placer_rates_and_fine_pitch(cal):
    truth = {m["name"]: m["rate"] for m in load_world()["grid"]["placer"]["machines"]}
    for name, rate in truth.items():
        assert abs(cal.place_rates[name] - rate) / rate < 0.05
    assert abs(cal.place_fine_pitch - load_world()["grid"]["placer"]["fine_pitch_slowdown"]) < 0.1


def test_manual_cell_and_defects(cal):
    m = load_world()["manual"]
    assert abs(cal.manual_qc_per_board - m["qc_minutes_per_board"]) < 0.1
    assert abs(cal.manual_tht_per_part - m["tht_minutes_per_part"]) < 0.05
    assert abs(cal.manual_rework - m["rework_minutes"]) < 1.0


def test_movers_slow_down_when_the_floor_is_busy(cal):
    t = cal.transport_by_load
    assert t[-1] > t[0] * 1.5


def test_failures_within_a_factor_of_the_truth(cal):
    g = load_world()["grid"]
    assert 0.6 < cal.mtbf_minutes["placer"] / (g["placer"]["mtbf_hours"] * 60) < 1.6


def test_supplier_reliability_is_learned(cal):
    assert 0.95 < cal.supplier_in_stock_prior <= 1.0
    assert len(cal.backorder_days) > 0
