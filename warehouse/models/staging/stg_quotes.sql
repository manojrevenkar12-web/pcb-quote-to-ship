-- one row per quotes record, as the factory's systems logged it
select * from {{ landing('quotes') }}
