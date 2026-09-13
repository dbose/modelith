{{ config(materialized='table') }}
with r0 as (select biomarker_id, row_number() over () as rn from {{ ref('stg_biomarker') }}),
r1 as (select mondo_id, row_number() over () as rn from {{ ref('stg_disease') }})
select
    md5(cast(r0.rn as varchar) || 'biomarker_disease') as biomarker_disease_sk,
    cast(r0.biomarker_id as varchar) as biomarker_id,
    cast(r1.mondo_id as varchar) as mondo_id
from r0
join r1 on r1.rn = r0.rn
