select
    order_line_id,
    order_id,
    customer_id,
    store_id,
    product_id,
    quantity,
    line_amount
from {{ ref('int_order_enriched') }}
