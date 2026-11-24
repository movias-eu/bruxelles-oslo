"""Export a live feed sample to OSLO Verkeersmeting RDF via RMLMapper.

    python export_live.py --feed tlc --traverse-map traverse_map.csv \
        ../data-samples/tlc_20260313_1300.json -o tlc.ttl
    python export_live.py --feed dai --traverse-map traverse_map.csv \
        ../data-samples/dai_202602201350.xml -o dai.ttl
    python export_live.py --feed bike_summary --traverse-map traverse_map.csv \
        ../data-samples/bike_summary_20260215.json -o bike_summary.ttl

Reads a feed sample file, preprocesses it into the unified measurement schema,
then runs the feed's RML mapping to emit OSLO RDF. Same preprocess -> RML
pipeline as the other folders; ported from PR #1's Morph-KGC/YARRRML mappings.
TLC and DAI report per detector (lane), bike_summary per counter track; they
are summed per traverse with the detector -> traverse map
(`django/manage.py export_live_traverse_map`).

The input bytes come from a file here, mirroring an API response -- swapping the
file read for requests.get(...).text is the only change for a live endpoint.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAPPING_DIR = HERE / "mapping"
OUTPUT_DIR = HERE / "output"
sys.path.insert(0, str(HERE / "preprocess"))
import tlc            # noqa: E402  (one preprocess module per feed, in preprocess/)
import dai            # noqa: E402
import bike_live      # noqa: E402
import bike_summary   # noqa: E402
import bike_devices   # noqa: E402
from util import TraverseMap   # noqa: E402

# Per feed: its preprocess module + the RML mapping it feeds.
#   tlc / dai / bike_live / bike_summary -> measurement observations
#   bike_devices -> measure locations / Verkeersmeetpunt (bike_devices.rml.ttl)
MEASUREMENT_FEEDS = ("tlc", "dai", "bike_live", "bike_summary")
FEEDS = {
    "tlc":          {"module": tlc,          "mapping": "tlc.rml.ttl"},
    "dai":          {"module": dai,          "mapping": "dai.rml.ttl"},
    "bike_live":    {"module": bike_live,    "mapping": "bike_live.rml.ttl"},
    "bike_summary": {"module": bike_summary, "mapping": "bike_summary.rml.ttl"},
    "bike_devices": {"module": bike_devices, "mapping": "bike_devices.rml.ttl"},
}


# Feeds whose detectors are summed per traverse; they need --traverse-map.
TRAVERSE_FEEDS = ("tlc", "dai", "bike_summary")


def build_source(feed, text, traverse_map=None):
    """Run the feed's preprocessor and assemble the JSON its mapping iterates.

    All rows go under $.measurements. The measurement feeds also split
    by kind into $.counts / $.speeds for the count/speed observation maps; the
    location feed (bike_devices) only needs $.measurements.
    """
    module = FEEDS[feed]["module"]
    if feed in TRAVERSE_FEEDS:
        rows = module.preprocess(text, traverse_map)
    else:
        rows = module.preprocess(text)
    src = {"measurements": rows}
    if feed in MEASUREMENT_FEEDS:
        src["counts"] = [r for r in rows if r.get("kind") == "count"]
        src["speeds"] = [r for r in rows if r.get("kind") == "speed"]
    return src
# The RMLMapper jar is expected at the repo root (one level up); override with
# RMLMAPPER_JAR to place it elsewhere.
DEFAULT_JAR = HERE.parent / "rmlmapper.jar"

OSLO_PREFIXES = {
    "ex": "http://example.org/id/measurement/",
    "loc": "http://example.org/id/measureLocation/",
    "verkeer": "https://data.vlaanderen.be/ns/verkeersmetingen#",
    "impl": "https://implementatie.data.vlaanderen.be/ns/vsds-verkeersmetingen#",
    "iso19156-ob": "http://def.isotc211.org/iso19156/2011/Observation#",
    "iso19103-mp": "http://def.isotc211.org/iso19103/2005/MeasureProfile#",
    "sosa": "http://www.w3.org/ns/sosa/",
    "terms": "http://purl.org/dc/terms/",
    "schema": "http://schema.org/",
    "cdt": "https://w3id.org/cdt/",
    "time": "http://www.w3.org/2006/time#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "VkmVerkeersKenmerkType": "https://data.vlaanderen.be/id/concept/VkmVerkeersKenmerkType/",
    "VkmMeetInstrumentType": "https://data.vlaanderen.be/id/concept/VkmMeetInstrumentType/",
    "VkmVoertuigType": "https://data.vlaanderen.be/id/concept/VkmVoertuigType/",
    # bike_devices (location) shape:
    "dv-weg": "https://data.vlaanderen.be/ns/weg#",
    "dv-netwerk": "https://data.vlaanderen.be/ns/netwerk#",
    "iso-sp": "http://def.isotc211.org/iso19156/2011/SamplingPoint#",
    "geo": "http://www.opengis.net/ont/geosparql#",
    "sf": "http://www.opengis.net/ont/sf#",
    "LinkDirectionValue": "http://inspire.ec.europa.eu/codelist/LinkDirectionValue/",
}


def materialize(data, mapping, output, fmt, jar):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        data_path = tmp / "data.json"
        data_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        mapping_path = tmp / "mapping.ttl"
        mapping_path.write_text(
            mapping.read_text(encoding="utf-8").replace("SOURCE.json", str(data_path)),
            encoding="utf-8",
        )
        nt_path = tmp / "out.nt"
        cmd = ["java", "-jar", str(jar), "-m", str(mapping_path),
               "-o", str(nt_path), "-s", "ntriples"]
        print("running:", " ".join(cmd), file=sys.stderr)
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            sys.exit(f"rmlmapper failed (exit {res.returncode}):\n{res.stderr}")

        import rdflib
        g = rdflib.Graph()
        g.parse(str(nt_path), format="nt")
        for prefix, ns in OSLO_PREFIXES.items():
            g.bind(prefix, ns)
        g.serialize(destination=str(output), format=fmt)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--feed", required=True, choices=tuple(FEEDS))
    p.add_argument("input_file", type=Path, help="path to the feed sample (json/xml)")
    p.add_argument("-o", "--output", type=Path,
                   help="output file. A bare name lands in output/; "
                        "an explicit path is used as-is. Default: output/<feed>.ttl")
    p.add_argument("-f", "--format", default="turtle")
    p.add_argument("--jar", default=os.environ.get("RMLMAPPER_JAR", str(DEFAULT_JAR)))
    p.add_argument("--traverse-map", type=Path,
                   help="CSV detector_id,traverse_id[,link_direction] (required for "
                        "tlc/dai/bike_summary). Produced by `django/manage.py export_live_traverse_map`.")
    args = p.parse_args(argv)
    if args.feed in TRAVERSE_FEEDS and args.traverse_map is None:
        p.error(f"--traverse-map is required for --feed {args.feed}")

    # Resolve output: default output/<feed>.ttl; a bare filename -> output/<name>.
    output = args.output or Path(f"{args.feed}.ttl")
    if output.parent == Path("."):
        OUTPUT_DIR.mkdir(exist_ok=True)
        output = OUTPUT_DIR / output
    args.output = output

    if not args.input_file.is_file():
        sys.exit(f"input file not found: {args.input_file}")
    jar = Path(args.jar)
    if not jar.is_file():
        sys.exit(f"rmlmapper jar not found: {jar} (set RMLMAPPER_JAR or --jar)")

    traverse_map = None
    if args.feed in TRAVERSE_FEEDS:
        if not args.traverse_map.is_file():
            sys.exit(f"traverse map not found: {args.traverse_map}")
        traverse_map = TraverseMap.from_csv(args.traverse_map)

    text = args.input_file.read_text(encoding="utf-8")
    data = build_source(args.feed, text, traverse_map)
    n = len(data["measurements"])
    if args.feed in MEASUREMENT_FEEDS:
        print(f"prepared {n} measurements "
              f"({len(data['counts'])} count, {len(data['speeds'])} speed)", file=sys.stderr)
    else:
        print(f"prepared {n} measure locations", file=sys.stderr)
    mapping = MAPPING_DIR / FEEDS[args.feed]["mapping"]
    materialize(data, mapping, args.output, args.format, jar)
    print(f"wrote OSLO RDF -> {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
