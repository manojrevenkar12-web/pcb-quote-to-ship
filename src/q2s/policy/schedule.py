"""Which orders go onto the Grid first.

Every order whose parts are on hand is released each morning; the decision is the order of release,
which the Grid uses as each panel's priority at every machine. Four ways to decide it: first come
first served (today), earliest due date, the ATCS dispatch rule, and a CP-SAT model that minimises
value-weighted lateness over the next few days. Simple rules stay unless the solver earns its place.
"""

from __future__ import annotations

import math

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


def cpsat(
    obs: Observation, cal: Calibration, wip_minutes: dict[str, float] | None = None, seconds: float | None = None
) -> list[str]:
    """Value-weighted tardiness on the two bottleneck groups (placers, manual cell), then value-weighted
    completion time as a tie-breaker, warm-started from ATCS. Durations are the calibrated expected
    minutes; work already on the floor occupies capacity first."""
    f = settings()["factory"]
    jobs = releasable(obs)
    if len(jobs) <= 1:
        return [o.order_id for o in jobs]
    now = obs.day * f["day_minutes"]
    n_pl, n_op = f["machines"]["placer"], f["operators"]
    shift_factor = f["day_minutes"] / (f["shift_end_minute"] - f["shift_start_minute"])
    wip = wip_minutes or {}
    m = cp_model.CpModel()
    data = {}
    horizon = (
        int(
            sum(cal.job_minutes(o.design)["placer"] + cal.job_minutes(o.design)["manual"] * shift_factor for o in jobs)
            + wip.get("placer", 0)
            + wip.get("manual", 0) * shift_factor
        )
        + 2000
    )
    pl_iv, pl_dem, man_iv, man_dem = [], [], [], []
    for name, cap, iv, dem in (("placer", n_pl, pl_iv, pl_dem), ("manual", n_op, man_iv, man_dem)):
        w = int(wip.get(name, 0) * (shift_factor if name == "manual" else 1) / cap)
        if w > 0:
            iv.append(m.NewIntervalVar(0, w, w, f"wip-{name}"))
            dem.append(cap)
    obj = []
    for o in jobs:
        jm = cal.job_minutes(o.design)
        k_pl = max(1, min(o.design.panels, n_pl))
        k_op = max(1, min(o.design.panels, n_op))
        d_pl = max(1, int(math.ceil(jm["placer"] / k_pl)))
        d_op = max(1, int(math.ceil(jm["manual"] * shift_factor / k_op)))
        s_pl = m.NewIntVar(0, horizon, f"s_pl_{o.order_id}")
        e_pl = m.NewIntVar(0, horizon, f"e_pl_{o.order_id}")
        s_op = m.NewIntVar(0, horizon, f"s_op_{o.order_id}")
        e_op = m.NewIntVar(0, horizon + d_op, f"e_op_{o.order_id}")
        pl_iv.append(m.NewIntervalVar(s_pl, d_pl, e_pl, f"pl_{o.order_id}"))
        pl_dem.append(k_pl)
        man_iv.append(m.NewIntervalVar(s_op, d_op, e_op, f"op_{o.order_id}"))
        man_dem.append(k_op)
        m.Add(s_op >= s_pl + d_pl // 2 + 20)  # panels reach the manual cell as they finish
        m.Add(e_op >= e_pl + 30)
        due = int(_due_minutes(o, now))
        tard = m.NewIntVar(0, horizon + d_op, f"t_{o.order_id}")
        m.Add(tard >= e_op - due)
        weight = max(1, int(round(o.price / 50)))
        obj.append(1000 * weight * tard)
        obj.append(weight * e_op)  # then: finish valuable work early (weighted flow time)
        data[o.order_id] = s_pl
    m.AddCumulative(pl_iv, pl_dem, n_pl)
    m.AddCumulative(man_iv, man_dem, n_op)
    m.Minimize(sum(obj))
    order = atcs(obs, cal)  # warm start: ATCS sequence as a hint
    t = 0
    for oid in order:
        m.AddHint(data[oid], t)
        t += 1
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = seconds or settings()["schedule"]["solver_seconds"]
    solver.parameters.num_workers = 2
    solver.parameters.random_seed = 0
    status = solver.Solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return order
    by_id = {o.order_id: o for o in jobs}
    # jobs the solver starts together go to the Grid earliest-due first, then by value
    return sorted(
        data, key=lambda oid: (solver.Value(data[oid]), by_id[oid].due_day, -by_id[oid].price, order.index(oid))
    )


RULES = {"fifo": fifo, "edd": edd, "atcs": atcs, "cpsat": cpsat}
