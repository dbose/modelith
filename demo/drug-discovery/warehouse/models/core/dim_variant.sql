{{ config(materialized='table') }}
select
    md5(cast(dbsnp_id as varchar)) as variant_sk,
    cast(dbsnp_id as varchar) as dbsnp_id,
    consequence,
    clinical_significance
from {{ ref('stg_variant') }}
