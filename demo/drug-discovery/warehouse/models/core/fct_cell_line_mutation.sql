{{ config(materialized='table') }}
with r0 as (select cellosaurus_id, row_number() over () as rn from {{ ref('stg_cell_line') }}),
r1 as (select ensembl_gene_id, row_number() over () as rn from {{ ref('stg_gene') }})
select
    md5(cast(r0.rn as varchar) || 'cell_line_mutation') as cell_line_mutation_sk,
    cast(r0.cellosaurus_id as varchar) as cellosaurus_id,
    cast(r1.ensembl_gene_id as varchar) as ensembl_gene_id
from r0
join r1 on r1.rn = r0.rn
