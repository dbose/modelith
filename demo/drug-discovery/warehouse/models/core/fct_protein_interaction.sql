{{ config(materialized='table') }}
with r0 as (select uniprot_id, row_number() over () as rn from {{ ref('stg_protein') }})
select
    md5(cast(r0.rn as varchar) || 'protein_interaction') as protein_interaction_sk,
    cast(r0.uniprot_id as varchar) as uniprot_id,
    cast(r0.uniprot_id as varchar) as partner_uniprot_id
from r0
