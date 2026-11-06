"""The simulated company, one working day at a time.

Each morning the planning systems get an Observation and return Decisions: quotes for yesterday's
requests, purchase orders, and which orders to release onto the Grid in which order. The world then
runs the day and writes logs. It keeps the truth (customer sensitivity, true process times,
supplier reliability) to itself.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import pandas as pd
import simpy

from q2s.data.logs import LogStore
from q2s.data.model import Decisions, Observation, Order, QuoteRequest
from q2s.world.demand import Catalog, accepts, make_catalog, requests_for_day
from q2s.world.grid import Grid, OrderState
from q2s.world.params import load_world
from q2s.world.rng import stream


def parts_needed(design, attrition: float) -> dict[str, int]:
    return {sku: math.ceil(uses * design.boards * (1 + attrition)) for sku, uses in design.bom}


class World:
    def __init__(
        self,
        seed: int,
        overrides: dict[str, Any] | None = None,
        scenario: dict[str, Any] | None = None,
        machines_extra: dict[str, int] | None = None,
    ):
        self.cfg = load_world(overrides)
        self.seed = seed
        self.scenario = scenario or {}
        self.catalog: Catalog = make_catalog(self.cfg, seed)
        self.env = simpy.Environment()
        self.store = LogStore()
        self.grid = Grid(self.env, self.cfg, seed, self._log, machines_extra)
        self.day = 0
        self.orders: dict[str, Order] = {}
        self.backlog: list[str] = []
        self.in_progress: set[str] = set()
        self.inventory: dict[str, int] = defaultdict(int)
        self.open_pos: list[dict[str, Any]] = []
        self.next_priority = 0
        self.requests = self._requests(0)
        for item in self.scenario.get("machine_outages", []):
            self.env.process(self._outage(item))

    # ------------------------------------------------------------------ plumbing
    def _log(self, table: str, row: dict[str, Any]) -> None:
        self.store.append(table, row)

    def _requests(self, day: int) -> list[QuoteRequest]:
        mult = 1.0
        for item in self.scenario.get("demand", []):
            if item["from_day"] <= day <= item["to_day"]:
                mult *= item["multiplier"]
        return requests_for_day(self.cfg, self.catalog, self.seed, day, mult)

    def _outage(self, item: dict[str, Any]):
        machine = next(m for ms in self.grid.machines.values() for m in ms if m.name == item["machine"])
        yield self.env.timeout(item["day"] * self.cfg["day_minutes"] + item.get("minute", 480))
        failed = self.env.now
        with machine.res.request(priority=-2) as req:
            yield req
            start = self.env.now
            self.grid.down_until[machine.name] = start + item["minutes"]
            yield self.env.timeout(item["minutes"])
        self._log(
            "downtime",
            {
                "machine": machine.name,
                "kind": machine.kind,
                "failed": failed,
                "repair_start": start,
                "repair_end": self.env.now,
            },
        )

    def _supplier_days(self, sku: str, day: int) -> int:
        s = self.cfg["parts"]["supplier"]
        p = s["constrained_in_stock_probability"] if sku in self.catalog.constrained else s["in_stock_probability"]
        for item in self.scenario.get("supplier_shocks", []):
            if item["from_day"] <= day <= item["to_day"]:
                p *= item["in_stock_multiplier"]
        rng = stream(self.seed, "supplier", sku, day)
        if rng.random() < p:
            return s["in_stock_days"]
        return int(rng.integers(s["backorder_days"]["min"], s["backorder_days"]["max"] + 1))

    # ------------------------------------------------------------------ the day
    def observe(self) -> Observation:
        return Observation(
            day=self.day,
            requests=list(self.requests),
            backlog=[self.orders[o] for o in self.backlog],
            in_progress=[self.orders[o] for o in sorted(self.in_progress)],
            inventory={k: v for k, v in self.inventory.items() if v > 0},
            open_purchases=[(p["sku"], p["qty"], p["day"]) for p in self.open_pos],
            catalog=self.catalog.unit_cost,
        )

    def step(self, decisions: Decisions) -> None:
        day, cfg = self.day, self.cfg
        by_id = {r.request_id: r for r in self.requests}
        for q in decisions.quotes:
            req = by_id.pop(q.request_id, None)
            if req is None:
                continue
            promise = max(1, int(q.promise_days))
            won = accepts(cfg, self.catalog, self.seed, req, q.price, promise)
            d = req.design
            self._log(
                "quotes",
                {
                    "request_id": req.request_id,
                    "day": day,
                    "segment": req.segment,
                    "boards": d.boards,
                    "parts_per_board": d.parts_per_board,
                    "unique_parts": d.unique_parts,
                    "two_sided": d.two_sided,
                    "second_side_share": d.second_side_share,
                    "boards_per_panel": d.boards_per_panel,
                    "tht_parts": d.tht_parts,
                    "fine_pitch_share": d.fine_pitch_share,
                    "parts_cost": round(d.boards * sum(self.catalog.unit_cost[s] * u for s, u in d.bom), 2),
                    "price": round(q.price, 2),
                    "promise_days": promise,
                    "explore": q.explore,
                    "won": won,
                },
            )
            if won:
                oid = "O" + req.request_id[1:]
                self.orders[oid] = Order(oid, req.request_id, req.segment, d, round(q.price, 2), day, day + promise)
                self.backlog.append(oid)
                for sku, uses in d.bom:
                    self._log(
                        "order_lines",
                        {"order_id": oid, "day": day, "sku": sku, "uses_per_board": uses, "boards": d.boards},
                    )
        for req in by_id.values():  # not quoted: lost
            self._log(
                "quotes",
                {
                    "request_id": req.request_id,
                    "day": day,
                    "segment": req.segment,
                    "boards": req.design.boards,
                    "parts_per_board": req.design.parts_per_board,
                    "unique_parts": req.design.unique_parts,
                    "two_sided": req.design.two_sided,
                    "second_side_share": req.design.second_side_share,
                    "boards_per_panel": req.design.boards_per_panel,
                    "tht_parts": req.design.tht_parts,
                    "fine_pitch_share": req.design.fine_pitch_share,
                    "parts_cost": None,
                    "price": None,
                    "promise_days": None,
                    "explore": False,
                    "won": False,
                },
            )
        for po in decisions.purchases:
            if po.qty <= 0:
                continue
            lead = self._supplier_days(po.sku, day)
            self.open_pos.append({"sku": po.sku, "qty": int(po.qty), "day": day, "arrival": day + lead})
            self._log(
                "purchases",
                {"sku": po.sku, "qty": int(po.qty), "day": day, "unit_cost": self.catalog.unit_cost[po.sku]},
            )
        attrition = cfg["parts"]["attrition"]
        for oid in decisions.release:
            if oid not in self.backlog:
                continue
            need = parts_needed(self.orders[oid].design, attrition)
            if any(self.inventory[s] < q for s, q in need.items()):
                continue
            for s, q in need.items():
                self.inventory[s] -= q
            self.backlog.remove(oid)
            self.in_progress.add(oid)
            order = self.orders[oid]
            order.released_day = day
            self.next_priority += 1
            state = OrderState(order, self.next_priority, order.design.panels)
            self.grid.release(state, self._done)
            self._log("releases", {"order_id": oid, "day": day, "minute": self.env.now, "panels": order.design.panels})
        self.env.run(until=(day + 1) * cfg["day_minutes"])
        self._ship(day)
        value = sum(q * self.catalog.unit_cost[s] for s, q in self.inventory.items() if q > 0)
        self._log(
            "inventory",
            {"day": day, "value": round(value, 2), "skus": sum(1 for q in self.inventory.values() if q > 0)},
        )
        self.day += 1
        arrived = [p for p in self.open_pos if p["arrival"] <= self.day]
        self.open_pos = [p for p in self.open_pos if p["arrival"] > self.day]
        for p in arrived:
            self.inventory[p["sku"]] += p["qty"]
            self._log("receipts", {"sku": p["sku"], "qty": p["qty"], "ordered_day": p["day"], "day": self.day})
        self.requests = self._requests(self.day)

    def _done(self, st: OrderState) -> None:
        st.order.completed_minute = self.env.now

    def _ship(self, day: int) -> None:
        cutoff = day * self.cfg["day_minutes"] + self.cfg["shipping_cutoff_minute"]
        for oid in sorted(self.in_progress):
            o = self.orders[oid]
            if o.completed_minute is not None and o.completed_minute <= cutoff:
                o.shipped_day = day
                self.in_progress.discard(oid)
                d = o.design
                self._log(
                    "shipments",
                    {
                        "order_id": oid,
                        "segment": o.segment,
                        "quote_day": o.quote_day,
                        "due_day": o.due_day,
                        "released_day": o.released_day,
                        "completed_minute": o.completed_minute,
                        "shipped_day": day,
                        "price": o.price,
                        "boards": d.boards,
                        "panels": d.panels,
                        "parts_per_board": d.parts_per_board,
                        "sides": d.sides,
                        "tht_parts": d.tht_parts,
                        "fine_pitch_share": d.fine_pitch_share,
                        "unique_parts": d.unique_parts,
                    },
                )

    def frames(self, tables: list[str] | None = None) -> dict[str, pd.DataFrame]:
        return self.store.frames(tables)
