select
    md5(cast(customer_id as varchar) || cast(valid_from as varchar)) as customer_sk,
    cast(customer_id as bigint) as customer_id,
    full_name,
    email,
    segment,
    cast(valid_from as date) as valid_from,
    cast(valid_to as date) as valid_to,
    cast(is_current as boolean) as is_current
from {{ ref('stg_customers') }}
