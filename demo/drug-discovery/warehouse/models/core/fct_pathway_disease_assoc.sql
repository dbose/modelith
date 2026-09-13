{{ config(materialized='table') }}
with r0 as (select reactome_id, row_number() over () as rn from {{ ref('stg_pathway') }}),
r1 as (select mondo_id, row_number() over () as rn from {{ ref('stg_disease') }})
select
    md5(cast(r0.rn as varchar) || 'pathway_disease_assoc') as pathway_disease_assoc_sk,
    cast(r0.reactome_id as varchar) as reactome_id,
    cast(r1.mondo_id as varchar) as mondo_id
from r0
join r1 on r1.rn = r0.rn
