-- one row per shipped order: how long each stage took and whether the promise held
select s.order_id, s.segment, s.quote_day, s.due_day, s.released_day, s.shipped_day, s.price, s.panels,
       s.released_day - s.quote_day as parts_wait_days,
       s.shipped_day - s.released_day as flow_days,
       s.shipped_day - s.quote_day as lead_days,
       greatest(0, s.shipped_day - s.due_day) as late_days,
       s.shipped_day <= s.due_day as on_time
from {{ ref('stg_shipments') }} s
