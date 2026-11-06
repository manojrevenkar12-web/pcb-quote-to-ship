# pcb-quote-to-ship

**Operations research for an on-demand PCB assembly factory: what to quote, what to promise, what to
release onto the line, which parts to stock, and what the next machine is worth. Every decision is
learned from the factory's own logs and measured against a simulated factory the models cannot see.**

The setting is a high-mix, low-volume line in the style of a robotic cell grid: kitting, paste
printers, four placers of two speeds, conveyor ovens, optical inspection, a fleet of movers that
congests, a manual cell for through-hole work, rework, QC and packing that works in shifts, machine
breakdowns, distributors that sometimes run out, and customers who weigh price against lead time.
About 74 quote requests a day, 2,500 parts in the catalogue.

Today's process is the baseline: price is a markup on a rule-of-thumb cost, every order is promised
a standard five days (eight when parts are missing), parts are bought per order, and work is
released first come, first served. The planning system replaces each of those decisions:

| Decision | Today | The system |
|---|---|---|
| Price | 1.8 × rule-of-thumb cost | Expected margin over a win model learned from randomised quotes only ([ADR 0002](docs/adr/0002-price-response-from-randomised-quotes.md)), inside the price range customers have seen |
| Promise | 5 days, +3 if parts are missing | First day the twin has spare capacity, plus recent flow time, plus a closed-loop buffer; parts risk priced as expected late credits ([ADR 0003](docs/adr/0003-promise-dates-from-the-twin-with-a-closed-loop-buffer.md)) |
| Release order | First come, first served | CP-SAT on the two bottlenecks (placers, manual cell): value-weighted tardiness, then value-weighted completion |
| Buying | Per order | Order-up-to levels from a critical ratio and bootstrapped lead-time demand |
| Capacity outlook | None | Calibrated SimPy twin, six days ahead, with a conformal band from its own past errors |

## Results

From `q2s evaluate` ([reports/REPORT.md](reports/REPORT.md), [dashboard](reports/dashboard.html)).
Three seeds; each run has 60 days of history under today's process, then 35 test days (the first 10
absorb the backlog and are not scored) and 8 days for orders to finish. Every stack sees the same
requests, breakdowns, supplier delays and customer draws (common random numbers), so differences are
differences in decisions.

| | Today's process | The system | Paired difference (90% interval) |
|---|---:|---:|---:|
| Contribution per day | $24,595 | **$38,620** | +$14,025 (+11,693 to +16,357) |
| Orders shipped on the promised day | 75.1% | **99.3%** | +24.2 pts (+19.4 to +29.0) |
| Lead time, 90th percentile | 15.3 days | **4.0 days** | −11.3 days |
| Late credits paid in the scored window | $299,144 | **$10,717** | |
| Quotes won | 58.3% | 48.2% | −10.2 pts (−11.9 to −8.5) |
| Parts holding cost | $9,262 | $21,714 | +$12,452 |

The system wins fewer quotes, at higher prices, and ships nearly all of them on time. At this load the
extra orders today's process wins are the ones it then ships late: with the system's buying and
release but today's quotes, it wins 70.5% of quotes, and ships 0.4% of orders on time.

**What each part is worth** (the full system minus the system with one part switched back to today):

| Switched back | Contribution per day | On time |
|---|---:|---:|
| Quoting (price and promise) | +$26,761 (+24,669 to +28,854) | +98.8 pts |
| Buying (stock vs per order) | +$7,653 (+6,085 to +9,222) | +21.0 pts |
| Release order: CP-SAT vs first come, first served | +$227 (−104 to +559) | −0.2 pts |
| Release order: CP-SAT vs ATCS | +$587 (−328 to +1,502) | −0.1 pts |

The release solver does not earn its place here. Once the quoting desk only promises what the twin
says the line can make, the order of release barely matters, and a 2-second CP-SAT model proves the
optimum on 2 of 114 mornings. First come, first served would do; the solver stays only because it
costs nothing measurable.

**The twin against the truth.** The same month replayed with one more resource of each kind, in the
simulated factory and in the twin calibrated on the 60 days before. The twin ranks the options with a
rank correlation of +0.96 and picks the same one (+1 placer: $3,062 of late credits saved in truth,
$3,379 predicted). It gets one option backwards: an extra operator per shift saves $445 in truth and
looks like a $605 loss in the twin.

**How far the outlook can be trusted.** Panels finished per day: 6% error for today, 9% tomorrow,
17 to 32% two to five days out, biased low. The twin's own 80% band covers 10 to 43% of outcomes;
the conformal band built from its past errors covers 80% for today and only about half of outcomes
further out. Ship day of every order in progress: within one day 77% of the time; the predicted p90
day covers 87% (5,802 predictions).

**Held-out scenarios.** Three scenarios written by an agent that saw only
[the writing guide](scenarios/WRITING_SCENARIOS.md), pinned by hash, run once, and recorded with the
commit ([first_run.json](reports/heldout/v1/first_run.json), pinned by
[first_run.sha256](reports/heldout/v1/first_run.sha256)):

| Scenario | Contribution per day | On time |
|---|---:|---:|
| Launch surge with a fast placer breakdown | $14,092 → $28,707 | 6.0% → 99.4% |
| Component allocation crunch with panic ordering | $15,076 → $29,633 | 57.4% → 95.9% |
| Overlapping oven outages across a holiday rebound | $23,364 → $29,378 | 58.9% → 98.6% |

## Two models of the release problem

