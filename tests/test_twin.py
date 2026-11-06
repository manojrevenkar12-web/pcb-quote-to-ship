"""The twin: deterministic per seed, more capacity never makes it slower."""

import numpy as np

from q2s.policy.twin import TwinJob, simulate


def _jobs(history_world, n=60):
    orders = [o for o in history_world.orders.values()][:n]
    return [TwinJob(o.order_id, o.design, i, 0.0, 3 * 1440.0) for i, o in enumerate(orders)]


def test_deterministic(cal, history_world):
    jobs = _jobs(history_world)
    a = simulate(cal, jobs, 0.0, 3, seed=5)
    b = simulate(cal, jobs, 0.0, 3, seed=5)
    assert a.completion == b.completion


def test_more_placers_and_operators_finish_sooner(cal, history_world):
    jobs = _jobs(history_world, 120)
    base = np.mean([np.mean(list(simulate(cal, jobs, 0.0, 6, s).completion.values())) for s in range(3)])
    more = np.mean(
        [
            np.mean(list(simulate(cal, jobs, 0.0, 6, s, {"placer": 2, "operators": 2}).completion.values()))
            for s in range(3)
        ]
    )
    assert more < base


def test_every_job_completes_given_time(cal, history_world):
    jobs = _jobs(history_world, 40)
    res = simulate(cal, jobs, 0.0, 10, seed=1)
    assert set(res.completion) == {j.order_id for j in jobs}
