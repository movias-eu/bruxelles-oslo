"""Export a live feed sample to OSLO Verkeersmeting RDF via RMLMapper.

    python export_live.py --feed tlc ../data-samples/tlc_20260313_1300.json -o tlc.ttl
    python export_live.py --feed dai ../data-samples/dai_202602201350.xml -o dai.ttl

Reads a feed sample file, preprocesses it into the unified measurement schema,
then runs RMLMapper (live.rml.ttl) to emit OSLO RDF. Same preprocess -> RML
pipeline as the other folders; ported from PR #1's Morph-KGC/YARRRML mappings.

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
sys.path.insert(0, str(HERE / "preprocess"))
from preprocess import preprocess  # noqa: E402  (lives in preprocess/)

MAPPING = HERE / "mapping" / "live.rml.ttl"
OUTPUT_DIR = HERE / "output"
# The RMLMapper jar is expected at the repo root (one level up); override with
# RMLMAPPER_JAR to place it elsewhere.
DEFAULT_JAR = HERE.parent / "rmlmapper.jar"

OSLO_PREFIXES = {
    "ex": "http://example.org/id/measurement/",
    "loc": "http://example.org/id/measureLocation/",
    "verkeer": "https://data.vlaanderen.be/ns/verkeersmetingen#",
    "impl": "https://implementatie.data.vlaanderen.be/ns/verkeersmetingen-uitwisseling#",
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
}


def materialize(data, output, fmt, jar):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        data_path = tmp / "data.json"
        data_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        mapping_path = tmp / "mapping.ttl"
        mapping_path.write_text(
            MAPPING.read_text(encoding="utf-8").replace("SOURCE.json", str(data_path)),
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
    p.add_argument("--feed", required=True, choices=("tlc", "dai"))
    p.add_argument("input_file", type=Path, help="path to the feed sample (json/xml)")
    p.add_argument("-o", "--output", type=Path,
                   help="output file. A bare name lands in output/; "
                        "an explicit path is used as-is. Default: output/<feed>.ttl")
    p.add_argument("-f", "--format", default="turtle")
    p.add_argument("--jar", default=os.environ.get("RMLMAPPER_JAR", str(DEFAULT_JAR)))
    args = p.parse_args(argv)

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

    text = args.input_file.read_text(encoding="utf-8")
    data = preprocess(args.feed, text)
    print(f"prepared {len(data['measurements'])} measurements "
          f"({len(data['counts'])} count, {len(data['speeds'])} speed)", file=sys.stderr)
    materialize(data, args.output, args.format, jar)
    print(f"wrote OSLO RDF -> {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
