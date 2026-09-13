{{ config(materialized='table') }}
with r0 as (select ensembl_gene_id, row_number() over () as rn from {{ ref('stg_gene') }}),
r1 as (select uberon_id, row_number() over () as rn from {{ ref('stg_tissue') }})
select
    md5(cast(r0.rn as varchar) || 'gene_expression') as gene_expression_sk,
    cast(r0.ensembl_gene_id as varchar) as ensembl_gene_id,
    cast(r1.uberon_id as varchar) as uberon_id,
    cast(null as double) as tpm
from r0
join r1 on r1.rn = r0.rn
