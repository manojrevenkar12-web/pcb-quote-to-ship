"""Release sequencing: only what can be built, every rule a permutation, the solver sensible."""

from dataclasses import replace

from q2s.data.model import Observation
from q2s.policy.schedule import RULES, releasable
from q2s.policy.settings import parts_needed


def _obs(history_world):
    return history_world.observe()


def test_rules_release_only_orders_with_parts(history_world, cal):
    obs = _obs(history_world)
    can = {o.order_id for o in releasable(obs)}
    for name, rule in RULES.items():
        out = rule(obs, cal)
        assert set(out) == can, name
        assert len(out) == len(set(out)), name


def test_releasable_never_overcommits_stock(history_world):
    obs = _obs(history_world)
    inv = dict(obs.inventory)
    for o in releasable(obs):
        for s, q in parts_needed(o.design).items():
            inv[s] -= q
    assert min(inv.values(), default=0) >= 0


def test_cpsat_puts_the_urgent_valuable_order_first(history_world, cal):
    obs = _obs(history_world)
    ready = releasable(obs)[:6]
    if len(ready) < 3:
        return
    late = [replace(o, due_day=obs.day + 12) for o in ready]
    urgent = replace(late[-1], due_day=obs.day, price=late[-1].price * 50)
    trial = Observation(obs.day, [], late[:-1] + [urgent], [], obs.inventory, [], obs.catalog)
    order = RULES["cpsat"](trial, cal)
    assert order[0] == urgent.order_id


def test_cpsat_still_solves_when_orders_are_long_overdue(history_world, cal):
    """Regression: an order overdue by more than the horizon made the model infeasible and the solver
    fell back to ATCS for the whole morning. Found by the second (MiniZinc) model, which agreed."""
    obs = _obs(history_world)
    ready = releasable(obs)[:5]
    if len(ready) < 2:
        return
    overdue = [replace(o, due_day=obs.day - 20) for o in ready]
    trial = Observation(obs.day, [], overdue, [], obs.inventory, [], obs.catalog)
    log: list[dict] = []
    RULES["cpsat"](trial, cal, log=log)
    assert log and log[0]["status"] != "fallback"
