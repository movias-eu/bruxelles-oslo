"""Preprocess bike_devices.json so the RML engine can emit a WKT point geometry.

Why this exists
---------------
bike_devices is a GeoJSON FeatureCollection; each feature's geometry is::

    "geometry": { "coordinates": [lon, lat] }

A proper OSLO/GeoSPARQL geometry is a single ``geo:asWKT "POINT(lon lat)"``
literal. Building that needs lon and lat combined in one RML template
(``POINT({coordinates[0]} {coordinates[1]})``), but Morph-KGC cannot index a
coordinate array inside a template for in-memory data -- the indexed references
are dropped and the feature produces no geometry.

So this script does the one thing the engine cannot: it derives, per feature, a
ready-made WKT string and places it where the mapping can reference it as a
plain field. It adds ``geometry.wkt = "POINT(<lon> <lat>)"`` (longitude first,
per GeoSPARQL) to each feature, leaving everything else untouched, and returns
the same FeatureCollection object.

The trilingual street names still iterate ``streetName[*]`` in the mapping (that
works in-memory), so only the geometry needs this derivation.

Mirrors the "API response" model: input is parsed JSON, output is a Python
object. Swapping the file read for an API call changes nothing here.

Usage
-----
    # as a library (e.g. from Django)
    from preprocess_bike_devices import preprocess
    data = preprocess(json.loads(api_response_body))

    # as a CLI
    python preprocess_bike_devices.py bike_devices.json -o bike_devices_prepared.json
"""

import argparse
import json
import sys
from pathlib import Path


def preprocess(data: dict) -> dict:
    """Add a WKT point (``geometry.wkt``) to each feature of the FeatureCollection.

    ``coordinates`` is [lon, lat]; the WKT is ``POINT(lon lat)`` (longitude
    first, GeoSPARQL convention). Features without a 2-element coordinate pair
    are left without a ``wkt`` field.
    """
    for feature in data.get("features", []):
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates")
        if isinstance(coordinates, list) and len(coordinates) == 2:
            lon, lat = coordinates[0], coordinates[1]
            geometry["wkt"] = f"POINT({lon} {lat})"
            feature["geometry"] = geometry
    return data


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add a WKT point geometry to each bike_devices feature for RML mapping.",
    )
    parser.add_argument("input_file", type=Path, help="Path to bike_devices.json")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write the prepared GeoJSON here. Defaults to stdout.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.input_file.is_file():
        print(f"Input file not found: {args.input_file}", file=sys.stderr)
        return 1

    data = json.loads(args.input_file.read_text(encoding="utf-8"))
    data = preprocess(data)

    output = json.dumps(data, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output, encoding="utf-8")
        n = len(data.get("features", []))
        print(f"Wrote {n} features (with WKT geometry) to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
