"""Two independently written models of the release-ordering problem must agree.

CP-SAT (schedule.solve_cpsat) against the MiniZinc model (models/release.mzn) solved by a MIP back end
(COIN-BC) and a CP back end (Gecode). Where both prove optimality, the objectives must be equal; a
difference means one of the models says something the other does not.
"""

import random

import pytest

from q2s.data.model import Observation
from q2s.policy.release_mzn import available, solve_minizinc
from q2s.policy.schedule import ReleaseInstance, ReleaseJob, releasable, release_instance, solve_cpsat

pytestmark = [pytest.mark.minizinc, pytest.mark.skipif(not available(), reason="MiniZinc not installed")]


def random_instance(seed: int, n: int) -> ReleaseInstance:
    r = random.Random(seed)
    jobs = []
    for i in range(n):
        jobs.append(
            ReleaseJob(
                order_id=f"o{i}",
                d_pl=r.randint(20, 300),
                k_pl=r.randint(1, 4),
                d_op=r.randint(30, 400),
                k_op=r.randint(1, 4),
                due=r.randint(200, 1500),
                weight=r.randint(1, 40),
            )
        )
    horizon = sum(j.d_pl + j.d_op for j in jobs) + 2000
    return ReleaseInstance(tuple(jobs), 4, 4, r.choice([0, 60]), r.choice([0, 90]), horizon)


@pytest.mark.parametrize("seed", range(8))
def test_cpsat_and_mip_agree_on_the_optimum(seed):
    inst = random_instance(seed, 5 + seed % 2)
    _, cp_obj, cp_opt = solve_cpsat(inst, seconds=30)
    _, mip_obj, mip_opt = solve_minizinc(inst, "cbc", seconds=60)
    assert cp_opt and mip_opt
    assert cp_obj == mip_obj


@pytest.mark.parametrize("seed", range(3))
def test_cpsat_and_gecode_agree_on_the_optimum(seed):
    inst = random_instance(100 + seed, 5)
    _, cp_obj, _ = solve_cpsat(inst, seconds=30)
    _, cp2_obj, opt = solve_minizinc(inst, "gecode", seconds=60)
    assert opt and cp_obj == cp2_obj


def test_models_agree_on_a_morning_from_the_simulated_factory(history_world, cal):
    obs = history_world.observe()
    ready = releasable(obs)[:5]
    if len(ready) < 3:
        pytest.skip("too few releasable orders on this morning")
    trial = Observation(obs.day, [], ready, [], obs.inventory, [], obs.catalog)
    inst = release_instance(trial, cal)
    _, cp_obj, cp_opt = solve_cpsat(inst, seconds=30)
    _, mip_obj, mip_opt = solve_minizinc(inst, "cbc", seconds=60)
    assert cp_opt and mip_opt and cp_obj == mip_obj
