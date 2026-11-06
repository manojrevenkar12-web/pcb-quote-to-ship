"""What parts to stock, when to order them, and how much shortage risk to accept.

Popular parts are kept on an order-up-to level sized from their demand over the replenishment time,
at a service level set by what a shortage costs (an order waiting for a part) against what holding
the part costs. Each part's supplier reliability comes from the calibration, so a part whose
distributors are often out is stocked deeper. Everything else is still bought per order.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from q2s.data.model import Observation, PurchaseOrder
from q2s.policy.calibrate import Calibration
from q2s.policy.settings import parts_needed, settings

STANDARD_DAYS = 3  # ordered in the morning, on hand two working days later, used the next


def daily_demand(order_lines: pd.DataFrame, today: int, window: int) -> pd.DataFrame:
    """Units per sku per day over the last `window` days (rows: day, columns: sku)."""
    att = settings()["factory"]["attrition"]
    lines = order_lines[order_lines["day"] >= today - window].copy()
    lines["units"] = np.ceil(lines["uses_per_board"] * lines["boards"] * (1 + att))
    table = lines.pivot_table(index="day", columns="sku", values="units", aggfunc="sum", fill_value=0)
    return table.reindex(range(max(0, today - window), today), fill_value=0)


class BaseStock:
    def __init__(
        self,
        cal: Calibration,
        order_lines: pd.DataFrame,
        catalog: dict[str, float],
        today: int,
        seed: int,
        window: int = 30,
    ):
        s = settings()
        b = s["buy"]
        holding_per_day = s["costs"]["holding_rate_per_year"] / s["costs"]["working_days_per_year"]
        demand = daily_demand(order_lines, today, window)
        freq = (demand > 0).sum().sort_values(ascending=False)
        self.skus = list(freq.index[: b["stock_top_skus"]])
        back = np.array(cal.backorder_days or [20], float)
        rng = np.random.default_rng(seed)
        recent = order_lines[order_lines["day"] >= today - window]
        per_line = (recent.uses_per_board * recent.boards).groupby(recent.sku).mean()
        self.order_up_to: dict[str, int] = {}
        self.service: dict[str, float] = {}
        n = 1500
        for sku in self.skus:
            d = demand[sku].to_numpy(float)
            p_in = cal.supplier_in_stock.get(sku, cal.supplier_in_stock_prior)
            exp_delay = p_in * STANDARD_DAYS + (1 - p_in) * float(back.mean())
            shortage = b["delay_cost_per_order_day"] * exp_delay / max(float(per_line.get(sku, 1.0)), 1.0)
            holding = catalog[sku] * holding_per_day * (exp_delay + b["review_days"])
            sl = float(np.clip(shortage / (shortage + holding), b["service_level_floor"], b["service_level_cap"]))
            lead = np.where(rng.random(n) < p_in, STANDARD_DAYS, rng.choice(back, n)).astype(int) + b["review_days"]
            width = int(lead.max())
            draws = rng.choice(d, size=(n, width))
            ltd = (draws * (np.arange(width)[None, :] < lead[:, None])).sum(axis=1)
            self.service[sku] = sl
            self.order_up_to[sku] = int(math.ceil(np.quantile(ltd, sl)))

    def purchases(self, obs: Observation) -> list[PurchaseOrder]:
        att = settings()["factory"]["attrition"]
        need: dict[str, int] = {}
        for o in obs.backlog:
            for s, q in parts_needed(o.design, att).items():
                need[s] = need.get(s, 0) + q
        on_order: dict[str, int] = {}
        for s, q, _ in obs.open_purchases:
            on_order[s] = on_order.get(s, 0) + q
        out = []
        for s in sorted(set(need) | set(self.order_up_to)):
            position = obs.inventory.get(s, 0) + on_order.get(s, 0) - need.get(s, 0)
            target = self.order_up_to.get(s, 0)
            if position < target:
                out.append(PurchaseOrder(s, int(target - position)))
        return out
