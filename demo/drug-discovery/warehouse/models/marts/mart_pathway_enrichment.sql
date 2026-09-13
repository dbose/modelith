{{ config(materialized='table') }}
select
    cast('group' as varchar) as pathway_group,
    avg(1.0) as enrichment_score
from {{ ref('dim_gene') }}
group by 1
