select
    ol.order_line_id,
    o.order_id,
    o.customer_id,
    o.store_id,
    ol.product_id,
    cast(ol.quantity as integer) as quantity,
    cast(ol.line_amount as decimal(38,2)) as line_amount,
    cast(o.order_ts as timestamp) as order_ts
from {{ ref('stg_order_lines') }} ol
join {{ ref('stg_orders') }} o on ol.order_id = o.order_id
