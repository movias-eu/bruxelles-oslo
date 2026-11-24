"""Export traverses to OSLO Verkeersmetingen RDF via RMLMapper.

    python manage.py export_oslo -o traverses.ttl

Pipeline (all on Python 3.8, the client's runtime):

    ORM rows  ->  preprocess()  ->  GeoJSON FeatureCollection (temp .json)
              ->  java -jar rmlmapper -m traverses.rml.ttl  ->  OSLO RDF

RMLMapper is a JVM tool, so the Python version is irrelevant to the mapping
engine. The mapping's logical source placeholder ("SOURCE.json") is rewritten to
the temp file path at run time -- the mapping on disk stays generic.
"""
import os
import subprocess
import tempfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from django.db.models import Prefetch

from traffic.columns import missing_columns
from traffic.models import UCComptageTraverses, SegmentMatch
from traffic.oslo.preprocess import preprocess

# Declared on the traverse mirror ahead of the client's migration.
NEW_TRAVERSE_FIELDS = ["link_direction", "link_direction_manual"]
import json

OSLO_DIR = Path(__file__).resolve().parents[2] / "oslo"
MAPPING = OSLO_DIR / "traverses.rml.ttl"
# The RMLMapper jar lives at the repo root (one level above django/), shared by
# all folders; override with RMLMAPPER_JAR to place it elsewhere.
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_JAR = str(REPO_ROOT / "rmlmapper.jar")

# Bound on the output graph so rdflib uses these names instead of ns1/ns2/...
# (RMLMapper's prefixes don't survive the N-Triples round-trip). Mirrors the
# prefixes declared in traverses.rml.ttl.
OSLO_PREFIXES = {
    "loc": "http://example.org/id/measureLocation/",
    "verkeer": "https://data.vlaanderen.be/ns/verkeersmetingen#",
    "iso-sp": "http://def.isotc211.org/iso19156/2011/SamplingPoint#",
    "geo": "http://www.opengis.net/ont/geosparql#",
    "sf": "http://www.opengis.net/ont/sf#",
    "impl": "https://implementatie.data.vlaanderen.be/ns/vsds-verkeersmetingen#",
    "dv-weg": "https://data.vlaanderen.be/ns/weg#",
    "dv-netwerk": "https://data.vlaanderen.be/ns/netwerk#",
    "iso-mp": "http://def.isotc211.org/iso19103/2005/MeasureProfile#",
    "schema": "http://schema.org/",
    "cdt": "https://w3id.org/cdt/",
    "LinkDirectionValue": "http://inspire.ec.europa.eu/codelist/LinkDirectionValue/",
}


class Command(BaseCommand):
    help = "Materialise OSLO Verkeersmeetpunt RDF for the traverses via RMLMapper."

    def add_arguments(self, parser):
        parser.add_argument("-o", "--output", type=Path, default=Path("traverses.ttl"))
        parser.add_argument("-f", "--format", default="turtle",
                            help="RMLMapper serialization (turtle, nquads, ...).")
        parser.add_argument("--jar", default=os.environ.get("RMLMAPPER_JAR", DEFAULT_JAR))
        parser.add_argument("--keep-json", action="store_true",
                            help="Keep the intermediate FeatureCollection JSON.")

    def handle(self, *args, **opts):
        jar = Path(opts["jar"])
        if not jar.is_file():
            raise CommandError(f"rmlmapper jar not found: {jar} (set RMLMAPPER_JAR)")
        if not MAPPING.is_file():
            raise CommandError(f"mapping not found: {MAPPING}")

        # 1. ORM -> GeoJSON FeatureCollection (decode + reproject happen here).
        # Fields declared on the mirror but not (yet) real columns are deferred,
        # otherwise Postgres errors on the SELECT. Nothing is deferred once the
        # client has added them.
        matches_qs = SegmentMatch.objects.defer(*missing_columns(SegmentMatch, ["offset"]))
        traverses = (
            UCComptageTraverses.objects
            .defer(*missing_columns(UCComptageTraverses, NEW_TRAVERSE_FIELDS))
            .prefetch_related(Prefetch("Traverse", queryset=matches_qs))
            .order_by("id")
        )
        fc = preprocess(traverses)
        self.stdout.write(f"prepared {len(fc['features'])} traverse features")

        # 2. Write temp JSON + a mapping copy pointing at it.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data_path = tmp / "traverses.json"
            data_path.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")

            mapping_text = MAPPING.read_text(encoding="utf-8").replace(
                "SOURCE.json", str(data_path)
            )
            mapping_path = tmp / "mapping.ttl"
            mapping_path.write_text(mapping_text, encoding="utf-8")

            # 3. RMLMapper -> N-Triples (a temp file). We let RMLMapper emit the
            #    flat triples and do the pretty-printing ourselves, because its
            #    Turtle writer only produces labelled blank nodes (_:b0); rdflib's
            #    nests single-use blanks inline as [ ... ], matching the other
            #    OSLO mappings' style.
            nt_path = tmp / "out.nt"
            cmd = [
                "java", "-jar", str(jar),
                "-m", str(mapping_path),
                "-o", str(nt_path),
                "-s", "ntriples",
            ]
            self.stdout.write("running: " + " ".join(cmd))
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise CommandError(
                    f"rmlmapper failed (exit {result.returncode}):\n{result.stderr}"
                )

            # 4. Re-serialize through rdflib to the requested format (pretty Turtle).
            # This step can be deleted if we want to rely on serialization of the RML Mapper,
            # but it does not output Turtle pretty.
            import rdflib

            g = rdflib.Graph()
            g.parse(str(nt_path), format="nt")
            for prefix, ns in OSLO_PREFIXES.items():
                g.bind(prefix, ns)
            g.serialize(destination=str(opts["output"]), format=opts["format"])

            if opts["keep_json"]:
                kept = Path(opts["output"]).with_suffix(".source.json")
                kept.write_text(data_path.read_text(encoding="utf-8"), encoding="utf-8")
                self.stdout.write(f"kept source JSON -> {kept}")

        self.stdout.write(self.style.SUCCESS(f"wrote OSLO RDF -> {opts['output']}"))
