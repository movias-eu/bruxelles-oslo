"""Export the detector -> traverse map the live TLC/DAI export needs.

    python manage.py export_live_traverse_map -o traverse_map.csv

One CSV row per active UCComptageDetectorTraverse link:

    detector_id,traverse_id,link_direction
    ARL_103_1,ARL_103,inOppositeDirection
    ARL_103_2,ARL_103,inOppositeDirection

live-data/export_live.py (--traverse-map) sums the detectors of each traverse
and gives the measurements the traverse's direction. link_direction stays empty
while unknown or before the column exists. Re-export whenever detectors,
traverses or directions change.
"""
import csv
import sys

from django.core.management.base import BaseCommand

from traffic.columns import loaded, missing_columns
from traffic.models import UCComptageDetectorTraverse, UCComptageTraverses
from traffic.oslo.direction import LINK_DIRECTIONS


class Command(BaseCommand):
    help = "Write the detector -> traverse map (CSV) for live-data/export_live.py."

    def add_arguments(self, parser):
        parser.add_argument("-o", "--output", help="CSV file (default: stdout).")

    def handle(self, *args, **opts):
        # Select only what the CSV needs; link_direction only once it exists.
        fields = ["detector_id__detector_id", "traverse_id__traverse_id"]
        if not missing_columns(UCComptageTraverses, ["link_direction"]):
            fields.append("traverse_id__link_direction")
        links = (
            UCComptageDetectorTraverse.objects.filter(active=True)
            .select_related("detector_id", "traverse_id")
            .only(*fields)
            .order_by("traverse_id__traverse_id", "detector_id__detector_id")
        )
        out = open(opts["output"], "w", newline="", encoding="utf-8") if opts["output"] else sys.stdout
        n = 0
        try:
            writer = csv.writer(out)
            writer.writerow(["detector_id", "traverse_id", "link_direction"])
            for link in links:
                direction = loaded(link.traverse_id, "link_direction")
                writer.writerow([
                    link.detector_id.detector_id,
                    link.traverse_id.traverse_id,
                    direction if direction in LINK_DIRECTIONS else "",
                ])
                n += 1
        finally:
            if out is not sys.stdout:
                out.close()
        self.stderr.write(f"wrote {n} detector -> traverse links")
