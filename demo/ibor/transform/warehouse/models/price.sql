-- mdl:generated-begin id=01KZ2CRW88VH6N972NY2G9VPDX fingerprint=sha256:abc28d1fe9f01a169d9a07aab02f7e0e75144b57087dc6141ff027dc383c42a2 spec=v1
{{ config(materialized='view') }}
with source as (
    select * from {{ ref('stg_price') }}
)
select
    price_id,
    price_date,
    close_price,
    instrument_id
from source
-- mdl:generated-end

-- mdl:user-begin
-- mdl:user-end
