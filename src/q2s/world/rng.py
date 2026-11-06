"""Random streams keyed by what they are for, so runs share randomness (common random numbers).

Two policies run on the same seed see the same quote requests, the same machine failures, the same
supplier delays and the same customer decisions, whatever they decide. Only the consequences of
their decisions differ, so a difference in outcome is caused by the decisions.
"""

from __future__ import annotations

import hashlib

import numpy as np


def stream(seed: int, *key: object) -> np.random.Generator:
    """A generator that depends only on the seed and the key, never on call order."""
    text = "|".join([str(seed), *map(str, key)])
    digest = hashlib.blake2b(text.encode(), digest_size=8).digest()
    return np.random.default_rng(int.from_bytes(digest, "little"))


def uniform(seed: int, *key: object) -> float:
    return float(stream(seed, *key).random())
