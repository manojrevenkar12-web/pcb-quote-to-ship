-- the promise the factory made, checked on the day it came due
select due_day as day, count(*) as orders_due, avg(case when on_time then 1.0 else 0.0 end) as hit_rate,
       sum(late_days) as late_days
from {{ ref('fct_order_flow') }}
group by due_day
