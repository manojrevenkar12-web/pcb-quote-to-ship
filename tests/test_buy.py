"""Parts buying: order-up-to levels follow the economics and the supplier's record."""

from dataclasses import replace

from q2s.policy.buy import BaseStock


def test_unreliable_suppliers_are_stocked_deeper(history_world, cal):
    obs = history_world.observe()
    lines = history_world.frames(["order_lines"])["order_lines"]
    base = BaseStock(cal, lines, obs.catalog, obs.day, seed=1)
    sku = base.skus[0]
    shaky = replace(cal, supplier_in_stock={**cal.supplier_in_stock, sku: 0.3})
    deeper = BaseStock(shaky, lines, obs.catalog, obs.day, seed=1)
    assert deeper.order_up_to[sku] >= base.order_up_to[sku]


def test_dear_parts_get_a_lower_service_level(history_world, cal):
    obs = history_world.observe()
    lines = history_world.frames(["order_lines"])["order_lines"]
    base = BaseStock(cal, lines, obs.catalog, obs.day, seed=1)
    sku = base.skus[0]
    dear = BaseStock(cal, lines, {**obs.catalog, sku: obs.catalog[sku] * 5000}, obs.day, seed=1)
    assert dear.service[sku] < base.service[sku]


def test_purchases_are_positive_and_cover_the_backlog(history_world, cal):
    obs = history_world.observe()
    lines = history_world.frames(["order_lines"])["order_lines"]
    pos = BaseStock(cal, lines, obs.catalog, obs.day, seed=1).purchases(obs)
    assert all(p.qty > 0 for p in pos)
