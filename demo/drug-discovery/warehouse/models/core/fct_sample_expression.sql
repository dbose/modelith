{{ config(materialized='table') }}
with r0 as (select sample_id, row_number() over () as rn from {{ ref('stg_sample') }}),
r1 as (select ensembl_gene_id, row_number() over () as rn from {{ ref('stg_gene') }})
select
    md5(cast(r0.rn as varchar) || 'sample_expression') as sample_expression_sk,
    cast(r0.sample_id as varchar) as sample_id,
    cast(r1.ensembl_gene_id as varchar) as ensembl_gene_id
from r0
join r1 on r1.rn = r0.rn
