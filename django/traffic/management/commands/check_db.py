"""Prove the ORM connects to the real webtools DB and reads traverses.

    python manage.py check_db

This is the smallest possible 'Django connector': it reads rows via the ORM
(reusing Django's own DB connection + the models' field/codelist semantics)
exactly as a real OSLO-export command would, then prints a summary. It writes
nothing.
"""
from django.core.management.base import BaseCommand
from django.db import connection

from traffic.models import VEH_TYPES, UCComptageTraverses


class Command(BaseCommand):
    help = "Read-only sanity check against the webtools traverse table."

    def handle(self, *args, **options):
        self.stdout.write(f"Connected via: {connection.settings_dict['HOST']}"
                          f"/{connection.settings_dict['NAME']} "
                          f"as {connection.settings_dict['USER']}")

        total = UCComptageTraverses.objects.count()
        self.stdout.write(f"traffic_uccomptagetraverses rows: {total}")

        self.stdout.write("veh_type breakdown (decoded via Django VEH_TYPES):")
        for code, label in sorted(VEH_TYPES.items()):
            n = UCComptageTraverses.objects.filter(veh_type=code).count()
            self.stdout.write(f"  {code} = {label:<6} : {n}")

        self.stdout.write("first 3 traverses (with FK link description):")
        for t in UCComptageTraverses.objects.select_related("link_id").order_by("id")[:3]:
            link = t.link_id.descr_short_fr if t.link_id else None
            self.stdout.write(
                f"  {t.traverse_id:<10} veh={t.veh_type_label:<6} "
                f"LB72=({t.co_x},{t.co_y}) zone={t.zone_geographic!r} link={link!r}"
            )
