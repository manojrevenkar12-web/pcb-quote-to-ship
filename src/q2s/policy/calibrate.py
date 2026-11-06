"""Learns how the factory really runs, from the logs alone.

Every station's process time, every machine kind's failures and repairs, the movers' slowdown when
the floor is busy, the manual cell's minutes and the defect rate, and how reliable each supplier is.
The result is a Calibration: the numbers the twin, the quoting desk and the buyer all share, so the
whole company plans with one definition of how long things take.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from q2s.policy.settings import settings

TRANSPORT_BINS = (0.0, 0.5, 0.75, 0.9, 1.01)


@dataclass
class Calibration:
    kit_per_panel: float
    kit_per_job: float
    kit_per_unique: float
    print_per_panel: float
    print_stencil: float
    place_handling: float
    place_fine_pitch: float
    place_rates: dict[str, float]
    oven_total: float
    aoi_base: float
    aoi_per_part: float
    transport_by_load: list[float]
    manual_qc_per_board: float
    manual_tht_per_part: float
    manual_rework: float
    pack_minutes: float
    defect_coef: list[float]  # rate per board = c0 + c1 * parts + c2 * fine pitch
    sigma: dict[str, float]  # lognormal noise per station
    mtbf_minutes: dict[str, float]
    mttr_log: dict[str, tuple[float, float]]  # lognormal (mu, sigma) of repair minutes
    supplier_in_stock: dict[str, float]  # per sku, smoothed
    supplier_in_stock_prior: float
    backorder_days: list[int]
    fitted_on_days: int
    n_ops: int
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["supplier_in_stock"] = {k: round(v, 3) for k, v in sorted(self.supplier_in_stock.items())[:20]}
        return d

    # ------------------------------------------------------------ predictions for planning
    def placer_minutes(self, parts: float, fine: float, rate: float | None = None) -> float:
        r = rate or float(np.mean(list(self.place_rates.values())))
        return (self.place_handling + parts / r) * (1 + self.place_fine_pitch * fine)

    def job_minutes(self, design) -> dict[str, float]:
        """Expected minutes of work a job puts on each resource group."""
        panels = design.panels
        placer = 0.0
        for side in range(1, design.sides + 1):
            placer += panels * self.placer_minutes(
                design.parts_on_side(side) * design.boards_per_panel, design.fine_pitch_share
            )
        p_def = max(
            0.0,
            self.defect_coef[0]
            + self.defect_coef[1] * design.parts_per_board
            + self.defect_coef[2] * design.fine_pitch_share,
        )
        manual = (
            design.boards
            * (self.manual_qc_per_board + self.manual_tht_per_part * design.tht_parts + self.manual_rework * p_def)
            + self.pack_minutes
        )
        return {
            "kitting": self.kit_per_job + self.kit_per_unique * design.unique_parts + panels * self.kit_per_panel,
            "printer": design.sides * (panels * self.print_per_panel + self.print_stencil),
            "placer": placer,
            "oven": design.sides * panels * self.oven_total / settings()["factory"]["oven_concurrency"],
            "aoi": panels * self.aoi_base + self.aoi_per_part * design.parts_per_board * design.boards,
            "manual": manual,
        }


def order_table(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    q = frames["quotes"]
    won = q[q["won"]].copy()
    won["order_id"] = "O" + won["request_id"].str[1:]
    return won.set_index("order_id")


def _ols(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.linalg.lstsq(X, y, rcond=None)[0]


def _sigma(t: np.ndarray, pred: np.ndarray) -> float:
    ok = (t > 0) & (pred > 0)
    return float(np.std(np.log(t[ok] / pred[ok]))) if ok.sum() > 10 else 0.15


def calibrate(frames: dict[str, pd.DataFrame], days: int) -> Calibration:
    s = settings()["factory"]
    ops = frames["ops"].copy()
    ops["t"] = ops["end"] - ops["start"]
    orders = order_table(frames)
    ops = ops.join(
        orders[
            [
                "boards",
                "parts_per_board",
                "unique_parts",
                "two_sided",
                "second_side_share",
                "boards_per_panel",
                "fine_pitch_share",
            ]
        ],
        on="order_id",
    )
    ops["panel_idx"] = ops["panel_id"].str[-3:].astype(int)
    panels = np.ceil(ops["boards"] / ops["boards_per_panel"])
    last = ops["panel_idx"] == panels - 1
    ops["boards_in_panel"] = np.where(
        last, ops["boards"] - ops["boards_per_panel"] * (panels - 1), ops["boards_per_panel"]
    )
    sigma: dict[str, float] = {}
    notes: list[str] = []

    # kitting: the first panel of a job pulls every unique part
    k = ops[ops.station == "kitting"]
    first = (k.panel_idx == 0).astype(float).to_numpy()
    X = np.column_stack([np.ones(len(k)), first, first * k.unique_parts.to_numpy()])
    kit = _ols(X, k.t.to_numpy())
    sigma["kitting"] = _sigma(k.t.to_numpy(), X @ kit)

    # printers: a stencil change when the job or side differs from the last one on that printer
    p = ops[ops.station == "printer"].sort_values(["machine", "start"])
    key = p.order_id + "/" + p.side.astype(str)
    stencil = (key != key.groupby(p.machine).shift()).astype(float).to_numpy()
    X = np.column_stack([np.ones(len(p)), stencil])
    pr = _ols(X, p.t.to_numpy())
    sigma["printer"] = _sigma(p.t.to_numpy(), X @ pr)

    # placers: (handling + parts / machine rate) * (1 + k * fine pitch), fitted in logs
    pl = ops[ops.station == "placer"]
    second = np.round(pl.parts_per_board * pl.second_side_share)
    parts_side = np.where(
        ~pl.two_sided.astype(bool), pl.parts_per_board, np.where(pl.side == 2, second, pl.parts_per_board - second)
    )
    parts = (parts_side * pl.boards_in_panel).to_numpy(float)
    fine = pl.fine_pitch_share.to_numpy(float)
    machines = sorted(pl.machine.unique())
    m_idx = pl.machine.map({m: i for i, m in enumerate(machines)}).to_numpy()
    t = pl.t.to_numpy()

    def resid(theta: np.ndarray) -> np.ndarray:
        h, kk, rates = theta[0], theta[1], theta[2:]
        pred = (h + parts / rates[m_idx]) * (1 + kk * fine)
        return np.log(t) - np.log(np.maximum(pred, 1e-6))

    fit = least_squares(
        resid,
        x0=np.r_[1.0, 0.3, np.full(len(machines), 20.0)],
        bounds=(np.r_[0, 0, np.full(len(machines), 1.0)], np.r_[10, 5, np.full(len(machines), 200)]),
    )
    sigma["placer"] = float(np.std(fit.fun))
    place_rates = {m: round(float(r), 3) for m, r in zip(machines, fit.x[2:])}

    # ovens: a conveyor; one panel enters every dwell / concurrency minutes
    oven_total = float(ops[ops.station == "oven"].t.median())

    a = ops[ops.station == "aoi"]
    X = np.column_stack([np.ones(len(a)), (a.parts_per_board * a.boards_in_panel).to_numpy(float)])
    aoi = _ols(X, a.t.to_numpy())
    sigma["aoi"] = _sigma(a.t.to_numpy(), X @ aoi)

    # movers: transport time by how busy the fleet is
    tr = ops[ops.station == "transport"]
    load = (tr.busy_movers / tr.fleet).clip(0, 1)
    by_load = []
    overall = float(tr.t.mean())
    for lo, hi in zip(TRANSPORT_BINS[:-1], TRANSPORT_BINS[1:]):
        sel = tr.t[(load >= lo) & (load < hi)]
        by_load.append(round(float(sel.mean()) if len(sel) > 20 else overall, 4))

    # the manual cell
    man = frames["manual"].join(orders[["tht_parts", "parts_per_board", "fine_pitch_share"]], on="order_id")
    man["t"] = man["end"] - man["start"]
    X = np.column_stack([man.boards, man.tht_parts * man.boards, man.defects]).astype(float)
    mc = _ols(X, man.t.to_numpy())
    sigma["manual"] = _sigma(man.t.to_numpy(), X @ mc)
    Xd = np.column_stack([np.ones(len(man)), man.parts_per_board, man.fine_pitch_share]).astype(float)
    w = np.sqrt(man.boards.to_numpy(float))
    dc = _ols(Xd * w[:, None], (man.defects / man.boards).to_numpy(float) * w)
    ship = frames.get("shipments", pd.DataFrame())
    pack = 9.0
    if len(ship):
        last_manual = man.groupby("order_id")["end"].max()
        gap = (ship.set_index("order_id")["completed_minute"] - last_manual).dropna()
        gap = gap[(gap > 0) & (gap < 240)]
        if len(gap) > 20:
            pack = float(gap.median())

    # failures and repairs per machine kind
    dt = frames.get("downtime", pd.DataFrame(columns=["kind", "repair_start", "repair_end"]))
    mtbf, mttr = {}, {}
    span = days * s["day_minutes"]
    for kind, n in s["machines"].items():
        rows = dt[dt.kind == kind]
        failures = len(rows)
        mtbf[kind] = float(n * span / max(failures, 0.5))
        rep = (rows.repair_end - rows.repair_start).to_numpy(float)
        if len(rep) >= 5:
            mttr[kind] = (float(np.log(rep).mean()), float(np.log(rep).std()))
        else:
            mttr[kind] = (np.log(30.0), 0.6)
            notes.append(f"{kind}: only {len(rep)} repairs seen; repair time uses a prior")

    # suppliers: did the parts come in the standard days, or were they backordered?
    rec = frames.get("receipts", pd.DataFrame(columns=["sku", "ordered_day", "day"]))
    in_stock, backorders, prior = {}, [], 0.98
    if len(rec):
        rec = rec.assign(lead=rec.day - rec.ordered_day)
        standard = int(rec.lead.min())
        hit = rec.lead <= standard
        prior = float(hit.mean())
        backorders = sorted(int(x) for x in rec.lead[~hit])
        grp = rec.assign(hit=hit).groupby("sku")["hit"].agg(["sum", "count"])
        # empirical Bayes: a Beta prior fitted across parts with a few orders each, then each part's
        # own record moves it; parts with long records stand on their own evidence
        rich = grp[grp["count"] >= 3]
        rates = (rich["sum"] / rich["count"]).to_numpy(float)
        m = float(np.average(rates, weights=rich["count"])) if len(rates) else prior
        v = float(np.var(rates)) if len(rates) > 5 else m * (1 - m) / 20
        strength = max(2.0, min(200.0, m * (1 - m) / max(v, 1e-6) - 1))
        a0, b0 = m * strength, (1 - m) * strength
        prior = float(np.median(rates)) if len(rates) else prior
        in_stock = {sku: float((r["sum"] + a0) / (r["count"] + a0 + b0)) for sku, r in grp.iterrows()}

    return Calibration(
        kit_per_panel=float(kit[0]),
        kit_per_job=float(kit[1]),
        kit_per_unique=float(kit[2]),
        print_per_panel=float(pr[0]),
        print_stencil=float(pr[1]),
        place_handling=float(fit.x[0]),
        place_fine_pitch=float(fit.x[1]),
        place_rates=place_rates,
        oven_total=oven_total,
        aoi_base=float(aoi[0]),
        aoi_per_part=float(aoi[1]),
        transport_by_load=by_load,
        manual_qc_per_board=float(mc[0]),
        manual_tht_per_part=float(mc[1]),
        manual_rework=float(mc[2]),
        pack_minutes=pack,
        defect_coef=[float(x) for x in dc],
        sigma=sigma,
        mtbf_minutes=mtbf,
        mttr_log=mttr,
        supplier_in_stock=in_stock,
        supplier_in_stock_prior=prior,
        backorder_days=backorders or [20],
        fitted_on_days=days,
        n_ops=len(ops),
        notes=notes,
    )
