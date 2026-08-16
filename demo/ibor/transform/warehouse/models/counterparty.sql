-- mdl:generated-begin id=01KZ2659634FAG7WT6KDWKA62S fingerprint=sha256:c2f9359e374b54e1fde2fac77baf43d0a04d2170c53d08c264be8799610280d5 spec=v1
{{ config(materialized='view') }}
with source as (
    select * from {{ ref('stg_counterparty') }}
)
select
    counterparty_id,
    legal_name,
    country
from source
-- mdl:generated-end

-- mdl:user-begin
-- mdl:user-end
