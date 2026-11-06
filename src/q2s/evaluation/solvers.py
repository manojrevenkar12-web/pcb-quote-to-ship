"""How far exact solving goes: the release problem by size, CP-SAT against MiniZinc back ends.

Random instances shaped like a morning on the Grid (placer and manual minutes, panels spread over
machines, due times within a day or two, a value weight). For each size and solver: how often the
optimum was proven inside the time limit, the median time to prove it, and whether every proven
optimum agrees with CP-SAT's. Writes reports/solvers.json.
"""

from __future__ import annotations

import json
import random
import statistics
import time
from pathlib import Path
from typing import Any

from q2s.policy.release_mzn import available, solve_minizinc
from q2s.policy.schedule import ReleaseInstance, ReleaseJob, solve_cpsat

ROOT = Path(__file__).resolve().parents[3]
SIZES = (5, 8, 12, 20)
SOLVERS = ("cp-sat", "cbc", "gecode", "chuffed", "highs")


def instance(seed: int, n: int) -> ReleaseInstance:
    r = random.Random(seed)
    jobs = tuple(
        ReleaseJob(
            order_id=f"o{i}",
            d_pl=r.randint(20, 300),
            k_pl=r.randint(1, 4),
            d_op=r.randint(30, 400),
            k_op=r.randint(1, 4),
            due=r.randint(200, 2800),
            weight=r.randint(1, 40),
        )
        for i in range(n)
    )
    horizon = sum(j.d_pl + j.d_op for j in jobs) + 2000
    return ReleaseInstance(jobs, 4, 4, r.choice([0, 60]), r.choice([0, 90]), horizon)


def study(per_size: int = 4, seconds: float = 15.0) -> dict[str, Any]:
    solvers = SOLVERS if available() else ("cp-sat",)
    out: dict[str, Any] = {"seconds_limit": seconds, "per_size": per_size, "sizes": {}}
    for n in SIZES:
        rows: dict[str, list[dict[str, Any]]] = {s: [] for s in solvers}
        for k in range(per_size):
            inst = instance(1000 * n + k, n)
            ref = None
            for s in solvers:
                t = time.time()
                if s == "cp-sat":
                    _, obj, opt = solve_cpsat(inst, seconds=seconds, workers=1)
                    ref = (obj, opt)
                else:
                    try:
                        _, obj, opt = solve_minizinc(inst, s, seconds=seconds)
                    except Exception as exc:  # noqa: BLE001 - a back end that cannot take the instance is a result
                        rows[s].append(
                            {
                                "objective": None,
                                "optimal": False,
                                "seconds": 0.0,
                                "ref": ref,
                                "error": str(exc).splitlines()[0][:160],
                            }
                        )
                        continue
                rows[s].append({"objective": obj, "optimal": opt, "seconds": round(time.time() - t, 2), "ref": ref})
        size: dict[str, Any] = {}
        for s, rs in rows.items():
            proven = [r for r in rs if r["optimal"]]
            both = [r for r in proven if r["ref"] and r["ref"][1]]
            errors = [r["error"] for r in rs if r.get("error")]
            claimed_infeasible = sum(
                r["objective"] is None and not r.get("error") and r["seconds"] < seconds * 0.9 for r in rs
            )
            size[s] = {
                "proven": len(proven),
                "median_seconds": round(statistics.median(r["seconds"] for r in proven), 2) if proven else None,
                "agree_with_cpsat": sum(r["objective"] == r["ref"][0] for r in both),
                "compared": len(both),
                "ended_without_solution_early": claimed_infeasible,
                "errors": len(errors),
                "first_error": errors[0] if errors else None,
            }
        out["sizes"][str(n)] = size
    return out


def run() -> dict[str, Any]:
    res = study()
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "solvers.json").write_text(json.dumps(res, indent=2))
    return res
