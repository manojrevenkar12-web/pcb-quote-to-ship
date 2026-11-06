-- one row per purchases record, as the factory's systems logged it
select * from {{ landing('purchases') }}
