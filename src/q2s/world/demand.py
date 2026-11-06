"""Quote requests and how customers decide. The competitor price and the customers' sensitivity
to price and lead time are truth: the decision models only ever see won and lost quotes."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from q2s.data.model import Design, QuoteRequest
from q2s.world.rng import stream, uniform


@dataclass(frozen=True)
class Catalog:
    skus: list[str]
    unit_cost: dict[str, float]
    constrained: frozenset[str]  # truth: distributors are often out of these
    popularity: np.ndarray  # truth: how often designs use each sku


def make_catalog(world: dict[str, Any], seed: int) -> Catalog:
    cfg = world["parts"]
    rng = stream(seed, "catalog")
    n = cfg["catalog_size"]
    skus = [f"C{i:05d}" for i in range(1, n + 1)]
    cost = np.exp(rng.normal(math.log(cfg["unit_cost"]["median"]), cfg["unit_cost"]["sigma"], n))
    ranks = np.arange(1, n + 1)
    pop = ranks ** (-cfg["zipf_exponent"])
    pop = pop / pop.sum()
    n_constrained = int(n * cfg["supplier"]["constrained_share"])
    constrained = rng.choice(n, size=n_constrained, replace=False)
    return Catalog(
        skus=skus,
        unit_cost={s: round(float(c), 4) for s, c in zip(skus, cost)},
        constrained=frozenset(skus[i] for i in constrained),
        popularity=pop,
    )


def _design(world: dict[str, Any], catalog: Catalog, rng: np.random.Generator) -> Design:
    d = world["demand"]
    boards = int(
        np.clip(
            round(math.exp(rng.normal(math.log(d["boards"]["median"]), d["boards"]["sigma"]))), 1, d["boards"]["max"]
        )
    )
    ppb = int(
        np.clip(
            round(math.exp(rng.normal(math.log(d["parts_per_board"]["median"]), d["parts_per_board"]["sigma"]))),
            d["parts_per_board"]["min"],
            d["parts_per_board"]["max"],
        )
    )
    unique = int(np.clip(round(0.55 * ppb ** d["unique_part_exponent"]), 3, ppb))
    idx = rng.choice(len(catalog.skus), size=unique, replace=False, p=catalog.popularity)
    extra = rng.multinomial(ppb - unique, np.full(unique, 1 / unique)) if ppb > unique else np.zeros(unique, int)
    bom = tuple(sorted((catalog.skus[i], int(1 + e)) for i, e in zip(idx, extra)))
    two_sided = bool(rng.random() < d["two_sided_share"])
    choices, weights = zip(*sorted(d["boards_per_panel"].items()))
    per_panel = int(rng.choice(np.array(choices, dtype=int), p=np.array(weights) / sum(weights)))
    per_panel = min(per_panel, boards)
    tht = int(rng.integers(d["tht_parts"]["min"], d["tht_parts"]["max"] + 1)) if rng.random() < d["tht_share"] else 0
    fine = float(np.clip(rng.beta(d["fine_pitch"]["alpha"], d["fine_pitch"]["beta"]), 0, 1))
    return Design(
        boards=boards,
        parts_per_board=ppb,
        unique_parts=unique,
        two_sided=two_sided,
        second_side_share=d["second_side_part_share"] if two_sided else 0.0,
        boards_per_panel=per_panel,
        tht_parts=tht,
        fine_pitch_share=round(fine, 3),
        bom=bom,
    )


def requests_for_day(
    world: dict[str, Any], catalog: Catalog, seed: int, day: int, rate_multiplier: float = 1.0
) -> list[QuoteRequest]:
    rng = stream(seed, "requests", day)
    n = int(rng.poisson(world["demand"]["quote_requests_per_day"] * rate_multiplier))
    segs = world["demand"]["segments"]
    names = sorted(segs)
    shares = np.array([segs[s]["share"] for s in names])
    out = []
    for i in range(n):
        r = stream(seed, "design", day, i)
        segment = str(r.choice(names, p=shares / shares.sum()))
        out.append(QuoteRequest(f"Q{day:04d}-{i:03d}", day, segment, _design(world, catalog, r)))
    return out


def competitor(world: dict[str, Any], catalog: Catalog, seed: int, req: QuoteRequest) -> tuple[float, int]:
    """Truth: the price and lead time the customer could get elsewhere."""
    d = req.design
    parts = d.boards * sum(catalog.unit_cost[s] * u for s, u in d.bom)
    assembly = 40 + d.boards * (0.025 * d.parts_per_board + 2.0 + 0.3 * d.tht_parts)
    noise = math.exp(stream(seed, "competitor", req.request_id).normal(0, world["demand"]["competitor_noise_sigma"]))
    seg = world["demand"]["segments"][req.segment]
    return world["demand"]["competitor_markup"] * (parts + assembly) * noise, seg["competitor_days"]


def win_probability(
    world: dict[str, Any], catalog: Catalog, seed: int, req: QuoteRequest, price: float, promise_days: int
) -> float:
    seg = world["demand"]["segments"][req.segment]
    comp_price, comp_days = competitor(world, catalog, seed, req)
    gap_days = float(np.clip(promise_days - comp_days, -6, 12))
    z = seg["alpha"] - seg["beta"] * math.log(price / comp_price) - seg["gamma"] * gap_days
    return 1 / (1 + math.exp(-z))


def accepts(
    world: dict[str, Any], catalog: Catalog, seed: int, req: QuoteRequest, price: float, promise_days: int
) -> bool:
    return uniform(seed, "accept", req.request_id) < win_probability(world, catalog, seed, req, price, promise_days)
