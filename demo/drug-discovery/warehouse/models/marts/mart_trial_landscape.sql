{{ config(materialized='table') }}
select
    cast('group' as varchar) as phase_bucket,
    count(*) as n_trials
from {{ ref('dim_gene') }}
group by 1
