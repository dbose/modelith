{{ config(materialized='table') }}
with r0 as (select uniprot_id, row_number() over () as rn from {{ ref('stg_protein') }}),
r1 as (select reactome_id, row_number() over () as rn from {{ ref('stg_pathway') }})
select
    md5(cast(r0.rn as varchar) || 'protein_in_pathway') as protein_in_pathway_sk,
    cast(r0.uniprot_id as varchar) as uniprot_id,
    cast(r1.reactome_id as varchar) as reactome_id
from r0
join r1 on r1.rn = r0.rn
