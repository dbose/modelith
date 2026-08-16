select
    md5(cast(order_line_id as varchar)) as order_line_hashkey,
    md5(cast(customer_id as varchar)) as customer_hashkey,
    md5(cast(product_id as varchar)) as product_hashkey,
    cast(order_ts as timestamp) as load_dts
from {{ ref('int_order_enriched') }}
