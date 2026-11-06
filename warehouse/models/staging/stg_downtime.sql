-- one row per downtime record, as the factory's systems logged it
select * from {{ landing('downtime') }}
