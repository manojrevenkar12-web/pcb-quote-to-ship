-- each panel passes optical inspection exactly once
select panel_id from {{ ref('fct_station_ops') }} where station = 'aoi' group by panel_id having count(*) <> 1
