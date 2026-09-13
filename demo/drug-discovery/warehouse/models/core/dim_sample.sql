{{ config(materialized='table') }}
select
    md5(cast(sample_id as varchar)) as sample_sk,
    cast(sample_id as varchar) as sample_id,
    tissue_uberon_id,
    condition
from {{ ref('stg_sample') }}
