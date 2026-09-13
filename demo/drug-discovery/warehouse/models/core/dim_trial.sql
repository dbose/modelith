{{ config(materialized='table') }}
select
    md5(cast(nct_id as varchar)) as trial_sk,
    cast(nct_id as varchar) as nct_id,
    title,
    phase,
    status,
    cast('2020-01-01' as timestamp) as valid_from,
    cast(null as timestamp) as valid_to,
    true as is_current
from {{ ref('stg_trial') }}
