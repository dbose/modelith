{#-
  mdl_get_constraints — Modelith's dispatched constraint-introspection macro.

  dbt exposes no way to READ a warehouse's declared PK / FK / UNIQUE / NOT NULL
  constraints (generate_source, the adapter Column API and catalog.json are all
  columns+types only; dbt's own `constraints`/`contracts` only ENFORCE, never read).
  Modelith ships this macro into the user's dbt project and runs it via

      dbt --quiet run-operation mdl_get_constraints --args '{schema: <s>, database: <d>}'

  It prints ONE JSON object to stdout (survives --quiet), which `mdl reverse --connect`
  parses and overlays onto the projection (mdl_reverse.schema_reader.apply_constraints),
  landing declared PK/FK at HIGH confidence — the erwin-parity path. Shape:

      { "<table>": {
          "primary_key": ["col", ...],
          "unique": [["col", ...], ...],
          "foreign_keys": [{"columns": ["col"], "ref_table": "t", "ref_columns": ["c"]}],
          "columns": { "col": {"nullable": true|false} } } }

  Dispatched per adapter because no single SQL is portable: Snowflake has no
  KEY_COLUMN_USAGE (SHOW ... KEYS + RESULT_SCAN instead); Redshift uses pg_catalog /
  SHOW CONSTRAINTS; the rest read the standard information_schema (BigQuery/Databricks
  need a catalog/dataset qualifier). Fail-soft: an empty or unreadable catalog -> {}.
-#}

{#- Dispatch within whatever project this macro was copied into (Modelith drops it in the
    user's dbt project macros/ dir), so the namespace is the running project, resolved at
    runtime — not a hardcoded package name. Helper macros below are called unqualified for
    the same reason (they live in the same project). -#}
{% macro mdl_get_constraints(schema, database=None) %}
  {{ return(adapter.dispatch('mdl_get_constraints', project_name)(schema, database)) }}
{% endmacro %}


{#- ------------------------------------------------------------------------- -#}
{#- default: standard SQL-standard information_schema (Postgres, BigQuery,     -#}
{#- Databricks/UC; DuckDB has its own branch below).                          -#}
{#- ------------------------------------------------------------------------- -#}
{% macro default__mdl_get_constraints(schema, database) %}
  {% if not execute %}{{ return({}) }}{% endif %}

  {%- set nullable_sql -%}
    select table_name, column_name, is_nullable
    from information_schema.columns
    where table_schema = '{{ schema }}'
  {%- endset -%}

  {%- set pk_sql -%}
    select tc.table_name, kcu.column_name, kcu.ordinal_position
    from information_schema.table_constraints tc
    join information_schema.key_column_usage kcu
      on  kcu.constraint_name   = tc.constraint_name
      and kcu.constraint_schema = tc.constraint_schema
    where tc.constraint_type = 'PRIMARY KEY'
      and tc.table_schema = '{{ schema }}'
    order by tc.table_name, kcu.ordinal_position
  {%- endset -%}

  {#- FK: child columns from key_column_usage, referenced side from
      constraint_column_usage joined on the constraint name. This resolves on
      Postgres, BigQuery and Databricks; a per-adapter override can refine it. -#}
  {%- set fk_sql -%}
    select
      kcu.table_name        as child_table,
      kcu.column_name       as child_column,
      kcu.ordinal_position  as pos,
      ccu.table_name        as ref_table,
      ccu.column_name       as ref_column
    from information_schema.table_constraints tc
    join information_schema.key_column_usage kcu
      on  kcu.constraint_name   = tc.constraint_name
      and kcu.constraint_schema = tc.constraint_schema
    join information_schema.constraint_column_usage ccu
      on  ccu.constraint_name   = tc.constraint_name
      and ccu.constraint_schema = tc.constraint_schema
    where tc.constraint_type = 'FOREIGN KEY'
      and tc.table_schema = '{{ schema }}'
    order by kcu.table_name, tc.constraint_name, kcu.ordinal_position
  {%- endset -%}

  {{ return(_mdl_assemble_constraints(nullable_sql, pk_sql, fk_sql)) }}
{% endmacro %}


{#- ------------------------------------------------------------------------- -#}
{#- DuckDB: duckdb_constraints() carries FK referenced table/cols inline (no   -#}
{#- multi-join) and NOT NULL as its own row — the most reliable source.       -#}
{#- ------------------------------------------------------------------------- -#}
{% macro duckdb__mdl_get_constraints(schema, database) %}
  {% if not execute %}{{ return({}) }}{% endif %}

  {%- set out = {} -%}

  {%- set null_q -%}
    select table_name, column_name, is_nullable
    from information_schema.columns
    where table_schema = '{{ schema }}'
  {%- endset -%}
  {%- set nulls = run_query(null_q) -%}
  {%- if nulls -%}
    {%- for r in nulls.rows -%}
      {%- set t = r[0] -%}
      {%- if t not in out -%}{% do out.update({t: {'primary_key': [], 'unique': [], 'foreign_keys': [], 'columns': {}}}) %}{%- endif -%}
      {%- do out[t]['columns'].update({r[1]: {'nullable': (r[2] == 'YES')}}) -%}
    {%- endfor -%}
  {%- endif -%}

  {%- set con_q -%}
    select table_name, constraint_type, constraint_column_names, referenced_table, referenced_column_names
    from duckdb_constraints()
    where schema_name = '{{ schema }}'
  {%- endset -%}
  {%- set cons = run_query(con_q) -%}
  {%- if cons -%}
    {%- for r in cons.rows -%}
      {%- set t = r[0] -%}
      {%- if t not in out -%}{% do out.update({t: {'primary_key': [], 'unique': [], 'foreign_keys': [], 'columns': {}}}) %}{%- endif -%}
      {%- set ctype = r[1] -%}
      {#- the dbt-duckdb adapter returns DuckDB LIST columns as JSON strings, not lists —
          normalise both the child columns and the FK-referenced columns. -#}
      {%- set cols = _mdl_as_list(r[2]) -%}
      {%- set ref_cols = _mdl_as_list(r[4]) -%}
      {%- if ctype == 'PRIMARY KEY' -%}
        {%- do out[t].update({'primary_key': cols}) -%}
      {%- elif ctype == 'UNIQUE' -%}
        {%- do out[t]['unique'].append(cols) -%}
      {%- elif ctype == 'FOREIGN KEY' -%}
        {%- do out[t]['foreign_keys'].append({'columns': cols, 'ref_table': r[3], 'ref_columns': ref_cols}) -%}
      {%- elif ctype == 'NOT NULL' -%}
        {%- for c in cols -%}
          {%- if c not in out[t]['columns'] -%}{% do out[t]['columns'].update({c: {}}) %}{%- endif -%}
          {%- do out[t]['columns'][c].update({'nullable': false}) -%}
        {%- endfor -%}
      {%- endif -%}
    {%- endfor -%}
  {%- endif -%}

  {{ return(_mdl_emit(out)) }}
{% endmacro %}


{#- ------------------------------------------------------------------------- -#}
{#- Snowflake: no KEY_COLUMN_USAGE. SHOW PRIMARY KEYS / SHOW IMPORTED KEYS +   -#}
{#- RESULT_SCAN. SHOW column names are lowercase -> double-quote them; the     -#}
{#- IMPORTED KEYS column set is undocumented, so reference by discovered name. -#}
{#- ------------------------------------------------------------------------- -#}
{% macro snowflake__mdl_get_constraints(schema, database) %}
  {% if not execute %}{{ return({}) }}{% endif %}
  {%- set db = database or target.database -%}
  {%- set scope = 'IN SCHEMA ' ~ db ~ '.' ~ schema -%}
  {%- set out = {} -%}

  {#- nullability from information_schema.columns -#}
  {%- set null_q -%}
    select table_name, column_name, is_nullable
    from {{ db }}.information_schema.columns
    where table_schema = '{{ schema }}'
  {%- endset -%}
  {%- set nulls = run_query(null_q) -%}
  {%- if nulls -%}
    {%- for r in nulls.rows -%}
      {%- set t = r[0] -%}
      {%- if t not in out -%}{% do out.update({t: {'primary_key': [], 'unique': [], 'foreign_keys': [], 'columns': {}}}) %}{%- endif -%}
      {%- do out[t]['columns'].update({r[1]: {'nullable': (r[2] == 'YES')}}) -%}
    {%- endfor -%}
  {%- endif -%}

  {#- primary keys: SHOW PRIMARY KEYS has a documented column contract -#}
  {%- do run_query('SHOW PRIMARY KEYS ' ~ scope) -%}
  {%- set pks = run_query('select "table_name", "column_name", "key_sequence" from table(result_scan(last_query_id())) order by "table_name", "key_sequence"') -%}
  {%- if pks -%}
    {%- for r in pks.rows -%}
      {%- set t = r[0] -%}
      {%- if t not in out -%}{% do out.update({t: {'primary_key': [], 'unique': [], 'foreign_keys': [], 'columns': {}}}) %}{%- endif -%}
      {%- do out[t]['primary_key'].append(r[1]) -%}
    {%- endfor -%}
  {%- endif -%}

  {#- foreign keys: SHOW IMPORTED KEYS. Columns are undocumented; select the
      fk_/pk_ names known from the JDBC contract, still via double-quoted refs. -#}
  {%- do run_query('SHOW IMPORTED KEYS ' ~ scope) -%}
  {%- set fks = run_query('select "fk_table_name", "fk_column_name", "pk_table_name", "pk_column_name", "key_sequence" from table(result_scan(last_query_id())) order by "fk_table_name", "fk_name", "key_sequence"') -%}
  {%- if fks -%}
    {%- set fk_acc = {} -%}
    {%- for r in fks.rows -%}
      {%- set child_t = r[0] -%}{% set child_c = r[1] %}{% set ref_t = r[2] %}{% set ref_c = r[3] %}
      {%- if child_t not in out -%}{% do out.update({child_t: {'primary_key': [], 'unique': [], 'foreign_keys': [], 'columns': {}}}) %}{%- endif -%}
      {%- set key = child_t ~ '||' ~ ref_t -%}
      {%- if key not in fk_acc -%}{% do fk_acc.update({key: {'child': child_t, 'columns': [], 'ref_table': ref_t, 'ref_columns': []}}) %}{%- endif -%}
      {%- do fk_acc[key]['columns'].append(child_c) -%}
      {%- do fk_acc[key]['ref_columns'].append(ref_c) -%}
    {%- endfor -%}
    {%- for key, fk in fk_acc.items() -%}
      {%- do out[fk['child']]['foreign_keys'].append({'columns': fk['columns'], 'ref_table': fk['ref_table'], 'ref_columns': fk['ref_columns']}) -%}
    {%- endfor -%}
  {%- endif -%}

  {{ return(_mdl_emit(out)) }}
{% endmacro %}


{#- ------------------------------------------------------------------------- -#}
{#- Redshift: PK/FK via SHOW CONSTRAINTS is per-table and unjoinable; the      -#}
{#- portable read is information_schema.table_constraints + key_column_usage   -#}
{#- (Postgres-derived). Nullability from information_schema.columns.           -#}
{#- (A pg_catalog.pg_constraint override can be added if a shop needs it.)     -#}
{#- ------------------------------------------------------------------------- -#}
{% macro redshift__mdl_get_constraints(schema, database) %}
  {{ return(default__mdl_get_constraints(schema, database)) }}
{% endmacro %}


{#- ------------------------------------------------------------------------- -#}
{#- Shared assembler for the standard-information_schema branches.            -#}
{#- ------------------------------------------------------------------------- -#}
{% macro _mdl_assemble_constraints(nullable_sql, pk_sql, fk_sql) %}
  {%- set out = {} -%}

  {%- set nulls = run_query(nullable_sql) -%}
  {%- if nulls -%}
    {%- for r in nulls.rows -%}
      {%- set t = r[0] -%}
      {%- if t not in out -%}{% do out.update({t: {'primary_key': [], 'unique': [], 'foreign_keys': [], 'columns': {}}}) %}{%- endif -%}
      {%- do out[t]['columns'].update({r[1]: {'nullable': (r[2] == 'YES')}}) -%}
    {%- endfor -%}
  {%- endif -%}

  {%- set pks = run_query(pk_sql) -%}
  {%- if pks -%}
    {%- for r in pks.rows -%}
      {%- set t = r[0] -%}
      {%- if t not in out -%}{% do out.update({t: {'primary_key': [], 'unique': [], 'foreign_keys': [], 'columns': {}}}) %}{%- endif -%}
      {%- do out[t]['primary_key'].append(r[1]) -%}
    {%- endfor -%}
  {%- endif -%}

  {%- set fks = run_query(fk_sql) -%}
  {%- if fks -%}
    {%- set fk_acc = {} -%}
    {%- for r in fks.rows -%}
      {%- set child_t = r[0] -%}{% set child_c = r[1] %}{% set ref_t = r[3] %}{% set ref_c = r[4] %}
      {%- if child_t not in out -%}{% do out.update({child_t: {'primary_key': [], 'unique': [], 'foreign_keys': [], 'columns': {}}}) %}{%- endif -%}
      {%- set key = child_t ~ '||' ~ ref_t -%}
      {%- if key not in fk_acc -%}{% do fk_acc.update({key: {'child': child_t, 'columns': [], 'ref_table': ref_t, 'ref_columns': []}}) %}{%- endif -%}
      {%- do fk_acc[key]['columns'].append(child_c) -%}
      {%- do fk_acc[key]['ref_columns'].append(ref_c) -%}
    {%- endfor -%}
    {%- for key, fk in fk_acc.items() -%}
      {%- do out[fk['child']]['foreign_keys'].append({'columns': fk['columns'], 'ref_table': fk['ref_table'], 'ref_columns': fk['ref_columns']}) -%}
    {%- endfor -%}
  {%- endif -%}

  {{ return(_mdl_emit(out)) }}
{% endmacro %}


{#- Coerce a column-array value to a Jinja list. Some adapters (dbt-duckdb) serialise a
    native LIST/ARRAY column to a JSON STRING in the agate result; others return a real
    list. Normalise both so downstream code never iterates a string char-by-char. -#}
{% macro _mdl_as_list(value) %}
  {%- if value is string -%}
    {%- set s = value | trim -%}
    {%- if s.startswith('[') -%}
      {{ return(fromjson(s)) }}
    {%- elif s == '' -%}
      {{ return([]) }}
    {%- else -%}
      {{ return([s]) }}
    {%- endif -%}
  {%- elif value is iterable and value is not mapping -%}
    {{ return(value | list) }}
  {%- elif value is none -%}
    {{ return([]) }}
  {%- else -%}
    {{ return([value]) }}
  {%- endif -%}
{% endmacro %}


{#- Print the payload as one JSON line (survives --quiet) AND return it. -#}
{% macro _mdl_emit(payload) %}
  {%- if execute -%}
    {{ print('MDL_CONSTRAINTS_JSON ' ~ tojson(payload)) }}
  {%- endif -%}
  {{ return(payload) }}
{% endmacro %}
