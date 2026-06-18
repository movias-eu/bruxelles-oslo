"""Preprocess bike_live data (bike_live.json) into measurement rows.

bike_live is a list of bike-counter devices, each carrying CUMULATIVE counts at
the moment the file was generated (`timestamp`)::

    { "device": "CEK049", "timestamp": "17/06/2026 10:59:02",
      "hourCount": 117, "dayCount": 1145, "yearCount": 384019, "tracks": [...] }

Each device yields THREE count (aantal) measurements -- hour, day, year -- whose
time windows are CALENDAR-ALIGNED to the timestamp. The window END is the device
timestamp (when the file was generated); the temporal entity carries an explicit
begin + end instant (no duration -- see mapping/bike_live.rml.ttl):

    timestamp = 17/06/2026 10:59:02   (the end instant for all three)
      hourCount -> begin 2026-06-17T10:00:00  (top of the hour)
      dayCount  -> begin 2026-06-17T00:00:00  (midnight)
      yearCount -> begin 2026-01-01T00:00:00  (Jan 1)

The `tracks` property is ignored (out of scope for now). Sensor = inductielus,
vehicle = fiets. Built on the shared row schema (util.row), with durationISO
replaced by endISO.
"""
import json

from util import FOI_COUNT, ids, parse_ts, row

FEED = "bike_live"
_TS = "%d/%m/%Y %H:%M:%S"   # "17/06/2026 10:59:02"


def preprocess(text):
    """bike_live.json text -> list of measurement rows (3 per device)."""
    devices = json.loads(text)
    rows = []
    for d in devices:
        ts = parse_ts(d.get("timestamp"), _TS)
        name = d.get("device")
        if ts is None or name is None:
            continue
        # End instant = the device timestamp (shared by all three windows).
        end_iso, _ = ids(ts)
        # Per count: calendar-aligned begin instant.
        windows = (
            ("hour", d.get("hourCount"), ts.replace(minute=0, second=0, microsecond=0)),
            ("day",  d.get("dayCount"),  ts.replace(hour=0, minute=0, second=0, microsecond=0)),
            ("year", d.get("yearCount"),
             ts.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)),
        )
        for period, value, start in windows:
            if value is None:
                continue
            start_iso, start_id = ids(start)
            r = row(FEED, name, f"{name}-{FOI_COUNT}-{period}-{start_id}",
                    FOI_COUNT, "count", value, start_iso, None)
            # bike_live carries an explicit end instant instead of a duration.
            del r["durationISO"]
            r["endISO"] = end_iso
            rows.append(r)
    return rows
