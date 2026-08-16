-- mdl:generated-begin id=01KZ2FV3S3JZJQFY7QAA969635 fingerprint=sha256:86541ba4ad4061a3ed0b47fdadce347828aa68c8876183ba965f5cb686d17722 spec=v1
{{ config(materialized='view') }}
with source as (
    select * from {{ ref('stg_benchmark') }}
)
select
    benchmark_code,
    benchmark_name
from source
-- mdl:generated-end

-- mdl:user-begin
-- mdl:user-end
