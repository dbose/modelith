-- mdl:generated-begin id=01KZ2B1RV0BZ5257G4AFKRW34X fingerprint=sha256:5853cb1211cfa860146e39e7915e0454cc7aa70a0d7e2d76e28b5034690b6c6d spec=v1
{{ config(materialized='view') }}
with source as (
    select * from {{ ref('stg_instrument') }}
)
select
    instrument_id,
    isin,
    asset_class,
    counterparty_id
from source
-- mdl:generated-end

-- mdl:user-begin
-- mdl:user-end
