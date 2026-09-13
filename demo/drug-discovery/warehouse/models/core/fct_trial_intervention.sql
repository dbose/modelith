{{ config(materialized='table') }}
with r0 as (select nct_id, row_number() over () as rn from {{ ref('stg_trial') }}),
r1 as (select chembl_id, row_number() over () as rn from {{ ref('stg_drug') }})
select
    md5(cast(r0.rn as varchar) || 'trial_intervention') as trial_intervention_sk,
    cast(r0.nct_id as varchar) as nct_id,
    cast(r1.chembl_id as varchar) as chembl_id
from r0
join r1 on r1.rn = r0.rn
