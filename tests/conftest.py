import pytest

from q2s.policy.calibrate import calibrate
from q2s.policy.stack import TodayStack
from q2s.world.sim import World

HISTORY = 30


@pytest.fixture(scope="session")
def history_world():
    """Thirty working days of today's process, with some quotes priced at random."""
    w = World(seed=4)
    p = TodayStack(seed=4, explore=True)
    for _ in range(HISTORY):
        w.step(p.decide(w.observe(), w.store))
    return w


@pytest.fixture(scope="session")
def cal(history_world):
    return calibrate(history_world.frames(), HISTORY)
