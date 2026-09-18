{% macro mdl_scd2(source_relation, business_key, tracked_columns, valid_from='valid_from', valid_to='valid_to', is_current='is_current') %}
{#- Type-2 slowly changing dimension. Emitted by Modelith; call from a model. -#}
with source as (
    select * from {{ source_relation }}
),
hashed as (
    select
        *,
        {{ dbt_utils_surrogate_key([business_key]) }} as mdl_scd_id,
        {{ dbt_utils_hash(tracked_columns) }}          as mdl_row_hash
    from source
)
select
    hashed.*,
    cast(current_timestamp as timestamp)     as {{ valid_from }},
    cast(null as timestamp)                  as {{ valid_to }},
    true                                     as {{ is_current }}
from hashed
{% endmacro %}
