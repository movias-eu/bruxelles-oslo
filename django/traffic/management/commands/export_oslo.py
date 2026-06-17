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

from traffic.models import UCComptageTraverses, SegmentMatch
from traffic.oslo.preprocess import preprocess, flatten_segment_matches
import json

OSLO_DIR = Path(__file__).resolve().parents[2] / "oslo"
MAPPING = OSLO_DIR / "traverses.rml.ttl"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_JAR = str(PROJECT_ROOT / "vendor" / "rmlmapper.jar")

# Bound on the output graph so rdflib uses these names instead of ns1/ns2/...
# (RMLMapper's prefixes don't survive the N-Triples round-trip). Mirrors the
# prefixes declared in traverses.rml.ttl.
OSLO_PREFIXES = {
    "loc": "http://example.org/id/measureLocation/",
    "verkeer": "https://data.vlaanderen.be/ns/verkeersmetingen#",
    "iso-sp": "http://def.isotc211.org/iso19156/2011/SamplingPoint#",
    "geo": "http://www.opengis.net/ont/geosparql#",
    "sf": "http://www.opengis.net/ont/sf#",
    "impl": "https://implementatie.data.vlaanderen.be/ns/verkeersmetingen-uitwisseling#",
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
        # Defer `offset`: it is declared on the model but not yet a real DB
        # column, so it must be excluded from the SELECT or Postgres errors.
        # Remove the .defer("offset") once the column exists.
        matches_qs = SegmentMatch.objects.defer("offset")
        traverses = (
            UCComptageTraverses.objects
            .prefetch_related(Prefetch("Traverse", queryset=matches_qs))
            .order_by("id")
        )
        fc = preprocess(traverses)
        self.stdout.write(f"prepared {len(fc['features'])} traverse features")

        # -------------------------------------------------------------------
        # TEMPORARY MOCK -- REMOVE once traffic_segmentmatch is populated.
        # traffic_segmentmatch is currently empty, so every traverse comes out
        # with segmentMatches == []. To exercise the segment-match path end to
        # end (preprocess -> RML -> RDF) we inject ONE fake match into the first
        # feature. This is purely test scaffolding: it writes nothing to the DB
        # and must be deleted the moment real match rows exist, otherwise it
        # will fabricate a bogus match on top of the real data.
        if fc["features"]:
            # Fictive hex-WKB LINESTRING near ROG_TD1 (Brussels); decoded to
            # line/begin/end WKT by preprocess.wkb_to_geometry, same as real data.
            from traffic.oslo.preprocess import wkb_to_geometry
            from shapely.geometry import LineString
            mock_wkb = LineString(
                [(4.355297, 50.856096), (4.356000, 50.856500), (4.356800, 50.857000)]
            ).wkb_hex
            mock_match = {
                "match_id": "MOCK-0001", "status": "resolved", "segment_id": "SEG_42",
                "wkb": mock_wkb,
                "geometry": wkb_to_geometry(mock_wkb),
                "offset": 42.5,   # metres from segment start (mock; column not in DB yet)
            }
            fc["features"][0]["properties"]["segmentMatches"] = [mock_match]
            # Rebuild the flat top-level array the RML maps iterate, so the mock
            # is included (preprocess() already flattened the empty originals).
            fc["segmentMatches"] = flatten_segment_matches(fc["features"])
            self.stdout.write(self.style.WARNING(
                "INJECTED mock segment match into "
                f"{fc['features'][0]['properties']['name']} -- remove before production"
            ))
        # -------------------------------------------------------------------

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
