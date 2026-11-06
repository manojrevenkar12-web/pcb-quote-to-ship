-- every operation, transport and repair takes zero or more minutes
select 'op' as kind, panel_id as id from {{ ref('fct_station_ops') }} where minutes < 0 or wait_minutes < 0
union all
select 'transport', panel_id from {{ ref('fct_transport') }} where minutes < 0
union all
select 'repair', machine from {{ ref('fct_downtime') }} where repair_minutes < 0
