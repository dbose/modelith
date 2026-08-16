select
    md5(cast(product_id as varchar) || cast(valid_from as varchar)) as product_sk,
    product_id,
    sku,
    category,
    list_price,
    valid_from,
    valid_to,
    is_current
from {{ ref('stg_products') }}
