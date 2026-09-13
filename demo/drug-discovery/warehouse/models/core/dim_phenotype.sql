{{ config(materialized='table') }}
select
    md5(cast(hp_id as varchar)) as phenotype_sk,
    cast(hp_id as varchar) as hp_id,
    name
from {{ ref('stg_phenotype') }}
