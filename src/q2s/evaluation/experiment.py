"""Runs planning stacks on the simulated company and scores them against the truth.

Every stack starts from the same history (60 working days of today's process, with a share of
quotes priced at random so price response can be learned), then runs the same test days on the
same random streams: same requests, same machine failures, same supplier delays, same customer
draws. Afterwards a drain period runs with no new quotes, so the orders quoted in the test window
can finish. Only orders quoted in the test window are scored.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from q2s.data.model import Decisions
from q2s.policy.settings import settings
from q2s.policy.stack import PlanningSystem, StackConfig, TodayStack
from q2s.world.sim import World, parts_needed

HISTORY_DAYS = 60
TEST_DAYS = 35
BURN_IN_DAYS = 10  # the first test days absorb the backlog today's process left; not scored
DRAIN_DAYS = 8

STACKS: dict[str, Callable[[int], Any]] = {
    "today": lambda seed: TodayStack(seed),
    "system": lambda seed: PlanningSystem(StackConfig("system"), seed),
    "system, FIFO release": lambda seed: PlanningSystem(StackConfig("fifo", schedule="fifo", outlook=False), seed),
    "system, ATCS release": lambda seed: PlanningSystem(StackConfig("atcs", schedule="atcs", outlook=False), seed),
    "system, today's quotes": lambda seed: PlanningSystem(StackConfig("quotes", quoting="today", outlook=False), seed),
    "system, buy per order": lambda seed: PlanningSystem(StackConfig("buy", buying="to_order", outlook=False), seed),
    "today + scheduling + buying": lambda seed: PlanningSystem(
        StackConfig("ops-only", quoting="today", outlook=False), seed
    ),
}


@dataclass
class Run:
    stack: str
    seed: int
    world: World
    ledger: Any
    test_start: int
    test_end: int


def run_stack(
    stack: str,
    seed: int,
    history: int = HISTORY_DAYS,
    test: int = TEST_DAYS,
    drain: int = DRAIN_DAYS,
    scenario: dict[str, Any] | None = None,
    machines_extra: dict[str, int] | None = None,
    outlook: bool | None = None,
) -> Run:
    world = World(seed, scenario=scenario, machines_extra=machines_extra)
    warm = TodayStack(seed, explore=True)
    for _ in range(history):
        world.step(warm.decide(world.observe(), world.store))
    policy = STACKS[stack](seed)
    if outlook is not None and isinstance(policy, PlanningSystem):
        policy.cfg.outlook = outlook
    for _ in range(test):
        world.step(policy.decide(world.observe(), world.store))
    for _ in range(drain):
        d = policy.decide(world.observe(), world.store)
        world.step(Decisions(quotes=[], release=d.release, purchases=d.purchases))
    return Run(stack, seed, world, policy.ledger, history + min(BURN_IN_DAYS, test // 3), history + test)


def score(run: Run) -> dict[str, Any]:
    w = run.world
    c = settings()["costs"]
    f = w.frames()
    q = f["quotes"]
    tq = q[(q.day >= run.test_start) & (q.day < run.test_end)]
    accepted = {oid: o for oid, o in w.orders.items() if run.test_start <= o.quote_day < run.test_end}
    end_day = w.day
    ops = f["ops"].copy()
    ops["t"] = ops["end"] - ops["start"]
    machine_min = ops[(ops.station != "transport") & ops.order_id.isin(accepted)].groupby("order_id")["t"].sum()
    man = f["manual"].copy()
    man["t"] = man["end"] - man["start"]
    manual_min = man[man.order_id.isin(accepted)].groupby("order_id")["t"].sum()
    revenue = parts = labour = machine = credits = 0.0
    late_days, lead, on_time, shipped = [], [], [], 0
    for oid, o in accepted.items():
        revenue += o.price
        parts += sum(w.catalog.unit_cost[s] * n for s, n in parts_needed(o.design, w.cfg["parts"]["attrition"]).items())
        labour += c["labour_per_minute"] * float(manual_min.get(oid, 0.0))
        machine += c["machine_per_minute"] * float(machine_min.get(oid, 0.0))
        done = o.shipped_day if o.shipped_day is not None else end_day + 5
        shipped += o.shipped_day is not None
        late = max(0, done - o.due_day)
        late_days.append(late)
        on_time.append(late == 0)
        lead.append(done - o.quote_day)
        credits += o.price * min(c["late_credit_cap"], c["late_credit_per_day"] * late)
    inv = f["inventory"]
    window = inv[(inv.day >= run.test_start) & (inv.day < end_day)]
    holding = float(window["value"].sum()) * c["holding_rate_per_year"] / c["working_days_per_year"]
    start_inv = (
        float(inv.loc[inv.day == run.test_start - 1, "value"].iloc[0]) if (inv.day == run.test_start - 1).any() else 0.0
    )
    end_inv = float(inv["value"].iloc[-1])
    contribution = revenue - parts - labour - machine - credits - holding
    priced = tq[tq.price.notna()]
    rel = f["releases"]
    rel = rel[rel.order_id.isin(accepted)].set_index("order_id")
    parts_wait = [rel.loc[oid, "day"] - o.quote_day for oid, o in accepted.items() if oid in rel.index]
    n_days = run.test_end - run.test_start
    return {
        "stack": run.stack,
        "seed": run.seed,
        "requests": len(tq),
        "win_rate": round(float(tq.won.mean()), 4),
        "orders": len(accepted),
        "shipped_share": round(shipped / max(len(accepted), 1), 4),
        "revenue": round(revenue, 0),
        "contribution": round(contribution, 0),
        "late_credits": round(credits, 0),
        "holding": round(holding, 0),
        "inventory_start": round(start_inv, 0),
        "inventory_end": round(end_inv, 0),
        "on_time": round(float(np.mean(on_time)) if on_time else 0.0, 4),
        "late_days_mean": round(float(np.mean(late_days)) if late_days else 0.0, 3),
        "lead_days_median": float(np.median(lead)) if lead else 0.0,
        "lead_days_p90": float(np.quantile(lead, 0.9)) if lead else 0.0,
        "promise_days_median": float(priced.promise_days.median()) if len(priced) else 0.0,
        "markup_median": round(float((priced.price / priced.parts_cost.clip(lower=1)).median()), 3)
        if len(priced)
        else 0.0,
        "parts_wait_days_mean": round(float(np.mean(parts_wait)), 3) if parts_wait else 0.0,
        "boards_per_day": round(sum(o.design.boards for o in accepted.values()) / n_days, 1),
        "contribution_per_day": round(contribution / n_days, 0),
    }


def forecast_checks(run: Run) -> dict[str, Any]:
    """Score the outlook ledger against what happened: panels finished per day, ship days."""
    led = run.ledger
    if not led.panels:
        return {}
    w = run.world
    man = w.frames(["manual"])["manual"]
    actual = (man["end"] // w.cfg["day_minutes"]).astype(int).value_counts()
    p = pd.DataFrame(led.panels)
    p = p[p.day < run.test_end]
    p["actual"] = p.day.map(actual).fillna(0)
    p["horizon"] = p.day - p.made
    out: dict[str, Any] = {"panels": {}}
    for h, g in p.groupby("horizon"):
        out["panels"][int(h)] = {
            "n": len(g),
            "mape": round(float((abs(g.p50 - g.actual) / g.actual.clip(lower=1)).mean()), 3),
            "bias": round(float((g.p50 - g.actual).sum() / g.actual.sum()), 3),
            "coverage_p10_p90": round(float(((g.actual >= g.p10) & (g.actual <= g.p90)).mean()), 3),
        }
    s = pd.DataFrame(led.ships)
    s = s[s.made < run.test_end]
    shipped = {oid: o.shipped_day for oid, o in w.orders.items() if o.shipped_day is not None}
    s["actual"] = s.order_id.map(shipped)
    s = s.dropna(subset=["actual"])
    s["err"] = s.p50 - s.actual
    if "lo" in p:
        q = p.dropna(subset=["lo"])
        out["panels_conformal"] = {
            int(h): round(float(((g.actual >= g.lo) & (g.actual <= g.hi)).mean()), 3)
            for h, g in q[q.made >= q.made.min() + 10].groupby("horizon")
        }
    out["ship_day"] = {
        "n": len(s),
        "within_1_day": round(float((s.err.abs() <= 1).mean()), 3),
        "median_error_days": float(s.err.median()),
        "p90_covers": round(float((s.actual <= s.p90).mean()), 3),
    }
    return out
