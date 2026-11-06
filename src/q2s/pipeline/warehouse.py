"""Builds the dbt warehouse and reads the planning inputs back out of it."""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
DBT_DIR = ROOT / "warehouse"
STAGING = {
    "ops": "stg_ops",
    "manual": "stg_manual",
    "downtime": "stg_downtime",
    "quotes": "stg_quotes",
    "shipments": "stg_shipments",
    "releases": "stg_releases",
    "purchases": "stg_purchases",
    "receipts": "stg_receipts",
    "order_lines": "stg_order_lines",
}


def dbt_build(landing: Path, warehouse: Path, select: str | None = None) -> dict:
    """`dbt build`: staging views, marts and every test. Raises if any model or test fails."""
    from dbt.cli.main import dbtRunner

    os.environ["Q2S_LANDING"] = str(Path(landing).resolve())
    os.environ["Q2S_WAREHOUSE"] = str(Path(warehouse).resolve())
    args = ["build", "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR), "--quiet"]
    if select:
        args += ["--select", select]
    res = dbtRunner().invoke(args)
    if not res.success:
        raise RuntimeError(f"dbt build failed: {res.exception}")
    statuses = [str(r.status) for r in res.result]
    return {"nodes": len(statuses), "passed": sum(s in ("success", "pass") for s in statuses)}


def frames_from_warehouse(warehouse: Path) -> dict[str, pd.DataFrame]:
    con = duckdb.connect(str(warehouse))
    try:
        return {name: con.execute(f"select * from {view}").df() for name, view in STAGING.items()}
    finally:
        con.close()


def mart(warehouse: Path, name: str) -> pd.DataFrame:
    con = duckdb.connect(str(warehouse))
    try:
        return con.execute(f"select * from {name}").df()
    finally:
        con.close()
