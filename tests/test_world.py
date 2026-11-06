"""The simulated factory: conservation, determinism and common random numbers."""

from q2s.data.model import Decisions
from q2s.policy.stack import TodayStack
from q2s.world.sim import World


def test_every_released_panel_passes_each_step_once(history_world):
    f = history_world.frames()
    ops = f["ops"]
    orders = history_world.orders
    done = {o.order_id for o in orders.values() if o.shipped_day is not None}
    for oid in list(done)[:200]:
        d = orders[oid].design
        o = ops[ops.order_id == oid]
        for station in ("kitting", "aoi"):
            assert (o.station == station).sum() == d.panels
        for station in ("printer", "placer", "oven"):
            assert (o.station == station).sum() == d.panels * d.sides
        assert (f["manual"].order_id == oid).sum() == d.panels


def test_no_order_ships_before_release_or_without_its_parts(history_world):
    for o in history_world.orders.values():
        if o.shipped_day is not None:
            assert o.released_day is not None and o.released_day <= o.shipped_day
            assert o.completed_minute is not None


def test_same_seed_same_world():
    def run():
        w = World(seed=9)
        p = TodayStack(seed=9)
        for _ in range(4):
            w.step(p.decide(w.observe(), w.store))
        return w.frames()["shipments"]

    a, b = run(), run()
    assert a.equals(b)


def test_policies_face_the_same_requests_and_failures():
    """Common random numbers: what happens to the factory does not depend on what it decides."""
    w1, w2 = World(seed=12), World(seed=12)
    p = TodayStack(seed=12)
    for _ in range(3):
        w1.step(p.decide(w1.observe(), w1.store))
        w2.observe()
        w2.step(Decisions(quotes=[], release=[], purchases=[]))
    r1 = {r.request_id: r.design for r in w1.requests}
    r2 = {r.request_id: r.design for r in w2.requests}
    assert r1 == r2
    d1 = w1.frames(["downtime"])["downtime"]
    d2 = w2.frames(["downtime"])["downtime"]
    assert list(d1.failed[:3]) == list(d2.failed[:3])
