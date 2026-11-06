select order_id, panel_id, "end" - start as minutes, busy_movers, fleet, busy_movers::double / fleet as fleet_load
from {{ ref('stg_ops') }}
where station = 'transport'
