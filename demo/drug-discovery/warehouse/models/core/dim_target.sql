{{ config(materialized='table') }}
select
    md5(cast(target_id as varchar)) as target_sk,
    cast(target_id as varchar) as target_id,
    target_type,
    tractability,
    cast('2020-01-01' as timestamp) as valid_from,
    cast(null as timestamp) as valid_to,
    true as is_current
from {{ ref('stg_target') }}
