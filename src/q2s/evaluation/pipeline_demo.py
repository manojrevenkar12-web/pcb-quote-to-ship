"""`q2s pipeline`: simulate days into the landing zone, then run tomorrow morning's Dagster job on it,
and feed the decisions back into the simulated factory to close the loop."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from dagster import materialize

from q2s.data.model import Decisions, PurchaseOrder, Quote
from q2s.pipeline import assets as A
from q2s.pipeline.landing import Landing
from q2s.policy.stack import TodayStack
from q2s.world.sim import World  # the simulated factory stands in for the real one here

ROOT = Path(__file__).resolve().parents[3]


def run_pipeline(days: int = 45, seed: int = 7, root: Path | None = None) -> dict[str, Any]:

    base = Path(root or ROOT / "data")
    landing_dir, wh = base / "landing", base / "warehouse.duckdb"
    shutil.rmtree(landing_dir, ignore_errors=True)
    wh.unlink(missing_ok=True)
    world = World(seed)
    landing = Landing(landing_dir)
    today = TodayStack(seed, explore=True)
    for d in range(days):
        world.step(today.decide(world.observe(), world.store))
        landing.flush(world.store, d)
    obs = world.observe()
    landing.snapshot(obs)
    res = materialize(
        [
            A.warehouse,
            A.observation,
            A.calibration,
            A.release_plan,
            A.outlook,
            A.quotes,
            A.purchase_orders,
            A.decisions,
            A.promise_check,
        ],
        resources={"paths": A.Paths(landing=str(landing_dir), warehouse=str(wh), seed=seed)},
    )
    out = {
        k: res.output_for_node(k)
        for k in ("warehouse", "release_plan", "quotes", "purchase_orders", "decisions", "promise_check")
    }
    look = res.output_for_node("outlook")
    quotes: list[Quote] = out["quotes"]
    pos: list[PurchaseOrder] = out["purchase_orders"]
    world.step(Decisions(quotes=quotes, release=out["release_plan"], purchases=pos))
    landing.flush(world.store, days)
    return {
        "success": res.success,
        "dbt": out["warehouse"],
        "released": len(out["release_plan"]),
        "quotes": len(quotes),
        "purchase_orders": len(pos),
        "outlook_flags": look.flags,
        "promise_check": out["promise_check"],
        "decisions_file": out["decisions"],
        "won_after_feedback": int(sum(1 for o in world.orders.values() if o.quote_day == days)),
    }
