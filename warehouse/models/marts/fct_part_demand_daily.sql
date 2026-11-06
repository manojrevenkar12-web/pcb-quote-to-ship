-- units of each part the day's new orders need (uses per board x boards, plus 3% attrition)
select day, sku, sum(ceil(uses_per_board * boards * 1.03)) as units, count(*) as orders
from {{ ref('stg_order_lines') }}
group by day, sku
