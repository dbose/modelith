{% macro mdl_hub(source_relation, business_key) %}
{#- Data Vault hub: distinct business keys with a hash key. -#}
select distinct
    {{ dbt_utils_surrogate_key([business_key]) }} as hub_hashkey,
    {{ business_key }},
    cast(current_timestamp as timestamp) as load_dts
from {{ source_relation }}
{% endmacro %}
