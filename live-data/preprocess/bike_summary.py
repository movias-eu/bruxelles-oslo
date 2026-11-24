"""Preprocess bike_summary data (bike_summary_*.json) into per-traverse rows.

bike_summary gives, per bike counter (device) and track, the count of each
finished 15-minute period::

    [ { "device": "CEK049",
        "data": [ { "track": "Track1.1",
                    "detections": [ { "id": 2,
                                      "start": "14/02/2026 23:15:00",
                                      "end":   "14/02/2026 23:30:00",
                                      "count": 1, "classesCount": {"3": 1},
                                      "averageSpeed": 25,
                                      "averageTemperature": 0 }, ... ] },
                  ... ] },
      ... ]

Unlike bike_live (running totals that keep growing until the hour, day or year
is over), every period here is closed: its count no longer changes, so each
measurement keeps one value.

Device + track is the detector of UCComptageDetector ("CEK049" + "Track1.1" ->
CEK049_Track1.1). The tracks are summed per traverse with the detector ->
traverse map, like TLC and DAI (see util.aggregate_by_traverse). A track can
count towards several traverses, e.g. CB1101_Track1.1 towards CB1101 (total)
and CB1101_inner_ring (one direction).

- count = `count` (equals the sum of classesCount); speed = `averageSpeed`
  (km/h), absent when nothing passed. classesCount and averageTemperature have
  no OSLO mapping and are dropped.
- start / end are taken as local Brussels time, like bike_live. To confirm:
  the 15/02 sample runs from 14/02 23:00 to 15/02 22:45, which would also fit
  UTC.
- Periods that end after the current time are skipped, so a period that is
  still running never gets published.
"""
import json
import sys

from util import aggregate_by_traverse, brussels_now, parse_ts

FEED = "bike_summary"
_TS = "%d/%m/%Y %H:%M:%S"   # "14/02/2026 23:15:00"


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def preprocess(text, traverse_map, now=None):
    """bike_summary JSON text -> list of per-traverse measurement rows.

    ``now`` (naive local Brussels time, default: the current time) is the
    cut-off: periods ending after it are still running and are skipped.
    """
    now = now or brussels_now()
    readings = []
    running = 0
    for dev in json.loads(text):
        device = dev.get("device")
        for track in dev.get("data") or []:
            name = track.get("track")
            if not device or not name:
                continue
            for d in track.get("detections") or []:
                start = parse_ts(d.get("start"), _TS)
                end = parse_ts(d.get("end"), _TS)
                if start is None or end is None:
                    continue
                if end > now:
                    running += 1
                    continue
                readings.append({
                    "detector": f"{device}_{name}",
                    "start": start,
                    "end": end,
                    "count": _number(d.get("count")),
                    "speed": _number(d.get("averageSpeed")),
                })
    if running:
        print(f"{FEED}: skipped {running} period(s) that end after {now}",
              file=sys.stderr)
    return aggregate_by_traverse(FEED, readings, traverse_map)
