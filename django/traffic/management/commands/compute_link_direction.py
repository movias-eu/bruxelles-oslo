"""Propose each traverse's link_direction from its orientation and segment match.

    python manage.py compute_link_direction [-o proposals.csv] [--all]

Writes a CSV report, one row per traverse with a segment match (--all: every
traverse): orientation, the matched segment's heading at the traverse, the
angle between them, the current and proposed link_direction, and why. See
traffic.oslo.direction for the rule.

Read-only, like every command in this mimic (the DB session is read-only). On
the client's side the same function fills the column: for every traverse with
link_direction_manual = False, store the proposed value (also None). The
implementation notes in django/README.md have the snippet.
"""
import csv
import sys

from django.core.management.base import BaseCommand
from django.db.models import Prefetch

from traffic.columns import loaded, missing_columns
from traffic.models import SegmentMatch, UCComptageTraverses
from traffic.oslo.direction import combine, compute_link_direction

NEW_TRAVERSE_FIELDS = ["link_direction", "link_direction_manual"]
COLUMNS = ["traverse_id", "veh_type", "orientation", "matches", "segment_bearing",
           "angle", "current", "manual", "proposed", "reason"]


def _round(value):
    return "" if value is None else round(value)


class Command(BaseCommand):
    help = "Propose link_direction per traverse (orientation vs matched segment) as CSV."

    def add_arguments(self, parser):
        parser.add_argument("-o", "--output", help="CSV file (default: stdout).")
        parser.add_argument("--all", action="store_true",
                            help="Also list traverses without a segment match.")

    def handle(self, *args, **opts):
        missing = missing_columns(UCComptageTraverses, NEW_TRAVERSE_FIELDS)
        if missing:
            self.stderr.write("link_direction columns not in the database yet: "
                              "'current' and 'manual' stay empty")
        matches_qs = SegmentMatch.objects.defer(*missing_columns(SegmentMatch, ["offset"]))
        traverses = (
            UCComptageTraverses.objects.defer(*missing)
            .prefetch_related(Prefetch("Traverse", queryset=matches_qs))
            .order_by("traverse_id")
        )

        out = open(opts["output"], "w", newline="", encoding="utf-8") if opts["output"] else sys.stdout
        try:
            writer = csv.writer(out)
            writer.writerow(COLUMNS)
            counts = {}
            for t in traverses:
                matches = list(t.Traverse.all())
                if not matches and not opts["all"]:
                    continue
                d = combine([compute_link_direction(t.orientation, m.wkb, t.co_x, t.co_y)
                             for m in matches])
                counts[d.value] = counts.get(d.value, 0) + 1
                writer.writerow([
                    t.traverse_id, t.veh_type_label, t.orientation, len(matches),
                    _round(d.segment_bearing), _round(d.angle),
                    loaded(t, "link_direction") or "", loaded(t, "link_direction_manual", ""),
                    d.value or "", d.reason,
                ])
        finally:
            if out is not sys.stdout:
                out.close()
        summary = ", ".join(f"{k or 'none'}: {v}" for k, v in sorted(counts.items(), key=str))
        self.stderr.write(f"proposals -- {summary}")
