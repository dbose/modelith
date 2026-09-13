{{ config(materialized='table') }}
with r0 as (select antibody_id, row_number() over () as rn from {{ ref('stg_antibody') }}),
r1 as (select uniprot_id, row_number() over () as rn from {{ ref('stg_protein') }})
select
    md5(cast(r0.rn as varchar) || 'antibody_target') as antibody_target_sk,
    cast(r0.antibody_id as varchar) as antibody_id,
    cast(r1.uniprot_id as varchar) as uniprot_id
from r0
join r1 on r1.rn = r0.rn
