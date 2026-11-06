"""The morning outlook: where every order is, when it will ship, and whether the Grid is about to
run out of work or out of capacity. Built from the twin and recorded so it can be checked against
what actually happens."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from q2s.data.model import Observation
from q2s.policy.calibrate import Calibration
from q2s.policy.settings import parts_needed, settings
from q2s.policy.twin import EXPECTED_PRIORITY, TwinJob, ship_day, simulate


@dataclass
class Outlook:
    day: int
    ship_day_p50: dict[str, int]
    ship_day_p90: dict[str, int]
    panels_out: list[tuple[float, float, float]]  # per day: p10, p50, p90 of panels finished
    utilisation: dict[str, float]  # over the outlook horizon, median
    flags: list[str]
    spare: dict[str, list[float]]  # per day: minutes not needed by orders already won (p25)


def progress(frames: dict[str, pd.DataFrame]) -> dict[str, tuple[int, int, bool]]:
    """Per order in progress: panels through the manual cell, panels inspected but not through it,
    and whether any panel has been kitted (from the shop-floor logs)."""
    out: dict[str, tuple[int, int, bool]] = {}
    ops = frames.get("ops")
    man = frames.get("manual")
    if ops is None or not len(ops):
        return out
    kitted = set(ops.loc[ops.station == "kitting", "order_id"])
    aoi = ops[ops.station == "aoi"].groupby("order_id").size()
    done = man.groupby("order_id").size() if man is not None and len(man) else pd.Series(dtype=int)
    for oid in kitted:
        m = int(done.get(oid, 0))
        a = int(aoi.get(oid, 0))
        out[oid] = (m, max(0, a - m), True)
    return out


def expected_arrivals(obs: Observation, cal: Calibration) -> dict[str, int]:
    """When each open purchase is expected in: the standard days, or a backorder if it is late."""
    standard = 2
    median_back = int(np.median(cal.backorder_days)) if cal.backorder_days else 20
    out: dict[str, int] = {}
    for sku, _, day in obs.open_purchases:
        eta = day + standard if obs.day <= day + standard else day + median_back
        out[sku] = max(out.get(sku, 0), max(eta, obs.day + 1))
    return out


def build_jobs(obs: Observation, frames: dict[str, pd.DataFrame], cal: Calibration, plan: list[str]) -> list[TwinJob]:
    f = settings()["factory"]
    now = obs.day * f["day_minutes"]
    prog = progress(frames)
    rel = frames.get("releases")
    order_rank = {oid: i for i, oid in enumerate(rel["order_id"])} if rel is not None and len(rel) else {}
    jobs = []
    for o in obs.in_progress:
        m, a, k = prog.get(o.order_id, (0, 0, False))
        jobs.append(
            TwinJob(
                o.order_id,
                o.design,
                order_rank.get(o.order_id, 0),
                now,
                o.due_day * f["day_minutes"] + f["shipping_cutoff_minute"],
                m,
                a,
                k,
            )
        )
    base = len(order_rank) + 1
    arrivals = expected_arrivals(obs, cal)
    inv = dict(obs.inventory)
    by_id = {o.order_id: o for o in obs.backlog}
    rank = {oid: i for i, oid in enumerate(plan)}
    rest = [o.order_id for o in sorted(obs.backlog, key=lambda o: (o.due_day, o.order_id)) if o.order_id not in rank]
    for i, oid in enumerate([*plan, *rest]):
        o = by_id[oid]
        need = parts_needed(o.design, f["attrition"])
        ready = obs.day
        for s, q in need.items():
            if inv.get(s, 0) >= q:
                inv[s] -= q
            else:
                ready = max(ready, arrivals.get(s, obs.day + 2))
        jobs.append(
            TwinJob(
                oid,
                o.design,
                base + i,
                ready * f["day_minutes"],
                o.due_day * f["day_minutes"] + f["shipping_cutoff_minute"],
            )
        )
    return jobs


def expected_new_orders(obs: Observation, frames: dict[str, pd.DataFrame], days: int, seed: int) -> list[TwinJob]:
    """Orders not yet won will still reach the Grid within the outlook: resample recent orders at the
    recent daily rate, released when their parts would arrive (three working days after winning)."""
    q = frames.get("quotes")
    if q is None or not len(q):
        return []
    recent = q[(q["won"].astype(bool)) & (q["day"] >= obs.day - 10)]
    if not len(recent):
        return []
    from q2s.policy.quote import design_from_row

    rng = np.random.default_rng(seed)
    f = settings()["factory"]
    rate = len(recent) / 10
    out = []
    for k in range(days):
        n = rng.poisson(rate)
        for i, row in enumerate(recent.sample(n, replace=True, random_state=int(rng.integers(1 << 31))).itertuples()):
            start = obs.day + k + 3
            out.append(
                TwinJob(
                    f"expected-{k}-{i}",
                    design_from_row(row),
                    EXPECTED_PRIORITY + k * 1000 + i,
                    start * f["day_minutes"],
                    (start + 3) * f["day_minutes"],
                )
            )
    return out


def outlook(
    obs: Observation, frames: dict[str, pd.DataFrame], cal: Calibration, plan: list[str], reps: int, seed: int
) -> Outlook:
    s = settings()
    days = s["capacity"]["outlook_days"]
    now = obs.day * s["factory"]["day_minutes"]
    jobs = build_jobs(obs, frames, cal, plan) + expected_new_orders(obs, frames, days, seed)
    finish: dict[str, list[int]] = {j.order_id: [] for j in jobs}
    panels, util = [], {k: [] for k in ("placer", "manual", "printer", "kitting", "aoi", "oven")}
    near = {"placer": [], "manual": []}
    spare_runs = {"placer": [], "manual": []}
    f = s["factory"]
    per_day_cap = {
        "placer": f["machines"]["placer"] * f["day_minutes"],
        "manual": f["operators"] * (f["shift_end_minute"] - f["shift_start_minute"]),
    }
    for r in range(reps):
        res = simulate(cal, jobs, now, days, seed * 1000 + r)
        for j in jobs:
            c = res.completion.get(j.order_id)
            finish[j.order_id].append(ship_day(c) if c is not None else obs.day + days + 3)
        panels.append(res.panels_out)
        for k in util:
            util[k].append(res.busy[k] / res.capacity[k])
        for k in near:
            near[k].append(sum(res.busy_by_day[k][:2]) / (2 * per_day_cap[k]))
            spare_runs[k].append([max(0.0, per_day_cap[k] * 0.92 - b) for b in res.known_by_day[k]])
    p = np.array(panels)
    panels_out = [tuple(float(x) for x in np.percentile(p[:, d], [10, 50, 90])) for d in range(p.shape[1])]
    u = {k: round(float(np.median(v)), 3) for k, v in util.items()}
    nxt = {k: round(float(np.median(v)), 3) for k, v in near.items()}
    u.update({f"{k}_next2": v for k, v in nxt.items()})
    flags = []
    if max(nxt.values()) < s["capacity"]["starve_utilisation"]:
        flags.append("starve")
    if max(nxt.values()) >= s["capacity"]["overload_utilisation"] * 0.95:
        flags.append("overload")
    spare = {k: [float(x) for x in np.percentile(np.array(v), 25, axis=0)] for k, v in spare_runs.items()}
    return Outlook(
        obs.day,
        {k: int(np.percentile(v, 50)) for k, v in finish.items()},
        {k: int(np.ceil(np.percentile(v, 90))) for k, v in finish.items()},
        panels_out,
        u,
        flags,
        spare,
    )
