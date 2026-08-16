{% macro dbt_utils_surrogate_key(fields) %}
    md5(cast(concat({{ fields | join(", '-', ") }}) as varchar))
{% endmacro %}

{% macro dbt_utils_hash(fields) %}
    md5(cast(concat({{ fields | join(", '-', ") }}) as varchar))
{% endmacro %}
