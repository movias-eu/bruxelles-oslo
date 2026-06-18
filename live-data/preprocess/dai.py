"""Preprocess DAI live data (dai_*.xml) into measurement rows.

DAI is a flat XML stream of <Measure> elements::

    <Measure>
      <Type>Occupancy|Speed|#C1|#C2|#C3</Type>
      <CameraId>3106</CameraId>
      <CameraName>ROG 106</CameraName>
      <LaneId>1</LaneId>
      <PeriodSec>60</PeriodSec>
      <Time>2026/02/20 13:50:00</Time>
      <Value>24</Value>
    </Measure>

Per (camera, lane, time) we SUM the per-class counts #C1/#C2/#C3 into one total
(aantal) and keep Speed (tijdsgemiddelde_snelheid). Occupancy is a percentage,
not a count -- no OSLO codelist match -- and is dropped.

Time handling: <Time> is the interval START (assumption -- it is minute-aligned
and matches the filename; verify against the DAI spec). The window END is
derived as Time + PeriodSec, and the temporal entity carries explicit begin +
end instants (no duration -- see mapping/dai.rml.ttl), like bike_live.

geobserveerdObject (location): the measure location is identified by CameraName
with spaces replaced by underscores (e.g. "LOU 110" -> "LOU_110"), which matches
the traverse ids in the metadata DB -- NOT by the numeric CameraId.
"""
import xml.etree.ElementTree as ET
from datetime import timedelta

from util import FOI_COUNT, FOI_SPEED, ids, parse_ts, row

FEED = "dai"
_TS = "%Y/%m/%d %H:%M:%S"   # "2026/02/20 13:50:00"


def preprocess(text):
    """dai_*.xml text -> list of measurement rows."""
    root = ET.fromstring(text)

    # Aggregate per (camera, lane, time): sum #Cn counts, keep one speed.
    agg = {}
    for m in root.findall(".//Measure"):
        get = lambda t: (m.findtext(t) or "").strip()
        mtype = get("Type")
        cam_name, lane, period, tm, val = (
            get("CameraName"), get("LaneId"), get("PeriodSec"), get("Time"), get("Value"))
        start = parse_ts(tm, _TS)
        if start is None:
            continue
        rec = agg.setdefault((cam_name, lane, tm),
                             {"count": 0.0, "has_count": False, "speed": None,
                              "period": period, "start": start})
        if mtype in ("#C1", "#C2", "#C3"):
            try:
                rec["count"] += float(val); rec["has_count"] = True
            except ValueError:
                pass
        elif mtype == "Speed":
            try:
                rec["speed"] = float(val)
            except ValueError:
                pass
        # Occupancy intentionally ignored.

    rows = []
    for (cam_name, lane, _tm), rec in agg.items():
        start = rec["start"]
        try:
            period_s = int(rec["period"])
        except (ValueError, TypeError):
            period_s = 60
        end = start + timedelta(seconds=period_s)
        start_iso, start_id = ids(start)
        end_iso, _ = ids(end)
        # Location id = CameraName with spaces -> underscores ("LOU 110" -> "LOU_110").
        loc = cam_name.replace(" ", "_")
        # Measurement subject keyed on the same loc + lane (stable, human-readable).
        cl = f"{loc}-{lane}"
        for present, foi, kind, value in (
            (rec["has_count"], FOI_COUNT, "count", rec["count"]),
            (rec["speed"] is not None, FOI_SPEED, "speed", rec["speed"]),
        ):
            if not present:
                continue
            r = row(FEED, loc, f"{cl}-{foi}-{start_id}", foi, kind, value, start_iso, None)
            # DAI carries explicit begin + end instants instead of a duration.
            del r["durationISO"]
            r["endISO"] = end_iso
            rows.append(r)
    return rows
