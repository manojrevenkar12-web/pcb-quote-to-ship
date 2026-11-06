-- every won quote is an order; its design attributes come from the quote
select
    'O' || substr(request_id, 2) as order_id,
    request_id, day as quote_day, segment, boards, parts_per_board, unique_parts, two_sided,
    second_side_share, boards_per_panel, tht_parts, fine_pitch_share, parts_cost, price, promise_days,
    explore, ceil(boards / boards_per_panel)::int as panels
from {{ ref('stg_quotes') }}
where won
