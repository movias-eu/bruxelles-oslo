"""Preprocess TLC live data (tlc_*.json) into per-traverse measurement rows.

TLC is a flat JSON list of per-detector counts, one detector per lane::

    { "counts": [ { "detector": "ARL_103_1",
                    "from_timestamp": "2026-03-13 12:58",
                    "to_timestamp":   "2026-03-13 12:59",
                    "volume": 1, "speed": 17.0, "occupancy": 6.0,
                    "validity": 1 }, ... ] }

Rows with validity == 0 are dropped; occupancy has no OSLO codelist match and
is dropped too. The detectors are then summed per traverse (ARL_103_1 +
ARL_103_2 -> ARL_103) with the detector -> traverse map, see
util.aggregate_by_traverse. The temporal entity carries explicit begin + end
instants (from_timestamp / to_timestamp, local Brussels time).
"""
import json

from util import aggregate_by_traverse, parse_ts

FEED = "tlc"
_TS = "%Y-%m-%d %H:%M"   # "2026-03-13 12:58"


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def preprocess(text, traverse_map):
    """tlc_*.json text -> list of per-traverse measurement rows."""
    data = json.loads(text)
    readings = []
    for c in data.get("counts", []):
        if c.get("validity") == 0:
            continue  # invalid reading dropped
        start = parse_ts(c.get("from_timestamp"), _TS)
        end = parse_ts(c.get("to_timestamp"), _TS)
        if start is None or end is None or not c.get("detector"):
            continue
        readings.append({
            "detector": c["detector"],
            "start": start,
            "end": end,
            "count": _number(c.get("volume")),
            "speed": _number(c.get("speed")),
        })
    return aggregate_by_traverse(FEED, readings, traverse_map)
