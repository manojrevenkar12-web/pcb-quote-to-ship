-- machine operations with the panel's position in its job and its board count
with ops as (
    select o.*, o."end" - o.start as minutes, o.start - o.arrive as wait_minutes,
           cast(right(panel_id, 3) as int) as panel_idx
    from {{ ref('stg_ops') }} o
    where station <> 'transport'
)
select ops.*, d.panels,
       case when ops.panel_idx = d.panels - 1 then d.boards - d.boards_per_panel * (d.panels - 1)
            else d.boards_per_panel end as boards_in_panel,
       ops.panel_idx = 0 as first_panel
from ops join {{ ref('dim_orders') }} d using (order_id)
