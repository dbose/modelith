{{ config(materialized='table') }}
with r0 as (select nct_id, row_number() over () as rn from {{ ref('stg_trial') }}),
r1 as (select mondo_id, row_number() over () as rn from {{ ref('stg_disease') }})
select
    md5(cast(r0.rn as varchar) || 'trial_condition') as trial_condition_sk,
    cast(r0.nct_id as varchar) as nct_id,
    cast(r1.mondo_id as varchar) as mondo_id
from r0
join r1 on r1.rn = r0.rn
