{{ config(materialized='table') }}
with r0 as (select chembl_id, row_number() over () as rn from {{ ref('stg_drug') }}),
r1 as (select uniprot_id, row_number() over () as rn from {{ ref('stg_protein') }})
select
    md5(cast(r0.rn as varchar) || 'drug_targets_protein') as drug_targets_protein_sk,
    cast(r0.chembl_id as varchar) as chembl_id,
    cast(r1.uniprot_id as varchar) as uniprot_id,
    cast(null as varchar) as action_type,
    cast(null as double) as affinity_nm
from r0
join r1 on r1.rn = r0.rn
