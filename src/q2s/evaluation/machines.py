"""What is the next machine worth? The twin's answer against the truth's.

The same month is replayed with one more placer, printer, oven, inspection machine, kitting robot,
eight more movers or one more operator per shift. Truth: the simulated factory runs today's process
with the extra resource. Twin: the calibrated twin replays the orders the factory actually released,
at the minutes they were released, with the same extra resource. Both report the change in mean
release-to-pack time and in late credits. If the twin ranks the options the way the truth does,
it can be trusted to say which machine to buy next.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from q2s.evaluation.experiment import HISTORY_DAYS, run_stack
from q2s.policy.calibrate import calibrate
from q2s.policy.settings import settings
from q2s.policy.stack import CAL_TABLES
from q2s.policy.twin import TwinJob, ship_day, simulate

OPTIONS: dict[str, dict[str, int]] = {
    "none": {},
    "+1 placer": {"placer": 1},
    "+1 printer": {"printer": 1},
    "+1 oven": {"oven": 1},
    "+1 inspection": {"aoi": 1},
    "+1 kitting robot": {"kitting": 1},
    "+8 movers": {"movers": 8},
    "+1 operator per shift": {"operators": 1},
}

TEST = 30


def _truth(seed: int, extra: dict[str, int]) -> dict[str, float]:
    return _truth_from(run_stack("today", seed, test=TEST, drain=8, machines_extra=extra))


def study(seed: int, reps: int = 3) -> dict[str, Any]:
    base = run_stack("today", seed, test=TEST, drain=8)
    w = base.world
    frames = w.frames()
    hist = dict(w.frames(CAL_TABLES))
    for k in ("ops", "manual", "downtime"):
        col = {"ops": "start", "manual": "start", "downtime": "failed"}[k]
        hist[k] = hist[k][hist[k][col] < HISTORY_DAYS * w.cfg["day_minutes"]]
    for k, col in (("quotes", "day"), ("shipments", "shipped_day"), ("purchases", "day"), ("releases", "day")):
        hist[k] = hist[k][hist[k][col] < HISTORY_DAYS]
    hist["receipts"] = hist["receipts"][hist["receipts"]["day"] < HISTORY_DAYS]
    cal = calibrate(hist, HISTORY_DAYS)
    day = w.cfg["day_minutes"]
    start = base.test_start * day
    rel = frames["releases"]
    jobs, prices = [], {}
    for i, r in enumerate(rel.itertuples()):
        o = w.orders[r.order_id]
        if o.completed_minute is not None and o.completed_minute <= start:
            continue  # finished before the replay starts
        jobs.append(
            TwinJob(
                o.order_id, o.design, i, max(float(r.minute), start), o.due_day * day + w.cfg["shipping_cutoff_minute"]
            )
        )
        if base.test_start <= o.quote_day < base.test_end:
            prices[o.order_id] = (o.price, o.due_day, r.day)
    c = settings()["costs"]
    horizon = (base.test_end - base.test_start) + 20
    out: dict[str, Any] = {"options": {}, "calibrated_on_days": HISTORY_DAYS, "orders": len(jobs)}
    for name, extra in OPTIONS.items():
        truth = _truth(seed, extra) if extra else _truth_from(base)
        flows, credits = [], []
        for r in range(reps):
            res = simulate(cal, jobs, start, horizon, seed * 100 + r, extra)
            fl, cr = [], 0.0
            for oid, (price, due, rday) in prices.items():
                done = res.completion.get(oid)
                if done is None:
                    continue
                fl.append(done / day - rday)
                late = max(0, ship_day(done) - due)
                cr += price * min(c["late_credit_cap"], c["late_credit_per_day"] * late)
            flows.append(float(np.mean(fl)))
            credits.append(cr)
        out["options"][name] = {
            "truth": truth,
            "twin": {"flow_days": float(np.mean(flows)), "late_credits": float(np.mean(credits))},
        }
    base_t, base_w = out["options"]["none"]["truth"], out["options"]["none"]["twin"]
    for name, row in out["options"].items():
        row["truth_saving"] = round(base_t["late_credits"] - row["truth"]["late_credits"], 0)
        row["twin_saving"] = round(base_w["late_credits"] - row["twin"]["late_credits"], 0)
        row["truth_flow_change"] = round(row["truth"]["flow_days"] - base_t["flow_days"], 3)
        row["twin_flow_change"] = round(row["twin"]["flow_days"] - base_w["flow_days"], 3)
    names = [n for n in OPTIONS if n != "none"]
    t_rank = np.argsort(np.argsort([-out["options"][n]["truth_saving"] for n in names]))
    w_rank = np.argsort(np.argsort([-out["options"][n]["twin_saving"] for n in names]))
    out["rank_correlation"] = round(float(np.corrcoef(t_rank, w_rank)[0, 1]), 3)
    out["truth_best"] = names[int(np.argmin(t_rank))]
    out["twin_best"] = names[int(np.argmin(w_rank))]
    return out


def _truth_from(run) -> dict[str, float]:
    w = run.world
    c = settings()["costs"]
    flows, credits, late = [], 0.0, []
    for o in w.orders.values():
        if not (run.test_start <= o.quote_day < run.test_end) or o.released_day is None:
            continue
        if o.completed_minute is not None:
            flows.append(o.completed_minute / w.cfg["day_minutes"] - o.released_day)
        done = o.shipped_day if o.shipped_day is not None else w.day + 5
        days_late = max(0, done - o.due_day)
        late.append(days_late)
        credits += o.price * min(c["late_credit_cap"], c["late_credit_per_day"] * days_late)
    return {"flow_days": float(np.mean(flows)), "late_credits": credits, "late_days": float(np.mean(late))}
