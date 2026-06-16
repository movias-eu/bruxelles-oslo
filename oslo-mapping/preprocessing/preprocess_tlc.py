"""Preprocess tlc_*.json into a flat JSON measurement list the RML engine can map.

Why this exists
---------------
TLC is already a flat JSON list of per-detector counts::

    { "supplier": "Brussels Mobility",
      "counts": [ { "detector": "ARL_103_1",
                    "from_timestamp": "2026-03-13 12:58",
                    "to_timestamp":   "2026-03-13 12:59",
                    "volume": 1, "occupancy": 6.0, "speed": 17.0,
                    "validity": 1 }, ... ] }

The records are flat, but three transforms are done here so the mapping stays
declarative and consistent with the other feeds:

1. Feature-of-interest split. Each count holds two kept characteristics:
     * ``volume`` -> a count (OSLO: aantal)
     * ``speed``  -> tijdsgemiddelde_snelheid
   ``occupancy`` has no VkmVerkeersKenmerkType match and is DROPPED (per the
   codelist analysis). So each valid count becomes up to two measurement rows.

2. Validity filtering. Rows with ``validity == 0`` are DROPPED entirely (invalid
   readings are not emitted).

3. Timestamp. ``from_timestamp`` ``2026-03-13 12:58`` (YYYY-MM-DD HH:MM) ->
   naive ISO ``startISO`` (``2026-03-13T12:58:00``) plus URI-safe ``startId``
   (``20260313T125800``). The window duration is derived from from/to_timestamp
   as an ISO ``durationISO`` (typically ``PT1M``).

Each output row is one measurement: detector, featureOfInterest, value,
startISO, startId, durationISO. Shaped like the other feeds' output.

Mirrors the "API response" model: input is parsed JSON, output is a Python
object. Swapping the file read for an API call changes nothing here.

Usage
-----
    # as a library (e.g. from Django)
    from preprocess_tlc import preprocess
    rows = preprocess(json.loads(api_response_body))

    # as a CLI
    python preprocess_tlc.py tlc_20260313_1300.json -o tlc_measurements.json
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# OSLO VkmVerkeersKenmerkType codes.
FOI_COUNT = "aantal"
FOI_SPEED = "tijdsgemiddelde_snelheid"

# Source timestamps look like "2026-03-13 12:58" (YYYY-MM-DD HH:MM, no seconds).
_SOURCE_TS = "%Y-%m-%d %H:%M"


def _parse(timestamp: str) -> datetime | None:
    if not timestamp:
        return None
    try:
        return datetime.strptime(timestamp, _SOURCE_TS)
    except ValueError:
        return None


def _iso_duration(start: datetime | None, end: datetime | None) -> str | None:
    """ISO 8601 duration between two datetimes, in whole minutes (e.g. PT1M)."""
    if start is None or end is None:
        return None
    seconds = int((end - start).total_seconds())
    if seconds <= 0:
        return None
    minutes, rem = divmod(seconds, 60)
    if rem == 0:
        return f"PT{minutes}M" if minutes else None
    return f"PT{seconds}S"


def preprocess(data: dict) -> list:
    """Flatten TLC counts into one row per measurement (volume or speed).

    Drops rows with validity == 0. Each valid count yields a volume (aantal)
    row and a speed (tijdsgemiddelde_snelheid) row. Adds ISO + URI-safe start
    time and an ISO window duration.
    """
    rows: list[dict] = []
    for count in data.get("counts", []):
        if count.get("validity") == 0:
            continue  # invalid reading: dropped

        start_dt = _parse(count.get("from_timestamp"))
        end_dt = _parse(count.get("to_timestamp"))
        start_iso = start_dt.isoformat() if start_dt else None
        start_id = start_iso.replace(":", "").replace("-", "") if start_iso else None
        duration_iso = _iso_duration(start_dt, end_dt)

        base = {
            "detector": count.get("detector"),
            "startISO": start_iso,
            "startId": start_id,
            "durationISO": duration_iso,
        }

        # volume -> count (aantal)
        rows.append({**base, "featureOfInterest": FOI_COUNT, "value": count.get("volume")})

        # speed -> tijdsgemiddelde_snelheid (only when present)
        if count.get("speed") is not None:
            rows.append({**base, "featureOfInterest": FOI_SPEED, "value": count["speed"]})

    return rows


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Flatten tlc_*.json into a per-measurement list for RML mapping.",
    )
    parser.add_argument("input_file", type=Path, help="Path to tlc_*.json")
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
