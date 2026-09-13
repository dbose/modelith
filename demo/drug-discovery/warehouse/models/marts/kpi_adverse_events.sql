{{ config(materialized='table') }}
select
    cast('group' as varchar) as severity_class,
    count(*) as n_reports
from {{ ref('dim_gene') }}
group by 1
