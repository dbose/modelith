{{ config(materialized='table') }}
select
    md5(cast(mondo_id as varchar)) as disease_sk,
    cast(mondo_id as varchar) as mondo_id,
    name,
    icd10_code
from {{ ref('stg_disease') }}
