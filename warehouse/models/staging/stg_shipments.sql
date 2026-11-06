-- one row per shipments record, as the factory's systems logged it
select * from {{ landing('shipments') }}
