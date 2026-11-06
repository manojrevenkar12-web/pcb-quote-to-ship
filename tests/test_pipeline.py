"""The warehouse path feeds the planning the same numbers as the logs, and the Dagster job runs."""

from pathlib import Path

import pytest

from q2s.pipeline.landing import Landing
from q2s.pipeline.warehouse import dbt_build, frames_from_warehouse
from q2s.policy.calibrate import calibrate
from q2s.policy.stack import TodayStack
from q2s.world.sim import World

pytestmark = pytest.mark.pipeline


def test_calibration_from_the_warehouse_equals_calibration_from_the_logs(tmp_path: Path):
    w = World(seed=21)
    p = TodayStack(seed=21, explore=True)
    landing = Landing(tmp_path / "landing")
    for d in range(15):
        w.step(p.decide(w.observe(), w.store))
        landing.flush(w.store, d)
    out = dbt_build(tmp_path / "landing", tmp_path / "wh.duckdb")
    assert out["passed"] == out["nodes"]
    a = calibrate(frames_from_warehouse(tmp_path / "wh.duckdb"), 15)
    b = calibrate(w.frames(), 15)
    assert a.place_rates == pytest.approx(b.place_rates, rel=1e-6)
    assert a.manual_rework == pytest.approx(b.manual_rework, rel=1e-6)
    assert a.mtbf_minutes == pytest.approx(b.mtbf_minutes, rel=1e-6)


def test_the_morning_plan_runs_end_to_end(tmp_path: Path):
    from q2s.evaluation.pipeline_demo import run_pipeline

    out = run_pipeline(20, seed=3, root=tmp_path)
    assert out["success"] and out["dbt"]["passed"] == out["dbt"]["nodes"]
    assert out["quotes"] > 0 and out["released"] >= 0
