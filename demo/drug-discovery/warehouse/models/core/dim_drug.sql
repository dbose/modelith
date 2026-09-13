{{ config(materialized='table') }}
select
    md5(cast(chembl_id as varchar)) as drug_sk,
    cast(chembl_id as varchar) as chembl_id,
    name,
    mechanism,
    max_phase,
    cast('2020-01-01' as timestamp) as valid_from,
    cast(null as timestamp) as valid_to,
    true as is_current
from {{ ref('stg_drug') }}
