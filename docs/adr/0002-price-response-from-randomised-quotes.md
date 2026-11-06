# 0002. Price response is learned from randomised quotes only

## Status
Accepted.

## Context
To set a price, the quoting desk needs to know how the chance of winning falls as the price rises.
The obvious data is every past quote and whether it was won. That data is confounded. Today's list
price is a markup on a rule-of-thumb cost, and that cost moves with the board: more layers, more
parts, more placements. Customers compare against what a competitor would charge for the same board,
which moves with the same things. A model fitted on all quotes therefore learns the habits of the
pricing formula (which boards it over- or under-prices against the market) and reads them as price
sensitivity. In early versions this gave a slope far too flat, prices drifted up, and the win rate
collapsed in the test window.

## Decision
- A share of quotes (`today.explore_share` in history, `quote.explore_share` afterwards) is priced at
  a random ratio of the list price, drawn from `explore_range`. Lead times are randomised the same way.
- The win model is a logistic regression on log(price / list price), the promised days and the
  customer segment, fitted only on those randomised quotes. The ratio is independent of the board by
  construction, so its coefficient is the customers' response, not the formula's.
- Prices are only set inside the range of ratios customers have actually seen, widened by
  `quote.trust_margin`. Outside it the model is extrapolating.

## Consequences
- About one quote in ten is priced off the optimum on purpose. That costs a little margin and is
  what keeps the model honest as customers and competitors change.
- The model answers "how does the win chance change if this board's price moves", not "what is this
  board worth". The list price still carries the second question.
- If exploration stops, the model goes stale; the weekly recalibration refits on a rolling window
  of randomised quotes only.
