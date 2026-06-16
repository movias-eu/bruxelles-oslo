"""Preprocess bike_summary_*.json into a flat list of measurements the RML
engine can map to OSLO Verkeersmetingen.

Why this exists
---------------
bike_summary is a three-level nested structure::

    [ { "device": "CEK049",
        "data": [ { "track": "Track1.1",
                    "detections": [ {"id", "start", "end", "count",
                                     "classesCount", "averageSpeed?",
                                     "averageTemperature?"}, ... ] } ] } ]

Two transforms are needed that the RML engine cannot do itself:

1. Parent context. A detection is only identifiable by its parent ``device`` +
   ``track`` + time, but Morph-KGC cannot reach *parent* fields from a deep
   ``rml:iterator`` (verified: referencing ``device`` from
   ``$[*].data[*].detections[*]`` yields zero triples). So we flatten the tree
   to one row per detection and stamp each row with ``device`` and ``track``.

2. One measurement per feature-of-interest. Each detection holds up to two
   measured characteristics -- the bike ``count`` (OSLO: aantal) and, when
   present, ``averageSpeed`` (tijdsgemiddelde_snelheid). Per OSLO these are two
   separate Verkeersmeting observations with distinct subject URIs. So each
   detection is exploded into one row per feature-of-interest:
     * always a {featureOfInterest: "aantal", value: <count>} row
     * if averageSpeed is present, a
       {featureOfInterest: "tijdsgemiddelde_snelheid", value: <averageSpeed>} row
   The mapping then iterates a uniform ``$[*]`` list of measurement rows.

It also: converts the ``start`` timestamp from ``DD/MM/YYYY HH:MM:SS`` to a
naive ISO ``startISO`` (``2026-02-14T23:15:00``) and a compact ``startId``
(no separators) for the subject URI; and lifts the dynamic-key ``classesCount``
object into an iterable ``classes`` list (RML cannot iterate arbitrary keys).

Mirrors the "API response" model: input is parsed JSON (a Python object), output
is a Python object. Swapping the file read for an API call changes nothing here.

Usage
-----
    # as a library (e.g. from Django)
    from preprocess_bike_summary import preprocess
    rows = preprocess(json.loads(api_response_body))

    # as a CLI: read a file, write the measurement list (then feed to rml_map.py)
    python preprocess_bike_summary.py bike_summary_20260215.json -o measurements.json
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# OSLO VkmVerkeersKenmerkType codes for each measured characteristic.
FOI_COUNT = "aantal"
FOI_SPEED = "tijdsgemiddelde_snelheid"

# Source timestamps look like "14/02/2026 23:15:00" (DD/MM/YYYY HH:MM:SS).
_SOURCE_TS = "%d/%m/%Y %H:%M:%S"


def _iso(timestamp: str) -> str | None:
    """Convert a source timestamp to a naive ISO string, or None if unparseable.

    "14/02/2026 23:15:00" -> "2026-02-14T23:15:00" (no timezone, as requested).
    """
    if not timestamp:
        return None
    try:
        return datetime.strptime(timestamp, _SOURCE_TS).isoformat()
    except ValueError:
        return None


def preprocess(data: list) -> list:
    """Flatten bike_summary into one row per measurement (feature-of-interest).

    Each detection becomes a count (aantal) row, plus a speed
    (tijdsgemiddelde_snelheid) row when ``averageSpeed`` is present. Every row
    carries the parent ``device``/``track``, ISO + compact-id forms of the start
    time, the time window ``end``, the expanded ``classes`` list, the
    ``featureOfInterest`` code, and the measured ``value``.
    """
    rows: list[dict] = []
    for device_entry in data:
        device = device_entry.get("device")
        for track_entry in device_entry.get("data", []):
            track = track_entry.get("track")
            for detection in track_entry.get("detections", []):
                start_iso = _iso(detection.get("start"))
                # A compact id form for the subject URI: drop the separators
                # so the timestamp is URI-safe (2026-02-14T231500).
                start_id = start_iso.replace(":", "").replace("-", "") if start_iso else None

                # Expand the dynamic-key classesCount into an iterable list.
                classes_count = detection.get("classesCount") or {}
                classes = [
                    {"vehicleClass": cls, "count": cnt}
                    for cls, cnt in classes_count.items()
                ]

                base = {
                    "device": device,
                    "track": track,
                    # URI-safe track form for subject/blank-node keys: track values
                    # are inconsistent in the source ("Track1.1" vs "Track 2.1"),
                    # and a space is illegal in a blank-node label, so strip it.
                    "trackId": track.replace(" ", "") if track else None,
                    "startISO": start_iso,
                    "startId": start_id,
                    "end": detection.get("end"),
                    "classes": classes,
                }

                # Always emit the count (aantal) measurement.
                rows.append({**base, "featureOfInterest": FOI_COUNT, "value": detection.get("count")})

                # Emit the speed measurement only when averageSpeed is present.
                if detection.get("averageSpeed") is not None:
                    rows.append({
                        **base,
                        "featureOfInterest": FOI_SPEED,
                        "value": detection["averageSpeed"],
                    })
    return rows


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Flatten bike_summary_*.json into a per-measurement list for RML mapping.",
    )
    parser.add_argument("input_file", type=Path, help="Path to bike_summary_*.json")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write the measurement list (JSON) here. Defaults to stdout.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.input_file.is_file():
        print(f"Input file not found: {args.input_file}", file=sys.stderr)
        return 1

    data = json.loads(args.input_file.read_text(encoding="utf-8"))
    rows = preprocess(data)

    output = json.dumps(rows, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output, encoding="utf-8")
        print(f"Wrote {len(rows)} measurement rows to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
