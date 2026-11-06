# Working in this repository

## Fast feedback
- `pytest -q -m "not pipeline"` runs in under a minute; `pytest -q` adds the dbt build, the
  warehouse parity check and the Dagster job (needs `pip install -e ".[pipeline]"`).
- `q2s evaluate --quick` compares today's process with the system on one seed (about 6 minutes).
- `q2s evaluate` runs every stack on three seeds, the machine study and the scenarios (about an hour)
  and rewrites `reports/results.json`; `q2s report` regenerates `reports/REPORT.md` and the dashboard.

## Invariants
- **The planning side never sees the truth.** `src/q2s/data`, `src/q2s/policy` and `src/q2s/pipeline`
  never import `q2s.world` or `q2s.evaluation`, and only the world and the evaluation read
  `config/world.yaml` (`tests/test_isolation.py`). Everything the models know comes from the logs.
- **One definition of how long work takes.** The calibration is the only source of process times;
  the twin, the quoting desk, the scheduler and the buyer all use it. The warehouse path must give the
  same calibration as the raw logs (`tests/test_pipeline.py`).
- **Price response is learned from randomised quotes only**, from the random part of the price
  (price / list price). See `docs/adr/0002-price-response-from-randomised-quotes.md`.
- **Prices stay inside the range customers have seen**, within `quote.trust_margin`.
- **Business settings live in `config/policy.yaml`**, not in code.
- **Reports are generated.** Do not hand-edit `reports/`.

## The held-out rule
`scenarios/heldout/<set>` was written by an agent that read only `scenarios/WRITING_SCENARIOS.md`.
Its files are pinned by `scenarios/heldout/<set>.sha256`; its first run is recorded once in
`reports/heldout/<set>/first_run.json` (pinned by `first_run.sha256`) and never rewritten. Never change
the planning code to do better on a held-out set. If you study a set's failures, write `regression`
into `scenarios/heldout/<set>/STATUS` and have someone who has not seen the code write a new set.
