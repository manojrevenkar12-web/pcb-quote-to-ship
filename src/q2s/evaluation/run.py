"""`q2s evaluate`: every stack on every seed, the machine-value study, and the scenario sets.

Writes reports/results.json (the numbers), reports/heldout/<set>/first_run.json the first time a
held-out set runs (never rewritten afterwards), and the figures the report and dashboard use.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from q2s.evaluation.experiment import HISTORY_DAYS, forecast_checks, run_stack, score

ROOT = Path(__file__).resolve().parents[3]
REPORTS = ROOT / "reports"
MAIN_STACKS = [
    "today",
    "system",
    "system, FIFO release",
    "system, ATCS release",
    "system, today's quotes",
    "system, buy per order",
]
SEEDS = [11, 23, 37]


def _one(args: tuple[str, int, dict[str, Any] | None]) -> dict[str, Any]:
    stack, seed, scenario = args
    t = time.time()
    run = run_stack(stack, seed, scenario=scenario, outlook=(stack == "system"))
    out = score(run)
    out["seconds"] = round(time.time() - t)
    if stack == "system":
        out["forecasts"] = forecast_checks(run)
        out["ledger"] = {
            "calibrations": run.ledger.calibrations[-6:],
            "flags": [o for o in run.ledger.outlooks if o["flags"]][-5:],
        }
    return out


def paired(rows: list[dict[str, Any]], a: str, b: str, metric: str) -> dict[str, float]:
    """Difference b - a per seed, with a bootstrap interval over seeds."""
    by = {(r["stack"], r["seed"]): r[metric] for r in rows}
    seeds = sorted({r["seed"] for r in rows if (a, r["seed"]) in by and (b, r["seed"]) in by})
    d = np.array([by[(b, s)] - by[(a, s)] for s in seeds], float)
    rng = np.random.default_rng(0)
    boots = [rng.choice(d, len(d)).mean() for _ in range(2000)] if len(d) > 1 else [d.mean()]
    return {
        "mean": round(float(d.mean()), 3),
        "low": round(float(np.quantile(boots, 0.05)), 3),
        "high": round(float(np.quantile(boots, 0.95)), 3),
        "per_seed": [round(float(x), 3) for x in d],
    }


def scenario_to_world(spec: dict[str, Any]) -> dict[str, Any]:
    """Scenario files count days from the first test day; the world counts from day 0."""
    out: dict[str, Any] = {}
    for key in ("demand", "supplier_shocks"):
        out[key] = [
            {**x, "from_day": x["from_day"] + HISTORY_DAYS, "to_day": x["to_day"] + HISTORY_DAYS}
            for x in spec.get(key, [])
        ]
    out["machine_outages"] = [{**x, "day": x["day"] + HISTORY_DAYS} for x in spec.get("machine_outages", [])]
    return out


def run_scenarios(folder: Path, workers: int) -> dict[str, Any]:
    specs = sorted(folder.glob("*.yaml"))
    jobs = []
    for path in specs:
        spec = yaml.safe_load(path.read_text())
        for stack in ("today", "system"):
            jobs.append((path.stem, stack, spec.get("seed", 101), scenario_to_world(spec)))
    with ProcessPoolExecutor(workers) as pool:
        results = list(pool.map(_one, [(st, seed, sc) for _, st, seed, sc in jobs]))
    out: dict[str, Any] = {}
    for (name, stack, _, _), r in zip(jobs, results):
        r.pop("ledger", None)
        out.setdefault(name, {})[stack] = r
    return out


def evaluate(workers: int = 2, quick: bool = False) -> dict[str, Any]:
    from q2s.evaluation.machines import study

    REPORTS.mkdir(exist_ok=True)
    seeds = SEEDS[:1] if quick else SEEDS
    stacks = ["today", "system"] if quick else MAIN_STACKS
    t0 = time.time()
    with ProcessPoolExecutor(workers) as pool:
        rows = list(pool.map(_one, [(st, s, None) for s in seeds for st in stacks]))
    results: dict[str, Any] = {"seeds": seeds, "stacks": stacks, "rows": rows, "comparisons": {}}
    for st in stacks:
        if st == "today":
            continue
        results["comparisons"][st] = {
            m: paired(rows, "today", st, m)
            for m in (
                "contribution_per_day",
                "on_time",
                "late_days_mean",
                "win_rate",
                "lead_days_p90",
                "revenue",
                "late_credits",
                "holding",
            )
        }
    if not quick:
        for st in stacks:
            if st.startswith("system,"):
                results["comparisons"][f"system vs {st}"] = {
                    m: paired(rows, st, "system", m) for m in ("contribution_per_day", "on_time", "late_days_mean")
                }
        results["machines"] = study(SEEDS[0])
        dev = run_scenarios(ROOT / "scenarios" / "dev", workers)
        results["scenarios_dev"] = dev
        heldout_root = ROOT / "scenarios" / "heldout"
        for folder in sorted(p for p in heldout_root.iterdir() if p.is_dir()) if heldout_root.exists() else []:
            first = REPORTS / "heldout" / folder.name / "first_run.json"
            status = (folder / "STATUS").read_text().strip() if (folder / "STATUS").exists() else "blind"
            res = run_scenarios(folder, workers)
            results.setdefault("scenarios_heldout", {})[folder.name] = {"status": status, "latest": res}
            if not first.exists():
                first.parent.mkdir(parents=True, exist_ok=True)
                commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                        text=True).stdout.strip()
                first.write_text(json.dumps({"set": folder.name, "commit": commit, "results": res}, indent=2))
                digest = hashlib.sha256(first.read_bytes()).hexdigest()
                (first.parent / "first_run.sha256").write_text(f"{digest}  first_run.json\n")
    results["seconds"] = round(time.time() - t0)
    (REPORTS / "results.json").write_text(json.dumps(results, indent=2, default=str))
    return results
