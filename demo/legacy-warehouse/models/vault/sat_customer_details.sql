select
    md5(cast(customer_id as varchar)) as customer_hashkey,
    name,
    tier,
    valid_from as load_dts
from {{ ref('stg_customers') }}
