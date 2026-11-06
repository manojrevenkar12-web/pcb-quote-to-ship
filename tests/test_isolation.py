"""Shipping code (data, policy, pipeline) never sees the simulated world's truth."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "q2s"
SHIPPING = ["data", "policy", "pipeline"]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def test_shipping_code_never_imports_the_world_or_the_evaluation():
    offenders = []
    for pkg in SHIPPING:
        for f in (SRC / pkg).rglob("*.py"):
            bad = {m for m in _imports(f) if m.startswith(("q2s.world", "q2s.evaluation"))}
            if bad:
                offenders.append((str(f.relative_to(SRC)), sorted(bad)))
    assert not offenders


def test_only_the_world_and_the_evaluation_read_world_yaml():
    readers = [str(f.relative_to(SRC)) for f in SRC.rglob("*.py") if "world.yaml" in f.read_text()]
    assert readers and all(r.startswith(("world/", "evaluation/")) for r in readers)


def test_logs_carry_no_truth(history_world):
    """Nothing the planning side reads mentions competitor prices, true rates or supplier classes."""
    forbidden = {"competitor", "comp_price", "alpha", "beta", "gamma", "constrained", "rate", "win_probability"}
    for name, frame in history_world.frames().items():
        assert not (set(frame.columns) & forbidden), name
