"""Price and promise.

What an order really costs (parts, machine minutes, manual minutes), the earliest ship date the
factory can keep given everything already ahead of it, and the price that maximises expected
margin given how customers respond to price and lead time.

The response model is learned only from quotes priced off the list at random, and only from the
random part: the ratio of the quoted price to the list price (ADR 0002). How the list price itself
varies across designs is the quoting desk's formula, and it moves with what competitors would
charge for the same board; letting the model learn from it would confuse the formula's habits with
the customers' price sensitivity. Prices are only ever set inside the range customers have seen.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from q2s.data.model import Design, Observation, Quote, QuoteRequest
from q2s.policy.calibrate import Calibration
from q2s.policy.settings import parts_cost, parts_needed, rule_of_thumb_cost, settings

SEGMENTS = ("enterprise", "research", "startup")


def cost_estimate(design, catalog: dict[str, float], cal: Calibration) -> float:
    c = settings()["costs"]
    m = cal.job_minutes(design)
    machine = sum(v for k, v in m.items() if k != "manual")
    return parts_cost(design, catalog) + c["labour_per_minute"] * m["manual"] + c["machine_per_minute"] * machine


def list_price(design, catalog: dict[str, float]) -> float:
    return settings()["today"]["markup"] * rule_of_thumb_cost(design, catalog)


def list_price_from_row(row) -> float:
    r = settings()["today"]["rule_of_thumb"]
    assembly = r["per_order"] + row.boards * (
        r["per_board"] + r["per_board_part"] * row.parts_per_board + r["per_board_tht_part"] * row.tht_parts
    )
    return settings()["today"]["markup"] * (float(row.parts_cost) + assembly)


def _features(log_markup: np.ndarray, days: np.ndarray, frame: pd.DataFrame) -> np.ndarray:
    seg = np.column_stack([(frame["segment"] == s).to_numpy(float) for s in SEGMENTS])
    return np.column_stack(
        [
            log_markup,
            days,
            seg,
            np.log(frame["boards"].to_numpy(float)),
            np.log(frame["parts_per_board"].to_numpy(float)),
            log_markup * seg[:, 0],
            log_markup * seg[:, 1],
            days * seg[:, 0],
            days * seg[:, 1],
        ]
    )


def design_from_row(row) -> Design:
    """The quote log carries the design's attributes (not its BOM): enough to cost the work."""
    return Design(
        int(row.boards),
        int(row.parts_per_board),
        int(row.unique_parts),
        bool(row.two_sided),
        float(row.second_side_share),
        int(row.boards_per_panel),
        int(row.tht_parts),
        float(row.fine_pitch_share),
        (),
    )


def logged_cost(row, cal: Calibration) -> float:
    c = settings()["costs"]
    m = cal.job_minutes(design_from_row(row))
    machine = sum(v for k, v in m.items() if k != "manual")
    return float(row.parts_cost) + c["labour_per_minute"] * m["manual"] + c["machine_per_minute"] * machine


@dataclass
class WinModel:
    model: LogisticRegression
    n: int
    slope_price: float
    slope_days: float
    markup_range: tuple[float, float]  # 5th-95th percentile of price / list price customers have seen

    def p(self, req: QuoteRequest, log_markup: np.ndarray, days: np.ndarray) -> np.ndarray:
        d = req.design
        frame = pd.DataFrame(
            {"segment": [req.segment] * len(log_markup), "boards": d.boards, "parts_per_board": d.parts_per_board}
        )
        return self.model.predict_proba(_features(np.asarray(log_markup, float), np.asarray(days, float), frame))[:, 1]


def fit_win_model(quotes: pd.DataFrame, cal: Calibration) -> WinModel:
    """Learn win probability from randomised quotes only."""
    q = quotes[quotes["price"].notna() & quotes["explore"].astype(bool)].copy()
    q["list"] = [list_price_from_row(r) for r in q.itertuples()]
    lm = np.log(q["price"].to_numpy(float) / q["list"].to_numpy(float))
    X = _features(lm, q["promise_days"].to_numpy(float), q)
    model = LogisticRegression(C=10.0, max_iter=2000).fit(X, q["won"].astype(int).to_numpy())
    seen = np.exp(np.quantile(lm, [0.05, 0.95]))
    return WinModel(model, len(q), float(model.coef_[0][0]), float(model.coef_[0][1]), (float(seen[0]), float(seen[1])))


