-- mdl:generated-begin id=01KZ2CKNG2S3P5AX7PK826ET35 fingerprint=sha256:4965fd5a07dfd9fcb1938591f325568910cb1d3d62efc72f4cbf5bacd28b239b spec=v1
{{ config(materialized='view') }}
with source as (
    select * from {{ ref('stg_position') }}
)
select
    as_of_date,
    quantity,
    portfolio_code,
    instrument_id
from source
-- mdl:generated-end

-- mdl:user-begin
-- mdl:user-end
