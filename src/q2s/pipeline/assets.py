"""The morning plan as Dagster assets.

landing (Parquet) -> warehouse (dbt build: staging, marts, tests) -> calibration -> release plan ->
outlook -> quotes, purchase orders -> decisions (written back for the ERP and the MES), and a check
of the promises that came due. Scheduled at 05:30 on working days, before the first shift.
"""

import json
from pathlib import Path
from typing import Any

from dagster import (
    AssetExecutionContext,
    ConfigurableResource,
    Definitions,
    ScheduleDefinition,
    asset,
    define_asset_job,
)

from q2s.data.model import Observation, PurchaseOrder, Quote
from q2s.pipeline.landing import load_snapshot
from q2s.pipeline.warehouse import dbt_build, frames_from_warehouse, mart
from q2s.policy.buy import BaseStock
from q2s.policy.calibrate import Calibration, calibrate
from q2s.policy.outlook import Outlook
from q2s.policy.outlook import outlook as make_outlook
from q2s.policy.quote import Quoter, capacity_from_history, fit_win_model, recent_flow
from q2s.policy.schedule import cpsat
from q2s.policy.settings import settings


class Paths(ConfigurableResource):
    landing: str
    warehouse: str
    seed: int = 0


@asset(description="dbt build over the landed logs: staging views, marts and their tests")
def warehouse(context: AssetExecutionContext, paths: Paths) -> dict[str, int]:
    out = dbt_build(Path(paths.landing), Path(paths.warehouse))
    context.add_output_metadata(out)
    return out


@asset(description="the ERP snapshot at 06:00: backlog, work in progress, stock, open purchases")
def observation(paths: Paths) -> Observation:
    return load_snapshot(Path(paths.landing))


@asset(description="process times, failures, congestion, manual cell and supplier reliability, from the logs")
def calibration(
    context: AssetExecutionContext, warehouse: dict[str, int], observation: Observation, paths: Paths
) -> Calibration:
    cal = calibrate(frames_from_warehouse(Path(paths.warehouse)), observation.day)
    context.add_output_metadata({"placer_rates": json.dumps(cal.place_rates), "ops": cal.n_ops})
    return cal


@asset(description="release order for every order whose parts are on hand (CP-SAT, warm-started from ATCS)")
def release_plan(calibration: Calibration, observation: Observation) -> list[str]:
    wip = {"placer": 0.0, "manual": 0.0}
    for o in observation.in_progress:
        m = calibration.job_minutes(o.design)
        wip["placer"] += 0.5 * m["placer"]
        wip["manual"] += 0.7 * m["manual"]
    return cpsat(observation, calibration, wip)


@asset(description="twin outlook: ship days, panels per day, free capacity, starve/overload flags")
def outlook(
    context: AssetExecutionContext,
    calibration: Calibration,
    observation: Observation,
    release_plan: list[str],
    paths: Paths,
) -> Outlook:
    frames = frames_from_warehouse(Path(paths.warehouse))
    look = make_outlook(
        observation,
        frames,
        calibration,
        release_plan,
        settings()["quote"]["replications"],
        paths.seed + observation.day,
    )
    context.add_output_metadata(
        {"flags": ",".join(look.flags) or "none", "placer_next2": look.utilisation.get("placer_next2", 0.0)}
    )
    return look


@asset(description="price and promise for every request that came in yesterday")
def quotes(
    context: AssetExecutionContext, calibration: Calibration, observation: Observation, outlook: Outlook, paths: Paths
) -> list[Quote]:
    frames = frames_from_warehouse(Path(paths.warehouse))
    win = fit_win_model(frames["quotes"], calibration)
    cap = capacity_from_history(frames, calibration)
    cap.flow_days = recent_flow(frames["shipments"], observation.day)
    out = Quoter(calibration, win, cap, paths.seed + observation.day).quote_all(observation, outlook.spare)
    context.add_output_metadata({"quotes": len(out), "price_slope": round(win.slope_price, 3)})
    return out


@asset(description="purchase orders: order-up-to levels for parts in use, the rest per order")
def purchase_orders(calibration: Calibration, observation: Observation, paths: Paths) -> list[PurchaseOrder]:
    lines = frames_from_warehouse(Path(paths.warehouse))["order_lines"]
    return BaseStock(calibration, lines, observation.catalog, observation.day, paths.seed).purchases(observation)


@asset(description="the morning's decisions, written for the ERP (quotes, purchases) and the MES (release order)")
def decisions(
    release_plan: list[str],
    quotes: list[Quote],
    purchase_orders: list[PurchaseOrder],
    observation: Observation,
    paths: Paths,
) -> str:
    path = Path(paths.landing) / "decisions" / f"day={observation.day:04d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "day": observation.day,
                "release": release_plan,
                "quotes": [q.__dict__ for q in quotes],
                "purchases": [p.__dict__ for p in purchase_orders],
            }
        )
    )
    return str(path)


@asset(description="promises that came due in the last ten days, checked against what shipped")
def promise_check(
    context: AssetExecutionContext, warehouse: dict[str, int], observation: Observation, paths: Paths
) -> dict[str, Any]:
    hits = mart(Path(paths.warehouse), "fct_promise_hit_daily")
    recent = hits[(hits.day >= observation.day - 10) & (hits.day < observation.day)]
    due = int(recent.orders_due.sum())
    out = {"orders_due": due, "hit_rate": round(float((recent.hit_rate * recent.orders_due).sum() / max(due, 1)), 3)}
    context.add_output_metadata(out)
    return out


morning_plan = define_asset_job("morning_plan", selection="*")

defs = Definitions(
    assets=[
        warehouse,
        observation,
        calibration,
        release_plan,
        outlook,
        quotes,
        purchase_orders,
        decisions,
        promise_check,
    ],
    jobs=[morning_plan],
    schedules=[
        ScheduleDefinition(job=morning_plan, cron_schedule="30 5 * * 1-5", execution_timezone="America/New_York")
    ],
    resources={"paths": Paths(landing="data/landing", warehouse="data/warehouse.duckdb")},
)