@dataclass
class Capacity:
    per_day: dict[str, float]  # minutes of planned work each group can absorb per day
    flow_days: dict[str, float]  # release-to-ship working days, quantile, by size bucket


def recent_flow(shipments: pd.DataFrame, today: int, days: int = 7) -> dict[str, float]:
    """Release-to-ship working days at the promise quantile, from what shipped in the last week."""
    q = settings()["quote"]["promise_quantile"]
    flow = {"small": 2.0, "medium": 2.0, "large": 3.0}
    if not len(shipments):
        return flow
    recent = shipments[shipments["shipped_day"] >= today - days]
    lead = recent["shipped_day"] - recent["released_day"]
    bucket = pd.cut(recent["panels"], [0, 3, 15, 10_000], labels=["small", "medium", "large"])
    for b, g in lead.groupby(bucket, observed=True):
        if len(g) >= 10:
            flow[str(b)] = float(np.quantile(g, q))
    return flow


def capacity_from_history(frames: dict[str, pd.DataFrame], cal: Calibration) -> Capacity:
    s = settings()
    f, q = s["factory"], s["quote"]["promise_quantile"]
    avail = {
        k: cal.mtbf_minutes[k] / (cal.mtbf_minutes[k] + math.exp(cal.mttr_log[k][0] + cal.mttr_log[k][1] ** 2 / 2))
        for k in cal.mtbf_minutes
    }
    mean_rate = float(np.mean(list(cal.place_rates.values())))
    eff_rate = float(np.sum(list(cal.place_rates.values()))) / len(cal.place_rates)
    per_day = {
        "placer": f["machines"]["placer"] * f["day_minutes"] * avail["placer"] * 0.92 * eff_rate / mean_rate,
        "manual": f["operators"] * (f["shift_end_minute"] - f["shift_start_minute"]) * 0.9,
    }
    ship = frames.get("shipments", pd.DataFrame())
    flow = {"small": 2.0, "medium": 2.0, "large": 3.0}
    if len(ship) > 50:
        lead = ship["shipped_day"] - ship["released_day"]
        bucket = pd.cut(ship["panels"], [0, 3, 15, 10_000], labels=["small", "medium", "large"])
        for b, g in lead.groupby(bucket, observed=True):
            if len(g) >= 10:
                flow[str(b)] = float(np.quantile(g, q))
    return Capacity(per_day, flow)


def _bucket(panels: int) -> str:
    return "small" if panels <= 3 else ("medium" if panels <= 15 else "large")


def free_stock(obs: Observation) -> dict[str, int]:
    """Stock on hand that the backlog has not already claimed."""
    att = settings()["factory"]["attrition"]
    free = dict(obs.inventory)
    for o in obs.backlog:
        for s, q in parts_needed(o.design, att).items():
            free[s] = free.get(s, 0) - q
    return free


def parts_risk(design, obs: Observation, cal: Calibration, free: dict[str, int] | None = None) -> tuple[int, float]:
    """(days until parts are on hand if every distributor has them, probability that they do)."""
    need = parts_needed(design, settings()["factory"]["attrition"])
    stock = free if free is not None else obs.inventory
    missing = [s for s, n in need.items() if stock.get(s, 0) < n]
    if not missing:
        return 0, 1.0
    p_all = 1.0
    for s in missing:
        p_all *= cal.supplier_in_stock.get(s, cal.supplier_in_stock_prior)
    return 3, p_all  # bought tomorrow morning, on hand two days later


