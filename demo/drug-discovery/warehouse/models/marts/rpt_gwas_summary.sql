{{ config(materialized='table') }}
select
    cast('group' as varchar) as chromosome_band,
    count(*) as n_variants
from {{ ref('dim_gene') }}
group by 1
