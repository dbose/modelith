select
    as_of_date,
    cast(quantity as decimal(38,2)) as quantity,
    portfolio_code,
    cast(instrument_id as bigint) as instrument_id
from {{ ref('raw_positions') }}
