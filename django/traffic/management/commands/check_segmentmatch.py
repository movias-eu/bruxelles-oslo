"""Query traverses together with their segment matches, future-proof.

    python manage.py check_segmentmatch

The join is driven by the existing integer FK
``traffic_segmentmatch.traverse_id -> traffic_uccomptagetraverses.id`` (one
traverse -> many matches), NOT by the varchar ``segment_id`` columns. Because the
relation is real, this exact query returns empty today and returns rows the moment
``traffic_segmentmatch`` is populated -- no query change needed then.

``prefetch_related("Traverse")`` follows the reverse accessor (the client's
``related_name="Traverse"``): one query for traverses, one for all their matches,
joined in memory. This is the shape a real OSLO export would iterate over.
"""
from django.core.management.base import BaseCommand

from traffic.models import UCComptageTraverses


class Command(BaseCommand):
    help = "List traverses with their segment matches (works empty now, populated later)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit", type=int, default=5,
            help="How many traverses to show (default 5).",
        )
        parser.add_argument(
            "--only-matched", action="store_true",
            help="Show only traverses that actually have >=1 segment match.",
        )

    def handle(self, *args, **options):
        qs = (
            UCComptageTraverses.objects
            .prefetch_related("Traverse")       # reverse FK -> segment matches
            .order_by("id")
        )
        if options["only_matched"]:
            # filter across the relation -- also future-proof, empty until data lands
            qs = qs.filter(Traverse__isnull=False).distinct()

        total_traverses = UCComptageTraverses.objects.count()
        total_matches = sum(t.Traverse.count() for t in UCComptageTraverses.objects.all())
        self.stdout.write(
            f"{total_traverses} traverses, {total_matches} segment matches total"
        )
        if total_matches == 0:
            self.stdout.write(
                "(segmentmatch is empty -- query is valid and will populate "
                "automatically once rows exist)"
            )

        for t in qs[: options["limit"]]:
            matches = list(t.Traverse.all())   # prefetched; no extra query
            self.stdout.write(f"\n{t.traverse_id} (id={t.id}) -> {len(matches)} match(es)")
            for m in matches:
                self.stdout.write(
                    f"    match_id={m.match_id!r} status={m.status!r} "
                    f"segment_id={m.segment_id!r} resolved_at={m.resolved_at}"
                )
