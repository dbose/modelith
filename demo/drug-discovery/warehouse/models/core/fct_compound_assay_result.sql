{{ config(materialized='table') }}
with r0 as (select compound_id, row_number() over () as rn from {{ ref('stg_compound') }}),
r1 as (select assay_id, row_number() over () as rn from {{ ref('stg_assay') }})
select
    md5(cast(r0.rn as varchar) || 'compound_assay_result') as compound_assay_result_sk,
    cast(r0.compound_id as varchar) as compound_id,
    cast(r1.assay_id as varchar) as assay_id
from r0
join r1 on r1.rn = r0.rn
