{{ config(materialized='table') }}
with r0 as (select ensembl_gene_id, row_number() over () as rn from {{ ref('stg_gene') }})
select
    md5(cast(r0.rn as varchar) || 'gene_regulates_gene') as gene_regulates_gene_sk,
    cast(r0.ensembl_gene_id as varchar) as regulator_ensembl_gene_id,
    cast(r0.ensembl_gene_id as varchar) as target_ensembl_gene_id,
    cast(null as varchar) as regulation_type,
    cast(null as double) as evidence_score
from r0
