{{ config(materialized='table') }}
select
    md5(cast(taxon_id as varchar)) as organism_sk,
    cast(taxon_id as varchar) as taxon_id,
    name,
    common_name
from {{ ref('stg_organism') }}
