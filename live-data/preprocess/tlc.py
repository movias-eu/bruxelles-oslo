"""Preprocess TLC live data (tlc_*.json) into measurement rows.

TLC is a flat JSON list of per-detector counts::

    { "counts": [ { "detector": "ARL_103_1",
                    "from_timestamp": "2026-03-13 12:58",
                    "to_timestamp":   "2026-03-13 12:59",
                    "volume": 1, "speed": 17.0, "occupancy": 6.0,
                    "validity": 1 }, ... ] }

Each valid count yields a volume (aantal) row and, when present, a speed
(tijdsgemiddelde_snelheid) row -- only those two are mapped; occupancy has no
OSLO codelist match and is dropped, and rows with validity == 0 are dropped.

The temporal entity carries explicit begin + end instants (no duration): both
from_timestamp and to_timestamp are in the data, so the end is taken directly.
See mapping/tlc.rml.ttl. Identity = detector. Ported from PR #1's preprocess_tlc.py.
"""
import json

from util import FOI_COUNT, FOI_SPEED, ids, parse_ts, row

FEED = "tlc"
_TS = "%Y-%m-%d %H:%M"   # "2026-03-13 12:58"


def preprocess(text):
    """tlc_*.json text -> list of measurement rows."""
    data = json.loads(text)
    rows = []
    for c in data.get("counts", []):
        if c.get("validity") == 0:
            continue  # invalid reading dropped
        start = parse_ts(c.get("from_timestamp"), _TS)
        end = parse_ts(c.get("to_timestamp"), _TS)
        if start is None or end is None:
            continue
        start_iso, start_id = ids(start)
        end_iso, _ = ids(end)
        det = c.get("detector")
        for present, foi, kind, value in (
            (c.get("volume") is not None, FOI_COUNT, "count", c.get("volume")),
            (c.get("speed") is not None, FOI_SPEED, "speed", c.get("speed")),
        ):
            if not present:
                continue
            r = row(FEED, det, f"{det}-{foi}-{start_id}", foi, kind, value, start_iso, None)
            # TLC carries explicit begin + end instants (from/to_timestamp) instead
            # of a duration.
            del r["durationISO"]
            r["endISO"] = end_iso
            rows.append(r)
    return rows
