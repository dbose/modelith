select
    order_id,
    customer_id,
    product_id,
    order_date,
    amount
from {{ ref('stg_orders') }}
