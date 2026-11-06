# 0003. Promise dates from the twin, with a closed-loop buffer

## Status
Accepted.

## Context
Today's process promises a standard lead time, plus three days when parts are missing. It does not
look at the queue. When the factory is overloaded the promises keep being made and orders ship late;
when it is idle, customers who would pay for speed are quoted the same five days.

## Decision
- Each morning the calibrated twin runs the open orders forward with the release order the scheduler
  chose, and reports the spare capacity per day (a low quantile over replications, so a promise is
  made against capacity that is usually there).
- A new order's earliest ship day is the first day with enough spare capacity for its work, plus its
  release-to-ship time (the promise quantile over
  the last week's shipments of orders the same size), plus the parts wait when stock is short (net of
  everything already claimed by the backlog).
- The price for each promised day is chosen together with the price. A short promise is offered when
  the capacity is there and the customer pays for speed.
- At most every three days, once 40 or more orders fell due in the last week, the system checks the
  share of promises kept. Below the target it adds a day to every new promise (up to four); well above
  it, it takes one away. The buffer
  is a control loop, not a constant someone has to tune.
- Parts risk is priced, not padded: the expected late credit from a backorder is added to the cost of
  the quote rather than adding days to every promise.

## Consequences
- Promises lengthen when the factory is loaded and shorten when it is not, which is the point.
- The buffer starts at zero and takes a couple of weeks to settle. The burn-in at the start of the
  test window absorbs this, and the report shows on-time over the scored days only.
- The twin's own uncertainty band is too narrow (it misses some of the world's roughness), so the
  outlook also reports a conformal band built from its own past errors.
