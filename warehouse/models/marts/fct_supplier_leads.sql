-- how long each purchase took to arrive, per part
select r.sku, r.ordered_day, r.day as received_day, r.day - r.ordered_day as lead_days, r.qty
from {{ ref('stg_receipts') }} r
