{{ config(materialized='table') }}
select
    cast('group' as varchar) as tractability_class,
    count(*) as n_targets
from {{ ref('dim_gene') }}
group by 1
