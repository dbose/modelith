select
    cast(instrument_id as bigint) as instrument_id,
    isin,
    asset_class,
    cast(counterparty_id as bigint) as counterparty_id
from {{ ref('raw_instruments') }}
