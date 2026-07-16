{#
  Use custom schema names literally (dbt's default prefixes them with the target
  schema, e.g. analytics_snapshots). This keeps `analytics` for models and
  `snapshots` for SCD2 snapshots -- predictable, readable schema names.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
