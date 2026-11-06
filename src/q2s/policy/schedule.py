"""Which orders go onto the Grid first.

Every order whose parts are on hand is released each morning; the decision is the order of release,
which the Grid uses as each panel's priority at every machine. Four ways to decide it: first come
first served (today), earliest due date, the ATCS dispatch rule, and a CP-SAT model that minimises
value-weighted lateness over the next few days. Simple rules stay unless the solver earns its place.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from ortools.sat.python import cp_model

from q2s.data.model import Observation, Order
from q2s.policy.calibrate import Calibration
from q2s.policy.settings import parts_needed, settings


def releasable(obs: Observation) -> list[Order]:
    inv = dict(obs.inventory)
    out = []
    for o in sorted(obs.backlog, key=lambda o: (o.quote_day, o.order_id)):
        need = parts_needed(o.design, settings()["factory"]["attrition"])
        if all(inv.get(s, 0) >= q for s, q in need.items()):
            for s, q in need.items():
                inv[s] -= q
            out.append(o)
    return out


def _due_minutes(o: Order, now: float) -> float:
    f = settings()["factory"]
    return o.due_day * f["day_minutes"] + f["shipping_cutoff_minute"] - now


def fifo(obs: Observation, cal: Calibration | None = None) -> list[str]:
    return [o.order_id for o in releasable(obs)]


def edd(obs: Observation, cal: Calibration | None = None) -> list[str]:
    return [o.order_id for o in sorted(releasable(obs), key=lambda o: (o.due_day, o.quote_day, o.order_id))]


def _load(o: Order, cal: Calibration) -> float:
    """Bottleneck minutes per resource unit: the larger of placer and manual share."""
    f = settings()["factory"]
    m = cal.job_minutes(o.design)
    return max(m["placer"] / f["machines"]["placer"], m["manual"] / f["operators"] * 1.5)


def atcs(obs: Observation, cal: Calibration) -> list[str]:
    """Apparent tardiness cost: weight per minute of work, discounted by slack."""
    jobs = releasable(obs)
    now = obs.day * settings()["factory"]["day_minutes"]
    k1 = settings()["schedule"]["atcs_k1"]
    p = {o.order_id: max(_load(o, cal), 1.0) for o in jobs}
    pbar = sum(p.values()) / max(len(p), 1)
    t, out, left = 0.0, [], list(jobs)
    while left:

        def index(o: Order, t: float = t) -> float:
            slack = max(_due_minutes(o, now) - p[o.order_id] - t, 0.0)
            return (o.price / p[o.order_id]) * math.exp(-slack / (k1 * pbar))

        best = max(left, key=lambda o: (index(o), -o.due_day))
        out.append(best.order_id)
        t += p[best.order_id]
        left.remove(best)
    return out


@dataclass(frozen=True)
class ReleaseJob:
    order_id: str
    d_pl: int  # minutes on the placers, spread over k_pl of them
    k_pl: int
    d_op: int  # minutes in the manual cell (stretched to the 24-hour clock), over k_op operators
    k_op: int
    due: int  # minutes from now to the shipping cut-off of the promised day
    weight: int


@dataclass(frozen=True)
class ReleaseInstance:
    """The release-ordering problem as plain numbers, so more than one solver can read it."""

    jobs: tuple[ReleaseJob, ...]
    placers: int
    operators: int
    wip_pl: int  # minutes every placer is busy with work already on the floor
    wip_op: int
    horizon: int

    def to_dict(self) -> dict:
        return {**asdict(self), "jobs": [asdict(j) for j in self.jobs]}


def release_instance(
    obs: Observation, cal: Calibration, wip_minutes: dict[str, float] | None = None
) -> ReleaseInstance:
    """Durations are the calibrated expected minutes; work already on the floor occupies capacity first."""
    f = settings()["factory"]
    jobs = releasable(obs)
    now = obs.day * f["day_minutes"]
    n_pl, n_op = f["machines"]["placer"], f["operators"]
    shift_factor = f["day_minutes"] / (f["shift_end_minute"] - f["shift_start_minute"])
    wip = wip_minutes or {}
    horizon = (
        int(
            sum(cal.job_minutes(o.design)["placer"] + cal.job_minutes(o.design)["manual"] * shift_factor for o in jobs)
            + wip.get("placer", 0)
            + wip.get("manual", 0) * shift_factor
        )
        + 2000
    )
    out = []
    for o in jobs:
        jm = cal.job_minutes(o.design)
        k_pl = max(1, min(o.design.panels, n_pl))
        k_op = max(1, min(o.design.panels, n_op))
        out.append(
            ReleaseJob(
                order_id=o.order_id,
                d_pl=max(1, int(math.ceil(jm["placer"] / k_pl))),
                k_pl=k_pl,
                d_op=max(1, int(math.ceil(jm["manual"] * shift_factor / k_op))),
                k_op=k_op,
                due=int(_due_minutes(o, now)),
                weight=max(1, int(round(o.price / 50))),
            )
        )
    return ReleaseInstance(
        jobs=tuple(out),
        placers=n_pl,
        operators=n_op,
        wip_pl=int(wip.get("placer", 0) / n_pl),
        wip_op=int(wip.get("manual", 0) * shift_factor / n_op),
        horizon=horizon,
    )


# Panels reach the manual cell as they come off the placers; the order is done 30 minutes after its
# last placement at the earliest. Both models (CP-SAT here, MiniZinc in release.mzn) use these.
HANDOFF_MIN = 20
FINISH_GAP = 30
TARDY_WEIGHT = 1000


def solve_cpsat(
    inst: ReleaseInstance, hint: list[str] | None = None, seconds: float = 2.0, workers: int = 2
) -> tuple[dict[str, int], int | None, bool]:
    """Placer start minute per order, the objective, and whether it is proven optimal.

    Objective: value-weighted tardiness (times TARDY_WEIGHT) plus value-weighted completion time."""
    m = cp_model.CpModel()
    pl_iv, pl_dem, man_iv, man_dem = [], [], [], []
    for name, cap, iv, dem, w in (
        ("placer", inst.placers, pl_iv, pl_dem, inst.wip_pl),
        ("manual", inst.operators, man_iv, man_dem, inst.wip_op),
    ):
        if w > 0:
            iv.append(m.NewIntervalVar(0, w, w, f"wip-{name}"))
            dem.append(cap)
    obj, data = [], {}
    horizon = inst.horizon
    for j in inst.jobs:
        s_pl = m.NewIntVar(0, horizon, f"s_pl_{j.order_id}")
        e_pl = m.NewIntVar(0, horizon, f"e_pl_{j.order_id}")
        s_op = m.NewIntVar(0, horizon, f"s_op_{j.order_id}")
        e_op = m.NewIntVar(0, horizon + j.d_op, f"e_op_{j.order_id}")
        pl_iv.append(m.NewIntervalVar(s_pl, j.d_pl, e_pl, f"pl_{j.order_id}"))
        pl_dem.append(j.k_pl)
        man_iv.append(m.NewIntervalVar(s_op, j.d_op, e_op, f"op_{j.order_id}"))
        man_dem.append(j.k_op)
        m.Add(s_op >= s_pl + j.d_pl // 2 + HANDOFF_MIN)
        m.Add(e_op >= e_pl + FINISH_GAP)
        # An order already overdue is late by more than the horizon; its tardiness must still fit.
        tard = m.NewIntVar(0, horizon + j.d_op + max(0, -j.due), f"t_{j.order_id}")
        m.Add(tard >= e_op - j.due)
        obj.append(TARDY_WEIGHT * j.weight * tard)
        obj.append(j.weight * e_op)  # then: finish valuable work early (weighted flow time)
        data[j.order_id] = s_pl
    m.AddCumulative(pl_iv, pl_dem, inst.placers)
    m.AddCumulative(man_iv, man_dem, inst.operators)
    m.Minimize(sum(obj))
    for t, oid in enumerate(hint or []):
        m.AddHint(data[oid], t)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = seconds
    solver.parameters.num_workers = workers
    solver.parameters.random_seed = 0
    status = solver.Solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {}, None, False
    return {k: solver.Value(v) for k, v in data.items()}, int(solver.ObjectiveValue()), status == cp_model.OPTIMAL


def cpsat(
    obs: Observation,
    cal: Calibration,
    wip_minutes: dict[str, float] | None = None,
    seconds: float | None = None,
    log: list[dict] | None = None,
) -> list[str]:
    """Value-weighted tardiness on the two bottleneck groups (placers, manual cell), then value-weighted
    completion time as a tie-breaker, warm-started from ATCS. Each morning's outcome (proven optimal,
    feasible, or fallen back to ATCS) goes to `log` when one is given."""
    jobs = releasable(obs)
    if len(jobs) <= 1:
        return [o.order_id for o in jobs]
    inst = release_instance(obs, cal, wip_minutes)
    order = atcs(obs, cal)  # warm start: ATCS sequence as a hint
    starts, _, optimal = solve_cpsat(inst, order, seconds or settings()["schedule"]["solver_seconds"])
    if log is not None:
        status = "optimal" if optimal else ("feasible" if starts else "fallback")
        log.append({"day": obs.day, "jobs": len(jobs), "status": status})
    if not starts:
        return order
    by_id = {o.order_id: o for o in jobs}
    # jobs the solver starts together go to the Grid earliest-due first, then by value
    return sorted(starts, key=lambda oid: (starts[oid], by_id[oid].due_day, -by_id[oid].price, order.index(oid)))


RULES = {"fifo": fifo, "edd": edd, "atcs": atcs, "cpsat": cpsat}
