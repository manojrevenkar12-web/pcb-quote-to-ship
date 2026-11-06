-- one row per releases record, as the factory's systems logged it
select * from {{ landing('releases') }}
