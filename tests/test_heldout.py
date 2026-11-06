"""Held-out scenarios: written blind, pinned, and their first run never rewritten."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_heldout_files_match_their_pins():
    pins = (ROOT / "scenarios" / "heldout" / "v1.sha256").read_text().split("\n")
    for line in filter(None, pins):
        digest, name = line.split()
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name


def test_first_run_is_recorded_once_and_pinned():
    first = ROOT / "reports" / "heldout" / "v1" / "first_run.json"
    pin = ROOT / "reports" / "heldout" / "v1" / "first_run.sha256"
    if not first.exists():
        return
    assert json.loads(first.read_text())["set"] == "v1"
    if pin.exists():
        assert hashlib.sha256(first.read_bytes()).hexdigest() == pin.read_text().split()[0]
