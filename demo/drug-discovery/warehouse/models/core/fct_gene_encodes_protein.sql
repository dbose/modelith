{{ config(materialized='table') }}
with r0 as (select ensembl_gene_id, row_number() over () as rn from {{ ref('stg_gene') }}),
r1 as (select uniprot_id, row_number() over () as rn from {{ ref('stg_protein') }})
select
    md5(cast(r0.rn as varchar) || 'gene_encodes_protein') as gene_encodes_protein_sk,
    cast(r0.ensembl_gene_id as varchar) as ensembl_gene_id,
    cast(r1.uniprot_id as varchar) as uniprot_id
from r0
join r1 on r1.rn = r0.rn
