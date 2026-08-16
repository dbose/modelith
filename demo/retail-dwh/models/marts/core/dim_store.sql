select
    cast(store_id as bigint) as store_id,
    store_name,
    region,
    country
from {{ ref('stg_stores') }}
