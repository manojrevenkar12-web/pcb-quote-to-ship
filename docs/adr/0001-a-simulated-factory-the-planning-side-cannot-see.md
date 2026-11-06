# 0001. A simulated factory the planning side cannot see

## Status
Accepted.

## Context
Nobody outside an on-demand PCB factory has its machine logs, its quote history or its supplier
records. Without them, a planning system can only be checked against data it generated itself, which
proves nothing: a model scored on its own assumptions always looks right.

## Decision
The repository holds two halves that never share code or settings.

- `src/q2s/world` is the truth: a discrete-event model of a factory with placers, printers, conveyor
  ovens, inspection, a mover fleet that gets congested, a manual cell that works in shifts, machine
  breakdowns, customers with a hidden price and lead-time response, and distributors that sometimes
  run out. Its settings are in `config/world.yaml`.
- `src/q2s/data`, `src/q2s/policy` and `src/q2s/pipeline` are the planning system. They see only what
  a real factory would log (operation start and end times, quotes and whether they were won,
  purchases and receipts) and the business settings in `config/policy.yaml`.

`tests/test_isolation.py` fails the build if the planning side imports the world or the evaluation,
or reads `config/world.yaml`. Every stack is run on the same requests, failures, supplier delays and
customer decisions (common random numbers, one seeded stream per decision), so differences between
stacks are differences in decisions, not luck.

## Consequences
- The calibration has to recover process times, failure rates and supplier lead times from logs,
  the way it would in a real factory, and the machine study can check the twin against the truth.
- Results are only as good as the world is plausible. The world is deliberately rougher than the
  twin (noise on every operation, transport congestion, breakdowns), and the held-out scenarios
  were written blind so the planning code was never tuned on them.
- Moving to a real factory means replacing `src/q2s/world` with the factory's own log export into
  `landing/`; nothing on the planning side changes.
