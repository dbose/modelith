-- mdl:generated-begin id=01KZ2CBAD9WD1EH0ZKQP3ESE2N fingerprint=sha256:9c670b74509e62a1d76ca34ed8758669321d17184a59e6f1c143e459e2360250 spec=v1
{{ config(materialized='view') }}
with source as (
    select * from {{ ref('stg_portfolio') }}
)
select
    portfolio_code,
    mandate,
    benchmark_code
from source
-- mdl:generated-end

-- mdl:user-begin
-- mdl:user-end
