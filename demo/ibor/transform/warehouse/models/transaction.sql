-- mdl:generated-begin id=01KZ2FSTGAEEH1QAZW9HTQVY1W fingerprint=sha256:f37105e4895432a5fc1f1ec79ca5c8af07a85d8cd8d93f7bfad4702f83b2118c spec=v1
{{ config(materialized='view') }}
with source as (
    select * from {{ ref('stg_transaction') }}
)
select
    transaction_id,
    trade_date,
    amount,
    portfolio_code,
    counterparty_id
from source
-- mdl:generated-end

-- mdl:user-begin
-- mdl:user-end