The release problem is built once as plain numbers (`schedule.release_instance`) and solved by two
independently written models: CP-SAT in Python, and [a MiniZinc model](src/q2s/policy/models/release.mzn)
that runs on COIN-BC (MIP), Gecode, Chuffed or HiGHS. Where both prove optimality the objectives must
be equal ([tests](tests/test_release_models.py)).

The first real morning tried made both models infeasible. An order overdue by more than the
planning horizon had no room for its tardiness, so on such mornings CP-SAT returned nothing and the
system quietly fell back to ATCS. The bound was fixed in both models before the recorded run, each
morning's solver outcome is now logged (0 fallbacks in 114 mornings), and a regression test holds it.
How far exact solving goes (`q2s solvers`, four random mornings per size, 15 s each, one thread;
[reports/solvers.json](reports/solvers.json)):

- Every optimum another back end proved equals CP-SAT's: 16 of 16 comparisons (COIN-BC, Gecode, HiGHS).
- CP-SAT proves 5 to 8 orders in well under a second and 3 of 4 instances of 12 orders (median 2.6 s);
  none of 20 within 15 s. COIN-BC and HiGHS prove up to 8 orders, Gecode up to 5, Chuffed none.
- HiGHS 1.9.0 through MiniZinc 2.9.3 stopped early with no solution on feasible instances, here and
  in development; COIN-BC and Gecode agreed with CP-SAT on the same ones. At 20 orders the objective
  outgrows 32-bit integers, which Gecode and Chuffed cannot load.

A real morning has more orders than exact solving reaches in 2 seconds (2 of 114 proven), so in
production the solver returns a good plan, not a proven one; first come, first served does as well.

## Learned from logs only

`src/q2s/world` is the simulated factory (the truth). `src/q2s/data`, `src/q2s/policy` and
`src/q2s/pipeline` are the planning system. They never import the world or read its settings
(`tests/test_isolation.py`); everything they know comes from what a real factory would log
([ADR 0001](docs/adr/0001-a-simulated-factory-the-planning-side-cannot-see.md)):

- **Calibration** recovers each station's process time from operation start and end times (least
  squares on placements and fine-pitch parts for the placers), the movers' slowdown when the floor is
  busy, failure and repair times per machine kind, the manual cell's minutes and defect rate, and how
  often each part is in stock at the distributor, with empirical-Bayes shrinkage for rarely ordered
  parts.
- **The twin** is a SimPy model built from the calibration alone. It runs the open orders forward
  each morning for the capacity outlook, the promise dates and the machine study.
- **The win model** is fitted only on randomised quotes, on the random part of the price.

## Data pipeline

The factory's logs land as Parquet (`landing/`), dbt-duckdb builds staging views and marts with tests
(`warehouse/`: orders, station operations, transport, downtime, order flow, promise hit rate,
supplier lead times, part demand, quote outcomes), and a Dagster job runs the morning plan from the
warehouse: calibration, release order, outlook, quotes, purchase orders and the promise check, on a
weekday 05:30 schedule. The warehouse path must give the same calibration as the raw logs
(`tests/test_pipeline.py`). The dbt profile also has a Postgres target.

```
 factory logs ──▶ landing/ (Parquet) ──▶ dbt: staging ─▶ marts ─▶ tests
                                                        │
                Dagster morning_plan ◀──────────────────┘
                  calibration ─▶ twin outlook ─▶ quotes (price, promise)
                              ─▶ release order (CP-SAT)
                              ─▶ purchase orders (order-up-to)
                              ─▶ promise check (closed-loop buffer)
```

## Limits

- The factory is simulated. The results say the methods work on a factory with this structure and
  this much noise; they are not a claim about any real factory's numbers. The world is deliberately
  rougher than the twin, and the twin still misjudges the value of an extra operator.
- Three seeds give wide intervals; the release-order differences are inside them.
- The outlook's bands under-cover beyond the current day.
- The system holds more stock ($21.7k vs $9.3k of holding cost in the scored window) and wins fewer
  quotes. Whether that trade suits a business that wants growth more than margin is a business choice
  the settings in `config/policy.yaml` express (minimum margin, promise quantile, service levels).
- Release order is the only scheduling decision modelled; the Grid's own routing and the placement
  of feeders are inside the simulated factory, not optimised here.

## Run it

```bash
pip install -e ".[dev]"            # add ",pipeline" for dbt and Dagster, ",solvers" for MiniZinc
pytest -q -m "not pipeline"         # under a minute or two
pytest -q                           # plus the dbt build, warehouse parity and the Dagster job
q2s evaluate --quick                # today vs the system on one seed, about 6 minutes
q2s evaluate                        # every stack, three seeds, machine study, scenarios: about an hour
q2s solvers                         # CP-SAT against MiniZinc back ends by problem size
q2s report                          # reports/REPORT.md and reports/dashboard.html
q2s pipeline --days 25              # simulated days into the landing zone, dbt build, Dagster job
```

The MiniZinc tests need the [MiniZinc bundle](https://www.minizinc.org/) on `PATH` and are skipped
without it; CI installs it.

## Layout

```
config/            world.yaml (the truth), policy.yaml (business settings)
src/q2s/world/     the simulated factory, customers and suppliers
src/q2s/data/      the records a factory keeps; the log store
src/q2s/policy/    calibration, twin, outlook, quoting, release order, buying, the planning stack
src/q2s/pipeline/  landing zone, dbt build, Dagster assets
src/q2s/evaluation/experiments, machine study, scenarios, solver study, reports
warehouse/         dbt project
scenarios/         dev scenarios; held-out sets written blind and pinned
docs/adr/          decisions
```

See [AGENTS.md](AGENTS.md) for the rules that keep the evaluation honest.
