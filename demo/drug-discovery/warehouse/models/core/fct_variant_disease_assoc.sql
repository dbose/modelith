{{ config(materialized='table') }}
with r0 as (select dbsnp_id, row_number() over () as rn from {{ ref('stg_variant') }}),
r1 as (select mondo_id, row_number() over () as rn from {{ ref('stg_disease') }})
select
    md5(cast(r0.rn as varchar) || 'variant_disease_assoc') as variant_disease_assoc_sk,
    cast(r0.dbsnp_id as varchar) as dbsnp_id,
    cast(r1.mondo_id as varchar) as mondo_id,
    cast(null as double) as p_value,
    cast(null as double) as odds_ratio
from r0
join r1 on r1.rn = r0.rn
