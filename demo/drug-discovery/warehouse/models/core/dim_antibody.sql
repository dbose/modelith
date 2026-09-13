{{ config(materialized='table') }}
select
    md5(cast(antibody_id as varchar)) as antibody_sk,
    cast(antibody_id as varchar) as antibody_id,
    name,
    clonality
from {{ ref('stg_antibody') }}
