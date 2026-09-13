{{ config(materialized='table') }}
with r0 as (select chembl_id, row_number() over () as rn from {{ ref('stg_drug') }}),
r1 as (select target_id, row_number() over () as rn from {{ ref('stg_target') }}),
r2 as (select assay_id, row_number() over () as rn from {{ ref('stg_assay') }})
select
    md5(cast(r0.rn as varchar) || 'drug_binding') as drug_binding_sk,
    cast(r0.chembl_id as varchar) as chembl_id,
    cast(r1.target_id as varchar) as target_id,
    cast(r2.assay_id as varchar) as assay_id,
    cast(null as double) as ic50_nm
from r0
join r1 on r1.rn = r0.rn
join r2 on r2.rn = r0.rn
