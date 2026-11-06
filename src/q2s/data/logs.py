"""The landing zone: append-only tables of what the factory's systems record.

The simulated world writes here; the planning systems read from here. In production these rows
would land in the warehouse from the MES, the ERP and the quoting tool (see warehouse/)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import pandas as pd


class LogStore:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def append(self, table: str, row: dict[str, Any]) -> None:
        self.tables[table].append(row)

    def frame(self, table: str) -> pd.DataFrame:
        return pd.DataFrame(self.tables.get(table, []))

    def frames(self, tables: list[str] | None = None) -> dict[str, pd.DataFrame]:
        names = tables or list(self.tables)
        return {t: self.frame(t) for t in names}

    def since(self, table: str, cursor: int) -> tuple[list[dict[str, Any]], int]:
        rows = self.tables.get(table, [])
        return rows[cursor:], len(rows)
