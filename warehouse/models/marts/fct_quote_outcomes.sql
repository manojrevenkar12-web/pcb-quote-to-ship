-- quotes and whether they were won; `explore` marks prices randomised on purpose
select request_id, day, segment, price, promise_days, explore, won, boards, parts_per_board, parts_cost
from {{ ref('stg_quotes') }}
where price is not null
