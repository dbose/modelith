{{ config(materialized='table') }}
with r0 as (select chembl_id, row_number() over () as rn from {{ ref('stg_drug') }})
select
    md5(cast(r0.rn as varchar) || 'adverse_event') as adverse_event_sk,
    cast(r0.chembl_id as varchar) as chembl_id,
    cast(null as varchar) as meddra_pt,
    cast(null as integer) as report_count
from r0
