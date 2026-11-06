"""The report builder renders from results.json alone and prefers the pinned held-out first run."""

import json
from pathlib import Path

from q2s.evaluation.report import build

ROW = {
    "contribution_per_day": 1000.0,
    "revenue": 50000.0,
    "win_rate": 0.4,
    "on_time": 0.8,
    "late_days_mean": 0.5,
    "lead_days_median": 6.0,
    "lead_days_p90": 10.0,
    "promise_days_median": 6.0,
    "parts_wait_days_mean": 1.0,
    "late_credits": 2000.0,
    "holding": 300.0,
    "boards_per_day": 400.0,
}
CI = {"mean": 1.0, "low": 0.5, "high": 1.5, "per_seed": [1.0]}


def _results() -> dict:
    rows = [
        {**ROW, "stack": "today", "seed": 1},
        {
            **ROW,
            "stack": "system",
            "seed": 1,
            "contribution_per_day": 1300.0,
            "on_time": 0.9,
            "forecasts": {
                "panels": {"1": {"n": 30, "mape": 0.1, "bias": 0.02, "coverage_p10_p90": 0.7}},
                "panels_conformal": {"1": 0.81},
                "ship_day": {"n": 500, "within_1_day": 0.85, "p90_covers": 0.9},
            },
        },
    ]
    scen = {"surge": {"today": ROW, "system": {**ROW, "contribution_per_day": 1200.0}}}
    return {
        "seeds": [1],
        "stacks": ["today", "system"],
        "rows": rows,
        "seconds": 60,
        "comparisons": {
            "system": {
                m: CI for m in ("contribution_per_day", "on_time", "late_days_mean", "win_rate", "lead_days_p90")
            }
        },
        "machines": {
            "rank_correlation": 0.8,
            "truth_best": "placer",
            "twin_best": "placer",
            "options": {
                "none": {},
                "placer": {
                    "truth_saving": 900,
                    "twin_saving": 700,
                    "truth_flow_change": -0.4,
                    "twin_flow_change": -0.3,
                },
            },
        },
        "scenarios_dev": scen,
        "scenarios_heldout": {"v1": {"status": "blind", "latest": {"later": {"today": ROW, "system": ROW}}}},
    }


def test_report_uses_first_run_and_renders(tmp_path: Path) -> None:
    (tmp_path / "results.json").write_text(json.dumps(_results()))
    first = tmp_path / "heldout" / "v1"
    first.mkdir(parents=True)
    (first / "first_run.json").write_text(json.dumps({"results": scen_first()}))
    build(tmp_path)
    md = (tmp_path / "REPORT.md").read_text()
    page = (tmp_path / "dashboard.html").read_text()
    assert "pinned-scenario" in md and "later" not in md
    assert "$1,300" in md and "Do not edit by hand" in md
    assert page.startswith("<!doctype html>") and "<svg" in page and "pinned-scenario" in page


def scen_first() -> dict:
    return {"pinned-scenario": {"today": ROW, "system": {**ROW, "on_time": 0.95}}}
