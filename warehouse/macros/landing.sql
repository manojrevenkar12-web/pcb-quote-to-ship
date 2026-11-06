{% macro landing(table) -%}
  read_parquet('{{ var("landing") }}/{{ table }}/*.parquet', union_by_name = true)
{%- endmacro %}
