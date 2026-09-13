{{ config(materialized='table') }}
select
    md5(cast(uberon_id as varchar)) as tissue_sk,
    cast(uberon_id as varchar) as uberon_id,
    name
from {{ ref('stg_tissue') }}
