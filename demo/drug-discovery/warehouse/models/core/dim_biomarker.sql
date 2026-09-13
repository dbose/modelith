{{ config(materialized='table') }}
select
    md5(cast(biomarker_id as varchar)) as biomarker_sk,
    cast(biomarker_id as varchar) as biomarker_id,
    name,
    type
from {{ ref('stg_biomarker') }}
