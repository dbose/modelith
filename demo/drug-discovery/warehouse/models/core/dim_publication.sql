{{ config(materialized='table') }}
select
    md5(cast(pmid as varchar)) as publication_sk,
    cast(pmid as varchar) as pmid,
    title,
    journal,
    year
from {{ ref('stg_publication') }}
