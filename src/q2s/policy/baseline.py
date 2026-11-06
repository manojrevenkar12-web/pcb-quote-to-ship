"""Today's process: cost-plus quotes with a standard lead time, parts bought per order, and
orders released first come, first served. Everything the models are measured against."""

from __future__ import annotations

import numpy as np

from q2s.data.model import Decisions, Observation, PurchaseOrder, Quote
from q2s.policy.settings import parts_needed, rule_of_thumb_cost, settings


def baseline_quotes(obs: Observation, explore: bool, rng: np.random.Generator) -> list[Quote]:
    t = settings()["today"]
    out = []
    for req in obs.requests:
        cost = rule_of_thumb_cost(req.design, obs.catalog)
        missing = any(obs.inventory.get(s, 0) < q for s, q in parts_needed(req.design).items())
        days = t["standard_days"] + (t["parts_missing_extra_days"] if missing else 0)
        price = t["markup"] * cost
        is_explore = explore and rng.random() < t["explore_share"]
        if is_explore:
            price *= rng.uniform(*t["explore_price"])
            days = max(2, days + int(rng.integers(t["explore_days"][0], t["explore_days"][1] + 1)))
        out.append(Quote(req.request_id, round(price, 2), int(days), explore=is_explore))
    return out


def buy_to_order(obs: Observation) -> list[PurchaseOrder]:
    """Buy exactly what the backlog is short of, net of what is already on order."""
    need: dict[str, int] = {}
    for o in obs.backlog:
        for s, q in parts_needed(o.design).items():
            need[s] = need.get(s, 0) + q
    on_order: dict[str, int] = {}
    for s, q, _ in obs.open_purchases:
        on_order[s] = on_order.get(s, 0) + q
    out = []
    for s, q in sorted(need.items()):
        short = q - obs.inventory.get(s, 0) - on_order.get(s, 0)
        if short > 0:
            out.append(PurchaseOrder(s, short))
    return out


def fifo_release(obs: Observation) -> list[str]:
    inv = dict(obs.inventory)
    out = []
    for o in sorted(obs.backlog, key=lambda o: (o.quote_day, o.order_id)):
        need = parts_needed(o.design)
        if all(inv.get(s, 0) >= q for s, q in need.items()):
            for s, q in need.items():
                inv[s] -= q
            out.append(o.order_id)
    return out


class TodaysProcess:
    name = "today"

    def __init__(self, seed: int = 0, explore: bool = False):
        self.rng = np.random.default_rng(seed)
        self.explore = explore

    def decide(self, obs: Observation, history=None) -> Decisions:
        return Decisions(
            quotes=baseline_quotes(obs, self.explore, self.rng), release=fifo_release(obs), purchases=buy_to_order(obs)
        )
