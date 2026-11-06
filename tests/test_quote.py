"""Price and promise: learned response slopes down, promises respect capacity, prices stay in range."""

import numpy as np

from q2s.policy.quote import Quoter, capacity_from_history, fit_win_model, list_price
from q2s.policy.settings import settings


def _quoter(history_world, cal):
    frames = history_world.frames()
    win = fit_win_model(frames["quotes"], cal)
    cap = capacity_from_history(frames, cal)
    return Quoter(cal, win, cap, seed=1), win


def test_customers_dislike_higher_prices_and_longer_waits(history_world, cal):
    _, win = _quoter(history_world, cal)
    assert win.slope_price < 0
    assert win.slope_days < 0


def test_less_free_capacity_means_later_promises(history_world, cal):
    q, _ = _quoter(history_world, cal)
    obs = history_world.observe()
    full = {"placer": [5000.0] * 20, "manual": [3500.0] * 20}
    tight = {"placer": [50.0] * 20, "manual": [30.0] * 20}
    roomy = q.quote_all(obs, full, explore=False)
    q2, _ = _quoter(history_world, cal)
    cramped = q2.quote_all(obs, tight, explore=False)
    assert np.mean([x.promise_days for x in cramped]) > np.mean([x.promise_days for x in roomy])


def test_prices_stay_inside_the_explored_range_and_above_cost(history_world, cal):
    q, win = _quoter(history_world, cal)
    obs = history_world.observe()
    quotes = {
        x.request_id: x for x in q.quote_all(obs, {"placer": [5000.0] * 20, "manual": [3500.0] * 20}, explore=False)
    }
    margin = settings()["quote"]["trust_margin"]
    for req in obs.requests:
        ratio = quotes[req.request_id].price / list_price(req.design, obs.catalog)
        assert win.markup_range[0] / margin - 1e-6 <= ratio <= win.markup_range[1] * margin + 1e-6


def test_buffer_days_lengthen_promises(history_world, cal):
    obs = history_world.observe()
    free = {"placer": [5000.0] * 20, "manual": [3500.0] * 20}
    q0, _ = _quoter(history_world, cal)
    q2, _ = _quoter(history_world, cal)
    a = np.mean([x.promise_days for x in q0.quote_all(obs, free, explore=False)])
    b = np.mean([x.promise_days for x in q2.quote_all(obs, free, explore=False, buffer_days=2)])
    assert b > a
