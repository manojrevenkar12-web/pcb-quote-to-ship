"""The factory floor: a Grid-style factory-scale robot and an off-grid manual cell (SimPy).

Every panel rides a mover through kitting, paste printing, placement and reflow (once per side)
and optical inspection, then leaves the Grid for the manual cell (through-hole parts, rework, QC
and packing). There are no changeovers: parts travel with the panel on its tray. Machines fail and
are repaired between operations. Movers slow each other down when the floor is busy, an effect
the logs show only indirectly.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import simpy

from q2s.data.model import Order
from q2s.world.rng import stream

Log = Callable[[str, dict[str, Any]], None]


@dataclass
class Machine:
    name: str
    kind: str
    res: simpy.PriorityResource
    rate: float = 0.0  # placers only: placements per minute
    last_job: str | None = None  # printers: stencil loaded for this job and side


@dataclass
class OrderState:
    order: Order
    priority: int
    panels_left: int
    kitted: bool = False
    first_done: set[str] = field(default_factory=set)


class Grid:
    def __init__(
        self,
        env: simpy.Environment,
        world: dict[str, Any],
        seed: int,
        log: Log,
        machines_extra: dict[str, int] | None = None,
    ):
        self.env, self.w, self.seed, self.log = env, world, seed, log
        g = world["grid"]
        extra = machines_extra or {}
        self.movers = simpy.PriorityResource(env, capacity=g["movers"] + extra.get("movers", 0))
        self.fleet = self.movers.capacity
        self.machines: dict[str, list[Machine]] = {}
        for kind in ("kitting", "printer", "aoi"):
            n = g[kind]["machines"] + extra.get(kind, 0)
            self.machines[kind] = [Machine(f"{kind}-{i + 1}", kind, simpy.PriorityResource(env, 1)) for i in range(n)]
        placers = list(g["placer"]["machines"])
        for i in range(extra.get("placer", 0)):
            placers.append(
                {"name": f"placer-{len(placers) + 1}", "rate": max(m["rate"] for m in g["placer"]["machines"])}
            )
        self.machines["placer"] = [
            Machine(p["name"], "placer", simpy.PriorityResource(env, 1), rate=p["rate"]) for p in placers
        ]
        n_ovens = g["oven"]["machines"] + extra.get("oven", 0)
        self.machines["oven"] = [
            Machine(f"oven-{i + 1}", "oven", simpy.PriorityResource(env, 1)) for i in range(n_ovens)
        ]
        self.operators = world["manual"]["operators"] + extra.get("operators", 0)
        self.manual = simpy.PriorityResource(env, capacity=self.operators)
        self.down_until: dict[str, float] = {}
        for kind, ms in self.machines.items():
            for m in ms:
                env.process(self._failures(m))
        env.process(self._shifts())

    # ------------------------------------------------------------------ helpers
    def _noise(self, *key: object) -> float:
        sigma = self.w["grid"]["placer"]["noise_sigma"]
        return math.exp(stream(self.seed, "op", *key).normal(-sigma * sigma / 2, sigma))

    def _pick(self, kind: str) -> Machine:
        def load(m: Machine) -> float:
            busy = len(m.res.queue) + m.res.count
            down = 1 if self.down_until.get(m.name, -1) > self.env.now else 0
            speed = m.rate if m.rate else 1.0
            return (busy + 1 + 3 * down) / speed

        return min(self.machines[kind], key=load)

    def _transport(self, key: tuple[object, ...], order_id: str, panel: str):
        t = self.w["grid"]["transport"]
        busy = self.movers.count / self.fleet
        minutes = t["base_minutes"] * (1 + t["congestion"] * busy**3) * self._noise("transport", *key)
        start = self.env.now
        yield self.env.timeout(minutes)
        self.log(
            "ops",
            {
                "order_id": order_id,
                "panel_id": panel,
                "side": 0,
                "station": "transport",
                "machine": "mover",
                "arrive": start,
                "start": start,
                "end": self.env.now,
                "busy_movers": self.movers.count,
                "fleet": self.fleet,
            },
        )

    def _operate(self, kind: str, minutes: float, order_id: str, panel: str, side: int, prio: int):
        m = self._pick(kind)
        arrive = self.env.now
        with m.res.request(priority=prio) as req:
            yield req
            start = self.env.now
            yield self.env.timeout(minutes)
        self.log(
            "ops",
            {
                "order_id": order_id,
                "panel_id": panel,
                "side": side,
                "station": kind,
                "machine": m.name,
                "arrive": arrive,
                "start": start,
                "end": self.env.now,
                "busy_movers": self.movers.count,
                "fleet": self.fleet,
            },
        )
        return m

    # ------------------------------------------------------------------ processes
    def _failures(self, m: Machine):
        cfg = self.w["grid"][m.kind]
        rng = stream(self.seed, "failures", m.name)
        while True:
            yield self.env.timeout(rng.exponential(cfg["mtbf_hours"] * 60))
            failed = self.env.now
            repair = rng.lognormal(math.log(cfg["mttr_minutes_mean"]) - 0.18, 0.6)
            self.down_until[m.name] = failed + repair
            with m.res.request(priority=-1) as req:
                yield req
                start = self.env.now
                yield self.env.timeout(repair)
            self.log(
                "downtime",
                {
                    "machine": m.name,
                    "kind": m.kind,
                    "failed": failed,
                    "repair_start": start,
                    "repair_end": self.env.now,
                },
            )

    def _shifts(self):
        man = self.w["manual"]
        day = self.w["day_minutes"]
        while True:
            now = self.env.now
            today = int(now // day)
            end = today * day + man["shift_end_minute"]
            if now >= end:
                end += day
            yield self.env.timeout(end - now)
            reqs = [self.manual.request(priority=-1) for _ in range(self.operators)]
            yield simpy.AllOf(self.env, reqs)
            start_next = (int(self.env.now // day) + 1) * day + man["shift_start_minute"]
            yield self.env.timeout(max(0.0, start_next - self.env.now))
            for r in reqs:
                self.manual.release(r)

    def release(self, state: OrderState, on_done: Callable[[OrderState], None]) -> None:
        d = state.order.design
        for p in range(d.panels):
            boards = d.boards_per_panel if p < d.panels - 1 else d.boards - d.boards_per_panel * (d.panels - 1)
            self.env.process(self._panel(state, p, boards, on_done))

    def _panel(self, st: OrderState, idx: int, boards: int, on_done: Callable[[OrderState], None]):
        g, man = self.w["grid"], self.w["manual"]
        o, d = st.order, st.order.design
        pid = f"{o.order_id}-P{idx:03d}"
        with self.movers.request(priority=st.priority) as mover:
            yield mover
            first = not st.kitted
            st.kitted = True
            kit = g["kitting"]["per_panel_minutes"] + (
                g["kitting"]["per_job_minutes"] + g["kitting"]["per_unique_part_minutes"] * d.unique_parts
                if first
                else 0
            )
            yield from self._operate("kitting", kit * self._noise(pid, "kit"), o.order_id, pid, 0, st.priority)
            for side in range(1, d.sides + 1):
                yield from self._transport((pid, side, "p"), o.order_id, pid)
                printer = self._pick("printer")
                stencil = g["printer"]["stencil_minutes"] if printer.last_job != f"{o.order_id}/{side}" else 0.0
                printer.last_job = f"{o.order_id}/{side}"
                yield from self._operate(
                    "printer",
                    (g["printer"]["per_panel_side_minutes"] + stencil) * self._noise(pid, side, "print"),
                    o.order_id,
                    pid,
                    side,
                    st.priority,
                )
                yield from self._transport((pid, side, "pl"), o.order_id, pid)
                parts = d.parts_on_side(side) * boards
                placer = self._pick("placer")
                minutes = (
                    (g["placer"]["handling_minutes"] + parts / placer.rate)
                    * (1 + g["placer"]["fine_pitch_slowdown"] * d.fine_pitch_share)
                    * self._noise(pid, side, "place")
                )
                arrive = self.env.now
                with placer.res.request(priority=st.priority) as req:
                    yield req
                    start = self.env.now
                    yield self.env.timeout(minutes)
                self.log(
                    "ops",
                    {
                        "order_id": o.order_id,
                        "panel_id": pid,
                        "side": side,
                        "station": "placer",
                        "machine": placer.name,
                        "arrive": arrive,
                        "start": start,
                        "end": self.env.now,
                        "busy_movers": self.movers.count,
                        "fleet": self.fleet,
                    },
                )
                yield from self._transport((pid, side, "o"), o.order_id, pid)
                oven = self._pick("oven")
                arrive = self.env.now
                spacing = g["oven"]["dwell_minutes"] / g["oven"]["concurrency"]
                with oven.res.request(priority=st.priority) as req:
                    yield req
                    start = self.env.now
                    yield self.env.timeout(spacing)
                yield self.env.timeout(g["oven"]["dwell_minutes"])
                self.log(
                    "ops",
                    {
                        "order_id": o.order_id,
                        "panel_id": pid,
                        "side": side,
                        "station": "oven",
                        "machine": oven.name,
                        "arrive": arrive,
                        "start": start,
                        "end": self.env.now,
                        "busy_movers": self.movers.count,
                        "fleet": self.fleet,
                    },
                )
            yield from self._transport((pid, "a"), o.order_id, pid)
            aoi = (g["aoi"]["base_minutes"] + g["aoi"]["per_part_minutes"] * d.parts_per_board * boards) * self._noise(
                pid, "aoi"
            )
            yield from self._operate("aoi", aoi, o.order_id, pid, 0, st.priority)
        # off the Grid: the manual cell
        p_defect = (
            man["defect_base"]
            + man["defect_per_part"] * d.parts_per_board
            + man["defect_fine_pitch"] * d.fine_pitch_share
        )
        defects = int(stream(self.seed, "defects", pid).binomial(boards, min(p_defect, 0.9)))
        minutes = (
            man["qc_minutes_per_board"] * boards
            + man["tht_minutes_per_part"] * d.tht_parts * boards
            + man["rework_minutes"] * defects
        ) * self._noise(pid, "manual")
        arrive = self.env.now
        with self.manual.request(priority=st.priority) as req:
            yield req
            start = self.env.now
            yield self.env.timeout(minutes)
        self.log(
            "manual",
            {
                "order_id": o.order_id,
                "panel_id": pid,
                "boards": boards,
                "defects": defects,
                "arrive": arrive,
                "start": start,
                "end": self.env.now,
            },
        )
        st.panels_left -= 1
        if st.panels_left == 0:
            with self.manual.request(priority=st.priority) as req:
                yield req
                yield self.env.timeout(man["pack_minutes_per_order"] * self._noise(o.order_id, "pack"))
            on_done(st)
