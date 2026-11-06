# Writing a held-out scenario

A scenario is a YAML file describing something that happens to the factory during a five-week test
period. It is written by someone who has not seen the planning code, from this page only, and it is
run once: its first result is recorded in `reports/heldout/<set>/first_run.json` and never
rewritten. If anyone studies why a held-out set went wrong, the set becomes a regression set
(write `regression` into `scenarios/heldout/<set>/STATUS`) and a new set has to be written.

## The factory

An on-demand circuit-board factory. Customers upload a design and get a price and a promised ship
date; about half accept. Accepted orders wait for their parts, then every panel rides a mover
through kitting, paste printing, pick-and-place and reflow (once per side), and optical inspection,
then goes to a manual cell for through-hole parts, rework and packing. It has two kitting robots,
two printers, four pick-and-place robots (`placer-1` and `placer-2` are older and slower,
`placer-3` and `placer-4` faster), two reflow ovens (`oven-1`, `oven-2`), two inspection machines
(`aoi-1`, `aoi-2`), 34 movers and four manual operators per shift. About 74 quote requests arrive
per working day. Parts come from distributors; usually they arrive in two working days, sometimes a
distributor is out and the wait is two to six weeks.

## Format

Days count from the first test day (day 0) to day 34. Any combination of these keys:

```yaml
name: short name
description: >
  One or two sentences: what happens and why it would matter to the factory.
seed: 301                      # any integer from 300 to 999, different for each file
demand:                        # quote requests per day are multiplied by `multiplier`
  - {from_day: 3, to_day: 9, multiplier: 1.4}
machine_outages:               # a machine is taken down for `minutes` from `minute` of `day`
  - {machine: oven-1, day: 12, minute: 600, minutes: 900}
supplier_shocks:               # each part ordered in the window is in stock with probability
  - {from_day: 4, to_day: 8, in_stock_multiplier: 0.9}   # multiplied by this (0.5 to 1.0)
```

Limits: `multiplier` between 0.3 and 2.5; outages up to 7200 minutes each, at most three per file;
machine names exactly as listed above. Keep each scenario plausible for a real factory, and make
each one test something different.
