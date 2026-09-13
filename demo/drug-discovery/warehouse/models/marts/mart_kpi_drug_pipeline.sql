{{ config(materialized='table') }}
select
    cast('group' as varchar) as pipeline_phase,
    count(*) as n_drugs
from {{ ref('dim_gene') }}
group by 1
