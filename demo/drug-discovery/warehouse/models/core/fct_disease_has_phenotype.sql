{{ config(materialized='table') }}
with r0 as (select mondo_id, row_number() over () as rn from {{ ref('stg_disease') }}),
r1 as (select hp_id, row_number() over () as rn from {{ ref('stg_phenotype') }})
select
    md5(cast(r0.rn as varchar) || 'disease_has_phenotype') as disease_has_phenotype_sk,
    cast(r0.mondo_id as varchar) as mondo_id,
    cast(r1.hp_id as varchar) as hp_id
from r0
join r1 on r1.rn = r0.rn
