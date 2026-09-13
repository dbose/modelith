{{ config(materialized='table') }}
select
    md5(cast(reactome_id as varchar)) as pathway_sk,
    cast(reactome_id as varchar) as reactome_id,
    name,
    category
from {{ ref('stg_pathway') }}
