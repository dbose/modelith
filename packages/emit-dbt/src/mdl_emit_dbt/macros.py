"""Shipped dbt macro package (spec §13.3 — recommended default).

SCD2 / hub / link / satellite logic lives in a small dbt macro package that
emitted models call, keeping generated SQL compact and upgradable. The `--inline`
escape hatch (see emitter.inline_pattern_sql) expands the same logic in place for
teams that want the model to survive Modelith being removed.

`macro_files()` returns {relative_path: content} the emitter writes under the dbt
project's macro directory.
"""

from __future__ import annotations

_SCD2_MACRO = """\
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
"""

# Minimal, dependency-free surrogate-key / hash helpers so the package is
# self-contained for the duckdb test path (real deployments swap in dbt_utils).
_HELPERS_MACRO = """\
{% macro dbt_utils_surrogate_key(fields) %}
    md5(cast(concat({{ fields | join(", '-', ") }}) as varchar))
{% endmacro %}

{% macro dbt_utils_hash(fields) %}
    md5(cast(concat({{ fields | join(", '-', ") }}) as varchar))
{% endmacro %}
"""

_HUB_MACRO = """\
{% macro mdl_hub(source_relation, business_key) %}
{#- Data Vault hub: distinct business keys with a hash key. -#}
select distinct
    {{ dbt_utils_surrogate_key([business_key]) }} as hub_hashkey,
    {{ business_key }},
    cast(current_timestamp as timestamp) as load_dts
from {{ source_relation }}
{% endmacro %}
"""


def macro_files() -> dict[str, str]:
    return {
        "macros/mdl_scd2.sql": _SCD2_MACRO,
        "macros/mdl_hub.sql": _HUB_MACRO,
        "macros/mdl_helpers.sql": _HELPERS_MACRO,
    }
