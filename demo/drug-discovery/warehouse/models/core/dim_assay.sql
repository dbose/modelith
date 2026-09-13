{{ config(materialized='table') }}
select
    md5(cast(assay_id as varchar)) as assay_sk,
    cast(assay_id as varchar) as assay_id,
    name,
    assay_type
from {{ ref('stg_assay') }}
