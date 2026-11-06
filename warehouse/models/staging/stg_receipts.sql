-- one row per receipts record, as the factory's systems logged it
select * from {{ landing('receipts') }}
