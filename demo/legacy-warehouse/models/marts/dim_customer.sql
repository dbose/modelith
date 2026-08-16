select
    md5(cast(customer_id as varchar) || cast(valid_from as varchar)) as customer_sk,
    customer_id,
    name,
    tier,
    valid_from,
    valid_to,
    is_current
from {{ ref('stg_customers') }}
