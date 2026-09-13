{{ config(materialized='table') }}
select
    md5(cast(cellosaurus_id as varchar)) as cell_line_sk,
    cast(cellosaurus_id as varchar) as cellosaurus_id,
    name,
    organism
from {{ ref('stg_cell_line') }}
