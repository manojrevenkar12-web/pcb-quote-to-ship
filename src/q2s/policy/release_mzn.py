"""The release-ordering problem solved through MiniZinc (models/release.mzn).

A second, independently written model of the problem `schedule.solve_cpsat` solves, so the two can be
checked against each other, and a way to try other back ends (Gecode, Chuffed, HiGHS, CP-SAT through
FlatZinc, or Gurobi where a licence exists) without rewriting the model. Needs the `minizinc` Python
package and the MiniZinc bundle on PATH; production ordering uses CP-SAT directly.
"""

from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path

from q2s.policy.schedule import FINISH_GAP, HANDOFF_MIN, TARDY_WEIGHT, ReleaseInstance

MODEL = Path(__file__).parent / "models" / "release.mzn"


def available() -> bool:
    if shutil.which("minizinc") is None:
        return False
    try:
        import minizinc  # noqa: F401
    except ImportError:
        return False
    return True


def solve_minizinc(
    inst: ReleaseInstance, solver: str = "chuffed", seconds: float = 30.0
) -> tuple[dict[str, int], int | None, bool]:
    """Placer start minute per order, the objective, and whether it is proven optimal."""
    import minizinc

    model = minizinc.Model(MODEL)
    mi = minizinc.Instance(minizinc.Solver.lookup(solver), model)
    jobs = inst.jobs
    mi["n"] = len(jobs)
    for field in ("d_pl", "k_pl", "d_op", "k_op", "due", "weight"):
        mi[field] = [getattr(j, field) for j in jobs]
    mi["placers"], mi["operators"] = inst.placers, inst.operators
    mi["wip_pl"], mi["wip_op"], mi["horizon"] = inst.wip_pl, inst.wip_op, inst.horizon
    mi["handoff"], mi["finish_gap"], mi["tardy_weight"] = HANDOFF_MIN, FINISH_GAP, TARDY_WEIGHT
    res = mi.solve(timeout=timedelta(seconds=seconds))
    if res.solution is None:
        return {}, None, False
    starts = {j.order_id: int(s) for j, s in zip(jobs, res["s_pl"])}
    return starts, int(res["objective"]), res.status == minizinc.Status.OPTIMAL_SOLUTION
