{{ config(materialized='table') }}
with r0 as (select ensembl_gene_id, row_number() over () as rn from {{ ref('stg_gene') }}),
r1 as (select hp_id, row_number() over () as rn from {{ ref('stg_phenotype') }})
select
    md5(cast(r0.rn as varchar) || 'gene_phenotype_assoc') as gene_phenotype_assoc_sk,
    cast(r0.ensembl_gene_id as varchar) as ensembl_gene_id,
    cast(r1.hp_id as varchar) as hp_id
from r0
join r1 on r1.rn = r0.rn
