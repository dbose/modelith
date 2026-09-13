{{ config(materialized='table') }}
select
    cast('group' as varchar) as tissue_group,
    avg(1.0) as mean_tpm
from {{ ref('dim_gene') }}
group by 1
