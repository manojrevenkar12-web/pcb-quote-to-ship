-- one row per order_lines record, as the factory's systems logged it
select * from {{ landing('order_lines') }}
