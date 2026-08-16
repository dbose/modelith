select
    md5(cast(order_id as varchar)) as order_hashkey,
    md5(cast(customer_id as varchar)) as customer_hashkey,
    md5(cast(product_id as varchar)) as product_hashkey,
    order_date as load_dts
from {{ ref('stg_orders') }}
