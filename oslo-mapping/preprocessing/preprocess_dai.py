"""Preprocess dai_*.xml into a flat JSON measurement list the RML engine can map.

Why this exists
---------------
DAI is a flat XML stream of <Measure> elements::

    <CitiEvent Type="Measure">
      <Measure>
        <Type>Occupancy|Speed|#C1|#C2|#C3</Type>
        <CameraId>3106</CameraId>
        <CameraName>ROG 106</CameraName>
        <LaneId>1</LaneId>
        <PeriodSec>60</PeriodSec>
        <Time>2026/02/20 13:50:00</Time>
        <Value>24</Value>
      </Measure>
      ...
    </CitiEvent>

The <Measure> elements are already flat, but several transforms are needed that
are awkward/impossible to do declaratively in the mapping, so they happen here:

1. Feature-of-interest classification + count aggregation. The ``Type`` field is
   the measured characteristic. We keep two:
     * ``#C1`` / ``#C2`` / ``#C3`` are per-vehicle-class COUNTS. The digit is a
       vehicle class (analysis confirmed these are independent counts whose sum
       is the total volume; the class->vehicle-type meaning is undocumented in
       both the codelist doc and schema.json, like the bike subtypes). We SUM
       them per (camera, lane, time) into a single total -> one count (aantal).
     * ``Speed`` -> tijdsgemiddelde_snelheid.
   ``Occupancy`` is a separate occupancy PERCENTAGE, not a count (verified: it
   does not equal sum(#Cn) -- it is sometimes higher, sometimes lower). It has
   no VkmVerkeersKenmerkType match and is DROPPED.

2. Timestamp. ``2026/02/20 13:50:00`` (YYYY/MM/DD) -> naive ISO ``startISO``
   (``2026-02-20T13:50:00``) plus a URI-safe ``startId`` (``20260220T135000``).

3. Duration. ``PeriodSec`` (e.g. 60) -> an xsd:duration ``durationISO``
   (``PT60S``) for the observation's time window.

Each output row is one measurement, shaped like the bike_summary output so the
mapping is consistent across feeds: camera, lane, featureOfInterest, value,
startISO, startId, durationISO.

Mirrors the "API response" model: input is the raw XML text (or bytes from an
API), output is a Python object. Swapping the file read for an API call changes
only how the XML string is obtained.

Usage
-----
    # as a library (e.g. from Django)
    from preprocess_dai import preprocess
    rows = preprocess(xml_text)

    # as a CLI: read an XML file, write the measurement list (then feed rml_map.py)
    python preprocess_dai.py dai_202602201350.xml -o dai_measurements.json
"""

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# OSLO VkmVerkeersKenmerkType codes.
FOI_COUNT = "aantal"
FOI_SPEED = "tijdsgemiddelde_snelheid"

# DAI Type -> feature-of-interest. #Cn classes are counts; Speed is speed.
# Occupancy is intentionally absent (dropped: no OSLO codelist match).
_SPEED_TYPE = "Speed"
_CLASS_PREFIX = "#C"

# Source timestamps look like "2026/02/20 13:50:00" (YYYY/MM/DD HH:MM:SS).
_SOURCE_TS = "%Y/%m/%d %H:%M:%S"


def _iso(timestamp: str) -> str | None:
    """Convert a source timestamp to a naive ISO string, or None if unparseable.

    "2026/02/20 13:50:00" -> "2026-02-20T13:50:00" (no timezone).
    """
    from datetime import datetime

    if not timestamp:
        return None
    try:
        return datetime.strptime(timestamp, _SOURCE_TS).isoformat()
    except ValueError:
        return None


def _int(value: str | None) -> int:
    """Parse an integer value, treating missing/blank as 0."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def preprocess(xml_text: str) -> list:
    """Flatten dai XML into per-measurement rows: one total count + speed per lane.

    Per (camera, lane, time): the ``#C1``/``#C2``/``#C3`` class counts are summed
    into a single total count (aantal) row, and ``Speed`` (if present) becomes a
    tijdsgemiddelde_snelheid row. ``Occupancy`` is dropped. Each row carries the
    camera/lane, ISO + URI-safe start time, and an ISO duration from PeriodSec.
    """
    root = ET.fromstring(xml_text)

    # Group measures by (camera, lane, time) so the class counts can be summed
    # and lined up with the lane's speed.
    groups: dict[tuple, dict] = {}
    for measure in root.findall("Measure"):
        camera = measure.findtext("CameraId")
        lane = measure.findtext("LaneId")
        time = measure.findtext("Time")
        key = (camera, lane, time)
        g = groups.setdefault(key, {
            "camera": camera,
            "cameraName": measure.findtext("CameraName"),
            "lane": lane,
            "time": time,
            "period": (measure.findtext("PeriodSec") or "").strip(),
            "count": 0,
            "hasCount": False,
            "speed": None,
        })

        mtype = (measure.findtext("Type") or "").strip()
        if mtype == _SPEED_TYPE:
            g["speed"] = measure.findtext("Value")
        elif mtype.startswith(_CLASS_PREFIX):
            g["count"] += _int(measure.findtext("Value"))
            g["hasCount"] = True
        # Occupancy and anything else: ignored.

    rows: list[dict] = []
    for g in groups.values():
        start_iso = _iso(g["time"])
        start_id = start_iso.replace(":", "").replace("-", "") if start_iso else None
        duration_iso = f"PT{g['period']}S" if g["period"] else None
        base = {
            "camera": g["camera"],
            "cameraName": g["cameraName"],
            "lane": g["lane"],
            "startISO": start_iso,
            "startId": start_id,
            "durationISO": duration_iso,
        }

        # Total count (sum of the class counts) -- only when class counts existed.
        if g["hasCount"]:
            rows.append({**base, "featureOfInterest": FOI_COUNT, "value": g["count"]})

        # Speed, when present.
        if g["speed"] is not None:
            rows.append({**base, "featureOfInterest": FOI_SPEED, "value": g["speed"]})

    return rows


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Flatten dai_*.xml into a per-measurement JSON list for RML mapping.",
    )
    parser.add_argument("input_file", type=Path, help="Path to dai_*.xml")
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

    xml_text = args.input_file.read_text(encoding="utf-8")
    rows = preprocess(xml_text)

    output = json.dumps(rows, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output, encoding="utf-8")
        print(f"Wrote {len(rows)} measurement rows to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
