{{ config(materialized='table') }}
with r0 as (select chembl_id, row_number() over () as rn from {{ ref('stg_drug') }}),
r1 as (select reactome_id, row_number() over () as rn from {{ ref('stg_pathway') }})
select
    md5(cast(r0.rn as varchar) || 'drug_pathway_effect') as drug_pathway_effect_sk,
    cast(r0.chembl_id as varchar) as chembl_id,
    cast(r1.reactome_id as varchar) as reactome_id
from r0
join r1 on r1.rn = r0.rn
