-- one row per inventory record, as the factory's systems logged it
select * from {{ landing('inventory') }}
