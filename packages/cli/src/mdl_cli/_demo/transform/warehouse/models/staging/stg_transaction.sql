select
    transaction_id,
    trade_date,
    cast(amount as decimal(38,2)) as amount,
    portfolio_code,
    cast(counterparty_id as bigint) as counterparty_id
from {{ ref('raw_transactions') }}
