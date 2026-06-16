"""Materialise RDF from a data file and an RML mapping using Morph-KGC.

Usage:
    python rml_map.py <mapping_file> <input_file> [-o OUTPUT]

The data is streamed into the RML engine in memory, not by pointing the mapping
at a file on disk. The mapping declares an in-memory logical source -- in YARRRML
``access: "{data}"``, or in RML/Turtle the SD-Ontology form
``rml:source [ a sd:DatasetSpecification ; sd:name "data" ]`` -- and this script
reads the input file, parses it, and hands it to Morph-KGC via its documented
``python_source`` argument keyed by that source's name. Nothing is written to a
temp file and the mapping is never rewritten.

This mirrors an API response: the bytes come from a file today, but switching to
a live endpoint changes only how those bytes are obtained
(``requests.get(...).json()`` instead of reading the file) -- the mapping and the
rest of this script stay the same.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import morph_kgc
from morph_kgc.constants import IN_MEMORY_TYPES

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("rml_map")


def make_config(mapping_file: Path) -> str:
    """Build the Morph-KGC config string for a single mapping file."""
    return f"[DataSource1]\nmappings: {mapping_file}\n"


def in_memory_source_keys(config: str) -> list[str]:
    """Return the in-memory (python_source) keys the mapping declares.

    Rather than scanning the mapping text ourselves, we let Morph-KGC's own
    parser normalise it -- this works identically for YARRRML (access: "{data}")
    and RML/Turtle (the SD-Ontology sd:name form, which the parser rewrites to
    {data}), and ignores comments. An in-memory source has source_type in
    IN_MEMORY_TYPES and a logical_source_value of the form {key}.
    """
    cfg = morph_kgc.load_config_from_argument(config)
    rml_df = morph_kgc.retrieve_mappings(cfg)[0]
    rows = rml_df[["source_type", "logical_source_value"]].drop_duplicates()
    return [
        value[1:-1]  # strip the surrounding braces
        for source_type, value in rows.itertuples(index=False)
        if source_type in IN_MEMORY_TYPES
    ]


def load_source(path: Path) -> object:
    """Read a data file into the in-memory structure Morph-KGC expects.

    The raw text is returned as-is. For JSON this is deliberate: Morph-KGC's
    in-memory reader treats a top-level JSON *array* (e.g. bike_summary's
    ``[ {...}, ... ]``) as a flat row-list when handed a parsed Python ``list``,
    silently ignoring the rml:iterator and producing zero triples. Handing it
    the JSON *string* instead routes through Morph-KGC's JSONPath reader, which
    honours the iterator for both arrays and objects. A quick validity check on
    JSON inputs surfaces malformed data early with a clear error.

    This is the seam to swap for an API call: return ``response.text`` (the raw
    JSON body) instead of reading a file, and the rest of the pipeline is
    unchanged.
    """
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".json", ".geojson"):
        json.loads(text)  # validate; raises JSONDecodeError on malformed input
    return text


def build_python_source(config: str, input_file: Path) -> dict[str, object]:
    """Load the input file under the mapping's single in-memory source key.

    The mapping must declare exactly one in-memory source; the input file is
    loaded and supplied to Morph-KGC under that source's key.
    """
    keys = in_memory_source_keys(config)
    if not keys:
        raise ValueError(
            "No in-memory source found in the mapping. The logical source must "
            'name an in-memory key -- YARRRML access: "{data}", or RML/Turtle '
            'rml:source [ a sd:DatasetSpecification ; sd:name "data" ].'
        )
    if len(keys) != 1:
        raise ValueError(
            f"The mapping declares {len(keys)} in-memory sources ({', '.join(keys)}), "
            "but this script feeds a single input file. The mapping must declare "
            "exactly one source."
        )

    key = keys[0]
    logger.info("Loaded source {%s} <- %s", key, input_file)
    return {key: load_source(input_file)}


def materialize(input_file: Path, mapping_file: Path) -> "object":
    """Run Morph-KGC over in-memory data and return the resulting RDFLib graph."""
    config = make_config(mapping_file)
    python_source = build_python_source(config, input_file)

    logger.info("Materialising with mapping %s", mapping_file)
    return morph_kgc.materialize(config, python_source=python_source)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialise RDF from a data file using an RML (Morph-KGC) mapping.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  rml_map.py tlc.ttl tlc_20260313_1300.json -o out.ttl\n"
            "  rml_map.py dai.ttl dai_202602201350.xml -o out.ttl"
        ),
    )
    parser.add_argument(
        "mapping_file",
        type=Path,
        help="Path to the RML mapping file (Turtle).",
    )
    parser.add_argument(
        "input_file",
        type=Path,
        help="The source data file (JSON, GeoJSON, XML, ...). Read into memory "
        "and supplied to the mapping's in-memory source.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write the result to this file. Defaults to stdout.",
    )
    parser.add_argument(
        "-f",
        "--format",
        default="turtle",
        choices=("turtle", "nt", "ntriples", "xml", "n3", "json-ld", "nquads", "trig"),
        help="RDF serialisation format (default: turtle).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    for label, path in (("mapping file", args.mapping_file), ("input file", args.input_file)):
        if not path.is_file():
            logger.error("%s not found: %s", label.capitalize(), path)
            return 1

    try:
        graph = materialize(args.input_file, args.mapping_file)
    except ValueError as exc:
        logger.error("%s", exc)
        return 1

    serialized = graph.serialize(format=args.format)
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
        logger.info("Wrote %d triples to %s", len(graph), args.output)
    else:
        sys.stdout.write(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
