-- one row per manual record, as the factory's systems logged it
select * from {{ landing('manual') }}
