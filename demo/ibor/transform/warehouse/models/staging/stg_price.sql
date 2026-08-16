select
    price_id,
    price_date,
    cast(close_price as decimal(38,2)) as close_price,
    cast(instrument_id as bigint) as instrument_id
from {{ ref('raw_prices') }}
