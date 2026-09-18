select
    cast(counterparty_id as bigint) as counterparty_id,
    legal_name,
    country
from {{ ref('raw_counterparties') }}
