{{ config(materialized='table') }}
select
    md5(cast(indication_id as varchar)) as indication_sk,
    cast(indication_id as varchar) as indication_id,
    mondo_id,
    name
from {{ ref('stg_indication') }}
