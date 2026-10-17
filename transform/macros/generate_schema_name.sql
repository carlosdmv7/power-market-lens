{#
    dbt's default concatenates the target schema and the custom one
    (`main_staging`). Here a model's schema is exactly its layer name —
    staging, intermediate, marts — so the warehouse reads the same in dev and
    prod, and the database (power_dev / power_prod) is what separates them.

    The ci target is the exception: it shares power_dev with local development,
    so its schemas get a `ci_` prefix and a PR build cannot overwrite a
    developer's tables.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- elif target.name == 'ci' -%}
        ci_{{ custom_schema_name | trim }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
