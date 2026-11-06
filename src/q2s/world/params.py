"""Loads the world's truth. Only the world and the evaluation may import this module."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORLD_YAML = ROOT / "config" / "world.yaml"


def load_world(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    with open(WORLD_YAML) as f:
        world = yaml.safe_load(f)
    if overrides:
        world = deep_merge(world, overrides)
    return world


def deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out
