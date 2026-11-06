"""A digital twin of the factory, built only from the Calibration and the company's own facts.

It knows the routing (kitting, print/place/reflow per side, inspection, the manual cell), how many
machines, movers and operators there are, and the shifts. Everything about speed, failures, noise
and congestion comes from the logs. It is used to look a few days ahead, to price what another
machine is worth, and to say how far its own forecasts can be trusted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import simpy

from q2s.data.model import Design
from q2s.policy.calibrate import TRANSPORT_BINS, Calibration
from q2s.policy.settings import settings

EXPECTED_PRIORITY = 10**7  # jobs at or above this priority are expected, not yet won


@dataclass
class TwinJob:
    order_id: str
    design: Design
    priority: int
    release_minute: float
    due_minute: float
    panels_done: int = 0  # panels already through the manual cell
    panels_manual_only: int = 0  # panels already inspected, waiting for the manual cell
    kitted: bool = False


@dataclass
class TwinResult:
    completion: dict[str, float]  # order id -> minute the order is packed
    busy: dict[str, float]  # resource group -> busy minutes over the horizon
    capacity: dict[str, float]  # resource group -> available minutes
    panels_out: list[int] = field(default_factory=list)  # panels leaving the manual cell, per day
    busy_by_day: dict[str, list[float]] = field(default_factory=dict)  # group -> busy minutes per day
    known_by_day: dict[str, list[float]] = field(default_factory=dict)  # the same, orders already won


class _Run:
    def __init__(self, cal: Calibration, rng: np.random.Generator, start: float, extra: dict[str, int] | None):
        f = settings()["factory"]
        self.cal, self.rng, self.f = cal, rng, f
        self.env = simpy.Environment(initial_time=start)
        self.start = start
        self.busy_day: dict[str, dict[int, float]] = {}
        self.busy_day_known: dict[str, dict[int, float]] = {}  # excluding expected (not yet won) orders
        extra = extra or {}
        self.movers = simpy.PriorityResource(self.env, f["movers"] + extra.get("movers", 0))
        self.groups: dict[str, list[tuple[str, simpy.PriorityResource, float]]] = {}
        for kind in ("kitting", "printer", "oven", "aoi"):
            n = f["machines"][kind] + extra.get(kind, 0)
            self.groups[kind] = [(f"{kind}-{i}", simpy.PriorityResource(self.env, 1), 1.0) for i in range(n)]
        rates = [cal.place_rates.get(name, float(np.mean(list(cal.place_rates.values())))) for name in f["placers"]]
        rates += [max(rates)] * extra.get("placer", 0)
        self.groups["placer"] = [(f"placer-{i}", simpy.PriorityResource(self.env, 1), r) for i, r in enumerate(rates)]
        self.operators = f["operators"] + extra.get("operators", 0)
        self.manual = simpy.PriorityResource(self.env, self.operators)
        self.busy = {k: 0.0 for k in [*self.groups, "manual"]}
        self.completion: dict[str, float] = {}
        self.panel_out: list[float] = []
        self.stencil: dict[str, str] = {}
        for kind, ms in self.groups.items():
            for name, res, _ in ms:
                self.env.process(self._failures(kind, res))
        self.env.process(self._shifts())

    def _noise(self, station: str) -> float:
        s = self.cal.sigma.get(station, 0.12)
        return math.exp(self.rng.normal(-s * s / 2, s))

    def _failures(self, kind: str, res: simpy.PriorityResource):
        mu, sd = self.cal.mttr_log[kind]
        while True:
            yield self.env.timeout(self.rng.exponential(self.cal.mtbf_minutes[kind]))
            with res.request(priority=-1) as req:
                yield req
                yield self.env.timeout(self.rng.lognormal(mu, sd))

    def _shifts(self):
        day, f = self.f["day_minutes"], self.f
        while True:
            now = self.env.now
            end = int(now // day) * day + f["shift_end_minute"]
            if now >= end:
                end += day
            yield self.env.timeout(end - now)
            reqs = [self.manual.request(priority=-1) for _ in range(self.operators)]
            yield simpy.AllOf(self.env, reqs)
            nxt = (int(self.env.now // day) + 1) * day + f["shift_start_minute"]
            yield self.env.timeout(max(0.0, nxt - self.env.now))
            for r in reqs:
                self.manual.release(r)

    def _pick(self, kind: str):
        return min(self.groups[kind], key=lambda m: (len(m[1].queue) + m[1].count + 1) / m[2])

    def _add(self, kind: str, minutes: float, at: float, prio: int = 0) -> None:
        self.busy[kind] += minutes
        day = int((at - self.start) // self.f["day_minutes"])
        per = self.busy_day.setdefault(kind, {})
        per[day] = per.get(day, 0.0) + minutes
        if prio < EXPECTED_PRIORITY:
            known = self.busy_day_known.setdefault(kind, {})
            known[day] = known.get(day, 0.0) + minutes

    def _use(self, kind: str, minutes_fn, prio: int):
        name, res, rate = self._pick(kind)
        with res.request(priority=prio) as req:
            yield req
            started = self.env.now
            minutes = minutes_fn(rate) * self._noise(kind)
            yield self.env.timeout(minutes)
        self._add(kind, minutes, started, prio)

    def _transport(self):
        u = self.movers.count / self.movers.capacity
        i = int(np.searchsorted(TRANSPORT_BINS, u, side="right") - 1)
        t = self.cal.transport_by_load[min(max(i, 0), len(self.cal.transport_by_load) - 1)]
        yield self.env.timeout(t * math.exp(self.rng.normal(-0.02, 0.2)))

    def job(self, j: TwinJob):
        d, cal = j.design, self.cal
        yield self.env.timeout(max(0.0, j.release_minute - self.env.now))
        left = d.panels - j.panels_done
        if left <= 0:
            self.completion[j.order_id] = self.env.now
            return
        done = []
        for idx in range(j.panels_done, d.panels):
            boards = d.boards_per_panel if idx < d.panels - 1 else d.boards - d.boards_per_panel * (d.panels - 1)
            manual_only = idx < j.panels_done + j.panels_manual_only
            done.append(self.env.process(self._panel(j, idx, boards, manual_only)))
        yield simpy.AllOf(self.env, done)
        with self.manual.request(priority=j.priority) as req:
            yield req
            started = self.env.now
            t = cal.pack_minutes * self._noise("manual")
            yield self.env.timeout(t)
        self._add("manual", t, started, j.priority)
        self.completion[j.order_id] = self.env.now

    def _panel(self, j: TwinJob, idx: int, boards: int, manual_only: bool):
        d, cal = j.design, self.cal
        if not manual_only:
            with self.movers.request(priority=j.priority) as mv:
                yield mv
                first = not j.kitted
                j.kitted = True
                yield from self._use(
                    "kitting",
                    lambda r: (
                        cal.kit_per_panel + (cal.kit_per_job + cal.kit_per_unique * d.unique_parts if first else 0)
                    ),
                    j.priority,
                )
                for side in range(1, d.sides + 1):
                    yield from self._transport()
                    name = self._pick("printer")[0]
                    stencil = cal.print_stencil if self.stencil.get(name) != f"{j.order_id}/{side}" else 0.0
                    self.stencil[name] = f"{j.order_id}/{side}"
                    yield from self._use("printer", lambda r, s=stencil: cal.print_per_panel + s, j.priority)
                    yield from self._transport()
                    parts = d.parts_on_side(side) * boards
                    yield from self._use(
                        "placer",
                        lambda r, p=parts: (
                            (cal.place_handling + p / r) * (1 + cal.place_fine_pitch * d.fine_pitch_share)
                        ),
                        j.priority,
                    )
                    yield from self._transport()
                    gap = cal.oven_total / (settings()["factory"]["oven_concurrency"] + 1)
                    yield from self._use("oven", lambda r, g=gap: g, j.priority)
                    yield self.env.timeout(cal.oven_total - gap)
                yield from self._transport()
                yield from self._use(
                    "aoi", lambda r: cal.aoi_base + cal.aoi_per_part * d.parts_per_board * boards, j.priority
                )
        p_def = min(
            0.9,
            max(
                0.0,
                cal.defect_coef[0] + cal.defect_coef[1] * d.parts_per_board + cal.defect_coef[2] * d.fine_pitch_share,
            ),
        )
        defects = self.rng.binomial(boards, p_def)
        with self.manual.request(priority=j.priority) as req:
            yield req
            started = self.env.now
            t = (
                cal.manual_qc_per_board * boards
                + cal.manual_tht_per_part * d.tht_parts * boards
                + cal.manual_rework * defects
            ) * self._noise("manual")
            yield self.env.timeout(t)
        self._add("manual", t, started, j.priority)
        self.panel_out.append(self.env.now)


def simulate(
    cal: Calibration,
    jobs: list[TwinJob],
    start_minute: float,
    days: float,
    seed: int,
    extra: dict[str, int] | None = None,
) -> TwinResult:
    rng = np.random.default_rng(seed)
    run = _Run(cal, rng, start_minute, extra)
    for j in sorted(jobs, key=lambda j: j.priority):
        run.env.process(run.job(TwinJob(**j.__dict__)))
    end = start_minute + days * settings()["factory"]["day_minutes"]
    run.env.run(until=end)
    f = settings()["factory"]
    span = end - start_minute
    cap = {k: len(v) * span for k, v in run.groups.items()}
    cap["manual"] = run.operators * days * (f["shift_end_minute"] - f["shift_start_minute"])
    day = f["day_minutes"]
    out = [0] * int(math.ceil(days))
    for t in run.panel_out:
        i = int((t - start_minute) // day)
        if 0 <= i < len(out):
            out[i] += 1
    by_day = {k: [run.busy_day.get(k, {}).get(i, 0.0) for i in range(len(out))] for k in run.busy}
    known = {k: [run.busy_day_known.get(k, {}).get(i, 0.0) for i in range(len(out))] for k in run.busy}
    return TwinResult(run.completion, run.busy, cap, out, by_day, known)


def ship_day(completion_minute: float) -> int:
    f = settings()["factory"]
    day = int(completion_minute // f["day_minutes"])
    return day if completion_minute - day * f["day_minutes"] <= f["shipping_cutoff_minute"] else day + 1
