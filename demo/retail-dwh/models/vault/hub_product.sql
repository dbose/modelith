select
    md5(cast(product_id as varchar)) as product_hashkey,
    cast(product_id as bigint) as product_id,
    cast(valid_from as timestamp) as load_dts,
    'raw_products' as record_source
from {{ ref('stg_products') }}
where is_current = 'true'
