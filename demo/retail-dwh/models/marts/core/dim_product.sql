select
    md5(cast(product_id as varchar) || cast(valid_from as varchar)) as product_sk,
    cast(product_id as bigint) as product_id,
    sku,
    product_name,
    cast(category_id as bigint) as category_id,
    cast(unit_price as decimal(38,2)) as unit_price,
    cast(valid_from as date) as valid_from,
    cast(valid_to as date) as valid_to,
    cast(is_current as boolean) as is_current
from {{ ref('stg_products') }}
