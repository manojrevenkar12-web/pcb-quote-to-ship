-- an order cannot ship before it was released, or be released before it was quoted
select order_id from {{ ref('fct_order_flow') }}
where shipped_day < released_day or released_day < quote_day
