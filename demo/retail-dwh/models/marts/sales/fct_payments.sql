select
    cast(payment_id as bigint) as payment_id,
    cast(order_id as bigint) as order_id,
    method,
    cast(amount as decimal(38,2)) as amount,
    cast(paid_ts as timestamp) as paid_ts
from {{ ref('stg_payments') }}