class Quoter:
    def __init__(self, cal: Calibration, win: WinModel, cap: Capacity, seed: int):
        self.cal, self.win, self.cap = cal, win, cap
        self.rng = np.random.default_rng(seed)

    def quote_all(
        self, obs: Observation, spare: dict[str, list[float]], explore: bool = True, buffer_days: int = 0
    ) -> list[Quote]:
        """Choose price and promise together.

        The twin's outlook says how many minutes of placer and manual-cell time are still free on
        each coming day once the orders already won are done. A promise is feasible if the job fits
        into the free time between its parts arriving and its ship date. Among feasible promises
        and prices, pick the one with the highest expected margin: P(win) * (price - cost - the
        expected late credit if a distributor turns out to be out of a part). Each quote then
        reserves its expected share of the free time, so the next quote sees less.
        """
        s = settings()
        qcfg, c, f = s["quote"], s["costs"], s["factory"]
        full = {
            "placer": f["machines"]["placer"] * f["day_minutes"] * 0.92,
            "manual": f["operators"] * (f["shift_end_minute"] - f["shift_start_minute"]) * 0.92,
        }
        horizon = max(qcfg["days_options"]) + 4
        free = {g: [*(spare.get(g) or []), *[full[g]] * horizon][:horizon] for g in ("placer", "manual")}
        back = float(np.mean(self.cal.backorder_days)) if self.cal.backorder_days else 20.0
        ratios = np.array(qcfg["list_ratios"], float)
        lo, hi = self.win.markup_range
        ratios = ratios[(ratios >= lo / qcfg["trust_margin"]) & (ratios <= hi * qcfg["trust_margin"])]
        options = np.array(qcfg["days_options"], int)
        out = []
        stock = free_stock(obs)
        for req in sorted(obs.requests, key=lambda r: r.request_id):
            cost = cost_estimate(req.design, obs.catalog, self.cal)
            listed = list_price(req.design, obs.catalog)
            mine = self.cal.job_minutes(req.design)
            wait, p_parts = parts_risk(req.design, obs, self.cal, stock)
            flow = self.cap.flow_days[_bucket(req.design.panels)]
            # a job won today is released tomorrow at the earliest, and needs `tail` days after its
            # last machine minute to get through the manual cell and onto the truck
            start = max(wait, 1)
            tail = max(1, int(np.ceil(flow))) + buffer_days
            feasible = np.array(
                [
                    all(
                        sum(free[g][start : max(start, L - tail + (1 if g == "manual" else 0))]) >= mine[g]
                        for g in ("placer", "manual")
                    )
                    and start + tail <= L
                    for L in options
                ]
            )
            late = np.array([(1 - p_parts) * max(0.0, wait + back + flow - L) for L in options])
            credit = np.minimum(c["late_credit_cap"], c["late_credit_per_day"] * late)
            grid_r, grid_d = np.meshgrid(ratios, options.astype(float), indexing="ij")
            p = self.win.p(req, np.log(grid_r.ravel()), grid_d.ravel()).reshape(grid_r.shape)
            price = grid_r * listed
            ev = p * (price * (1 - credit[None, :]) - cost)
            ev[price < cost * (1 + qcfg["min_margin"])] = -np.inf
            if feasible.any():
                ev[:, ~feasible] = -np.inf
            else:
                ev[:, :-1] = -np.inf  # no room: offer the longest promise
            i, j = np.unravel_index(int(np.argmax(ev)), ev.shape)
            ratio, days = float(ratios[i]), int(options[j])
            is_explore = explore and self.rng.random() < qcfg["explore_share"]
            if is_explore:
                ratio = float(self.rng.uniform(*qcfg["explore_range"]))
            p_win = float(self.win.p(req, np.array([math.log(ratio)]), np.array([float(days)]))[0])
            out.append(Quote(req.request_id, round(ratio * listed, 2), days, explore=is_explore))
            if wait == 0:  # claim the stock this quote expects to use
                for sku, q in parts_needed(req.design, settings()["factory"]["attrition"]).items():
                    stock[sku] = stock.get(sku, 0) - int(np.ceil(q * p_win))
            for g in ("placer", "manual"):
                need = mine[g] * p_win
                d = max(wait, 1)
                while need > 1e-6 and d < horizon:
                    take = min(need, free[g][d])
                    free[g][d] -= take
                    need -= take
                    d += 1
        return out
