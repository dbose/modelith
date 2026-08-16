select
    cast(category_id as bigint) as category_id,
    category_name,
    department
from {{ ref('stg_categories') }}
