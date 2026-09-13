{{ config(materialized='table') }}
with r0 as (select dbsnp_id, row_number() over () as rn from {{ ref('stg_variant') }}),
r1 as (select ensembl_gene_id, row_number() over () as rn from {{ ref('stg_gene') }})
select
    md5(cast(r0.rn as varchar) || 'variant_in_gene') as variant_in_gene_sk,
    cast(r0.dbsnp_id as varchar) as dbsnp_id,
    cast(r1.ensembl_gene_id as varchar) as ensembl_gene_id
from r0
join r1 on r1.rn = r0.rn
