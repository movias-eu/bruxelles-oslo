"""Which model fields are not (yet) real columns in the client's database.

The mirror declares a few fields before the client has added them
(SegmentMatch.offset, UCComptageTraverses.link_direction*). Selecting a missing
column makes Postgres fail, so queries .defer() whatever this returns; once the
client migrates, nothing is deferred and the values flow through unchanged.
"""
from django.db import connection


def missing_columns(model, field_names):
    """Names from ``field_names`` whose column is absent from the model's table."""
    with connection.cursor() as cursor:
        present = {
            col.name for col in
            connection.introspection.get_table_description(cursor, model._meta.db_table)
        }
    return [f for f in field_names if model._meta.get_field(f).column not in present]


def loaded(obj, field_name, default=None):
    """Value of a field unless it was deferred (column missing) -- never queries."""
    deferred = obj.get_deferred_fields() if hasattr(obj, "get_deferred_fields") else ()
    if field_name in deferred:
        return default
    return getattr(obj, field_name, default)
