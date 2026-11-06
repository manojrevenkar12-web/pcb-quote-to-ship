"""Business settings for the decision models (config/policy.yaml). Never the world's truth."""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from q2s.data.model import Design

ROOT = Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def settings() -> dict[str, Any]:
    with open(ROOT / "config" / "policy.yaml") as f:
        return yaml.safe_load(f)


def parts_cost(design: Design, catalog: dict[str, float]) -> float:
    return design.boards * sum(catalog[s] * u for s, u in design.bom)


def parts_needed(design: Design, attrition: float = 0.03) -> dict[str, int]:
    return {sku: math.ceil(uses * design.boards * (1 + attrition)) for sku, uses in design.bom}


def rule_of_thumb_cost(design: Design, catalog: dict[str, float]) -> float:
    r = settings()["today"]["rule_of_thumb"]
    assembly = r["per_order"] + design.boards * (
        r["per_board"] + r["per_board_part"] * design.parts_per_board + r["per_board_tht_part"] * design.tht_parts
    )
    return parts_cost(design, catalog) + assembly
