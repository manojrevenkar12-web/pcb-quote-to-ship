-- one row per ops record, as the factory's systems logged it
select * from {{ landing('ops') }}
