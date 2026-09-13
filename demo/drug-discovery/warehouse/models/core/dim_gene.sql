{{ config(materialized='table') }}
select
    md5(cast(ensembl_gene_id as varchar)) as gene_sk,
    cast(ensembl_gene_id as varchar) as ensembl_gene_id,
    symbol,
    chromosome,
    biotype
from {{ ref('stg_gene') }}
