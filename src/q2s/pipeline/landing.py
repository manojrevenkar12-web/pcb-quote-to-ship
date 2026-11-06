"""Lands the factory's logs as Parquet, one file per table per day (what the MES, ERP and quoting
tool would export), plus the morning's ERP snapshot the planning assets start from."""

from __future__ import annotations

import pickle
from pathlib import Path

import pandas as pd

from q2s.data.logs import LogStore
from q2s.data.model import Observation

TABLES = [
    "ops",
    "manual",
    "downtime",
    "quotes",
    "shipments",
    "releases",
    "purchases",
    "receipts",
    "order_lines",
    "inventory",
]


class Landing:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.cursor = {t: 0 for t in TABLES}

    def flush(self, store: LogStore, day: int) -> dict[str, int]:
        written = {}
        for t in TABLES:
            rows, self.cursor[t] = store.since(t, self.cursor[t])
            if not rows:
                continue
            folder = self.root / t
            folder.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(rows).to_parquet(folder / f"day={day:04d}.parquet", index=False)
            written[t] = len(rows)
        return written

    def snapshot(self, obs: Observation) -> Path:
        path = self.root / "state" / "observation.pkl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pickle.dumps(obs))
        return path


def load_snapshot(root: Path) -> Observation:
    return pickle.loads((Path(root) / "state" / "observation.pkl").read_bytes())
