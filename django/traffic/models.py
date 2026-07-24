"""Read-only mirror of the client's `traffic` models, trimmed to the traverse
subgraph we care about for the OSLO mapping.

Faithfulness notes:
  * Field names, types and the table-name inference are copied from the client's
    models.py (traffic_models.txt) so Django resolves the SAME tables it does on
    the server: class UCComptageTraverses -> table "traffic_uccomptagetraverses".
  * VEH_TYPES is the codelist that lives ONLY in the Django source, not the DB.
  * Every Meta sets ``managed = False`` -> Django never creates/alters/drops
    these tables. This mimic is strictly read-only.
  * The client file imports GIS/admin helpers (format_html, ugettext, ...). None
    are needed to READ rows, so they are omitted here; only the field
    definitions matter for the ORM.
"""
from django.db import models

# Codelist from the client's models.py — not present anywhere in the database.
VEH_TYPES = {
    1: "VEH",
    2: "BIKE",
    3: "RADAR",
}


class UCComptageLink(models.Model):
    link_name = models.CharField(max_length=50, unique=True, null=True, blank=True)
    descr_short_fr = models.CharField(null=True, blank=True, max_length=250)
    descr_short_nl = models.CharField(null=True, blank=True, max_length=250)
    descr_long_fr = models.TextField(null=True, blank=True, max_length=250)
    descr_long_nl = models.TextField(null=True, blank=True, max_length=250)

    class Meta:
        managed = False
        db_table = "traffic_uccomptagelink"

    def __str__(self):
        return self.link_name or f"link {self.pk}"


class UCComptageTraverses(models.Model):
    traverse_id = models.CharField(unique=True, max_length=50)
    num_lanes = models.IntegerField(null=True, blank=True)
    orientation = models.IntegerField(null=True, blank=True)  # degrees, North=0, clockwise
    segment_id = models.CharField(null=True, blank=True, max_length=20)
    traverse_number = models.IntegerField(blank=True, null=True)
    traverse_position = models.IntegerField(blank=True, null=True)
    link_id = models.ForeignKey(
        UCComptageLink, on_delete=models.DO_NOTHING, null=True, blank=True, db_column="link_id_id"
    )
    co_x = models.FloatField(null=True, blank=True)  # Belgian Lambert 72 (EPSG:31370)
    co_y = models.FloatField(null=True, blank=True)
    zone_geographic = models.CharField(null=True, blank=True, max_length=100)
    direction = models.CharField(null=True, blank=True, max_length=100)
    install_date = models.DateField(blank=True, null=True)
    uninstall_date = models.DateField(blank=True, null=True)
    veh_type = models.IntegerField(choices=tuple(sorted(VEH_TYPES.items())), default=1)

    class Meta:
        managed = False
        db_table = "traffic_uccomptagetraverses"

    def __str__(self):
        return self.traverse_id

    @property
    def veh_type_label(self):
        return VEH_TYPES.get(self.veh_type)


class SegmentMatch(models.Model):
    """Read-only mirror of traffic_segmentmatch.

    Coupled to a traverse by the existing integer FK
    ``traverse_id -> traffic_uccomptagetraverses.id`` (one traverse -> many
    matches). The varchar ``segment_id`` columns on either table are NOT the
    join key and play no part in this relationship. ``related_name="Traverse"``
    matches the client's model, so a traverse reaches its matches via
    ``traverse.Traverse.all()``.
    """

    match_id = models.CharField(max_length=64, null=True, blank=True)
    status = models.CharField(max_length=20)
    segment_id = models.CharField(max_length=20, null=True, blank=True)
    wkb = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)
    # NOT YET IN THE REAL TABLE. Distance in metres of the traverse location from
    # the start of the segment linestring. Declared here so the model is ready,
    # but it MUST NOT be read from the ORM until the column actually exists --
    # selecting it would make Postgres raise "column does not exist". Until then
    # the value comes only from mock data. Once the DB column is added, this
    # field works automatically with no further change.
    offset = models.FloatField(null=True, blank=True)
    traverse_id = models.ForeignKey(
        UCComptageTraverses,
        related_name="Traverse",
        on_delete=models.DO_NOTHING,
        db_column="traverse_id",
    )

    class Meta:
        managed = False
        db_table = "traffic_segmentmatch"

    def __str__(self):
        return f"{self.match_id} ({self.status})"
