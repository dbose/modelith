select
    md5(cast(customer_id as varchar)) as customer_hashkey,
    md5(coalesce(full_name,'') || coalesce(email,'') || coalesce(segment,'')) as hashdiff,
    full_name,
    email,
    segment,
    cast(valid_from as timestamp) as load_dts
from {{ ref('stg_customers') }}
