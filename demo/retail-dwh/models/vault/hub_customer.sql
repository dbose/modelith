select
    md5(cast(customer_id as varchar)) as customer_hashkey,
    cast(customer_id as bigint) as customer_id,
    cast(valid_from as timestamp) as load_dts,
    'raw_customers' as record_source
from {{ ref('stg_customers') }}
where is_current = 'true'
