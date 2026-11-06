"""The planning system the factory would run every morning.

It recalibrates from the logs once a week, then each morning: sequences the releasable orders,
quotes yesterday's requests (price and promise), places purchase orders, and records an outlook
of the next days that is later checked against what happened. Each part can be switched back to
today's process, which is how the evaluation measures what each part is worth on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from q2s.data.logs import LogStore
from q2s.data.model import Decisions, Observation
from q2s.policy.baseline import baseline_quotes, buy_to_order
from q2s.policy.buy import BaseStock
from q2s.policy.calibrate import Calibration, calibrate
from q2s.policy.outlook import outlook
from q2s.policy.quote import Quoter, capacity_from_history, fit_win_model, recent_flow
from q2s.policy.schedule import RULES
from q2s.policy.settings import settings

CAL_TABLES = ["ops", "manual", "downtime", "quotes", "shipments", "purchases", "receipts", "releases"]


@dataclass
class StackConfig:
    name: str = "system"
    schedule: str = "cpsat"  # fifo | edd | atcs | cpsat
    quoting: str = "model"  # today | model
    buying: str = "base_stock"  # to_order | base_stock
    outlook: bool = True
    recalibrate_days: int = 5


@dataclass
class Ledger:
    """Every forecast the system made, so each can be scored when its day comes."""

    panels: list[dict[str, Any]] = field(default_factory=list)
    ships: list[dict[str, Any]] = field(default_factory=list)
    outlooks: list[dict[str, Any]] = field(default_factory=list)
    calibrations: list[dict[str, Any]] = field(default_factory=list)


class PlanningSystem:
    def __init__(self, config: StackConfig, seed: int):
        self.cfg, self.seed = config, seed
        self.cal: Calibration | None = None
        self.last_cal = -(10**9)
        self.ledger = Ledger()
        self.rng = np.random.default_rng(seed)
        self.buffer_days = 0
        self.last_buffer_change = -(10**9)

    def _check_promises(self, obs: Observation, logs: LogStore) -> None:
        """Close the loop: compare the promises that came due in the last two weeks with what shipped,
        and add (or take back) a day of buffer when the hit rate drifts from the target."""
        target = settings()["quote"]["promise_quantile"]
        ship = logs.frame("shipments")
        if not len(ship) or obs.day - self.last_buffer_change < 3:
            return
        due = ship[(ship.due_day >= obs.day - 7) & (ship.due_day < obs.day) & (ship.quote_day >= self.started)]
        if len(due) < 40:
            return
        hit = float((due.shipped_day <= due.due_day).mean())
        before = self.buffer_days
        if hit < target - 0.03:
            self.buffer_days = min(self.buffer_days + 1, 4)
        elif hit > target + 0.04 and self.buffer_days > 0:
            self.buffer_days -= 1
        if self.buffer_days != before:
            self.last_buffer_change = obs.day
        self.ledger.calibrations.append(
            {"day": obs.day, "promise_hit_rate": round(hit, 3), "buffer_days": self.buffer_days}
        )

    def _recalibrate(self, obs: Observation, logs: LogStore) -> None:
        frames = logs.frames(CAL_TABLES)
        self.cal = calibrate(frames, obs.day)
        self.win = fit_win_model(frames["quotes"], self.cal)
        self.cap = capacity_from_history(frames, self.cal)
        self.quoter = Quoter(self.cal, self.win, self.cap, self.seed + obs.day)
        if self.cfg.buying == "base_stock":
            self.buyer = BaseStock(self.cal, logs.frame("order_lines"), obs.catalog, obs.day, self.seed + obs.day)
        self.last_cal = obs.day
        self.ledger.calibrations.append(
            {
                "day": obs.day,
                "win_quotes": self.win.n,
                "win_price_slope": round(self.win.slope_price, 3),
                "win_days_slope": round(self.win.slope_days, 3),
                "placer_rates": self.cal.place_rates,
                "capacity_per_day": {k: round(v) for k, v in self.cap.per_day.items()},
            }
        )

    def _band_corrections(self, obs: Observation, logs: LogStore) -> dict[int, tuple[float, float]]:
        """Conformal bands: the twin's own spread is too narrow, so the 80% band for each horizon is
        set from the errors its past forecasts actually made (once 10 have matured)."""
        if not self.ledger.panels:
            return {}
        man = logs.frame("manual")
        if not len(man):
            return {}
        actual = (man["end"] // settings()["factory"]["day_minutes"]).astype(int).value_counts()
        out = {}
        past = [r for r in self.ledger.panels if r["day"] < obs.day]
        for h in range(settings()["capacity"]["outlook_days"]):
            errs = [actual.get(r["day"], 0) - r["p50"] for r in past if r["day"] - r["made"] == h]
            if len(errs) >= 10:
                out[h] = (float(np.quantile(errs, 0.1)), float(np.quantile(errs, 0.9)))
        return out

    def _wip_minutes(self, obs: Observation) -> dict[str, float]:
        assert self.cal is not None
        out = {"placer": 0.0, "manual": 0.0}
        for o in obs.in_progress:
            m = self.cal.job_minutes(o.design)
            out["placer"] += 0.5 * m["placer"]
            out["manual"] += 0.7 * m["manual"]
        return out

    def decide(self, obs: Observation, logs: LogStore) -> Decisions:
        if not hasattr(self, "started"):
            self.started = obs.day
        self._check_promises(obs, logs)
        if self.cal is None or obs.day - self.last_cal >= self.cfg.recalibrate_days:
            self._recalibrate(obs, logs)
        assert self.cal is not None
        rule = RULES[self.cfg.schedule]
        release = rule(obs, self.cal, self._wip_minutes(obs)) if self.cfg.schedule == "cpsat" else rule(obs, self.cal)
        look = None
        if self.cfg.outlook or self.cfg.quoting == "model":
            look = outlook(
                obs,
                logs.frames(["ops", "manual", "releases", "quotes"]),
                self.cal,
                release,
                settings()["quote"]["replications"],
                self.seed + obs.day,
            )
        if self.cfg.quoting == "model":
            assert look is not None
            self.cap.flow_days = recent_flow(logs.frame("shipments"), obs.day)
            quotes = self.quoter.quote_all(obs, look.spare, buffer_days=self.buffer_days)
        else:
            quotes = baseline_quotes(obs, explore=False, rng=self.rng)
        purchases = self.buyer.purchases(obs) if self.cfg.buying == "base_stock" else buy_to_order(obs)
        if self.cfg.outlook and look is not None:
            widen = self._band_corrections(obs, logs)
            for i, (p10, p50, p90) in enumerate(look.panels_out):
                lo, hi = widen.get(i, (0.0, 0.0))
                self.ledger.panels.append(
                    {
                        "made": obs.day,
                        "day": obs.day + i,
                        "p10": p10,
                        "p50": p50,
                        "p90": p90,
                        "lo": max(0.0, p50 + lo),
                        "hi": p50 + hi,
                    }
                )
            for oid in look.ship_day_p50:
                if oid.startswith("expected-"):
                    continue
                self.ledger.ships.append(
                    {"made": obs.day, "order_id": oid, "p50": look.ship_day_p50[oid], "p90": look.ship_day_p90[oid]}
                )
            self.ledger.outlooks.append({"day": obs.day, "utilisation": look.utilisation, "flags": look.flags})
        return Decisions(quotes=quotes, release=release, purchases=purchases)


class TodayStack:
    """Today's process with the same interface (FIFO release, cost-plus, buy per order)."""

    def __init__(self, seed: int, explore: bool = False):
        from q2s.policy.baseline import TodaysProcess

        self.inner = TodaysProcess(seed=seed, explore=explore)
        self.ledger = Ledger()

    def decide(self, obs: Observation, logs: LogStore) -> Decisions:
        return self.inner.decide(obs)
