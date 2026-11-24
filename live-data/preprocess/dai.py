"""Preprocess DAI live data (dai_*.xml) into per-traverse measurement rows.

DAI is a flat XML stream of <Measure> elements, one per camera, lane and type::

    <Measure>
      <Type>Occupancy|Speed|#C1|#C2|#C3</Type>
      <CameraId>3106</CameraId>
      <CameraName>ROG 106</CameraName>
      <LaneId>1</LaneId>
      <PeriodSec>60</PeriodSec>
      <Time>2026/02/20 13:50:00</Time>
      <Value>24</Value>
    </Measure>

Per (camera, lane, time) the per-class counts #C1/#C2/#C3 are summed into one
count and Speed is kept; Occupancy is a percentage, not a count, and is dropped.
A Speed of -1 means "no valid speed": it is ignored, the count is kept.

Camera + lane is the detector: "ARL 103" lane 1 -> detector ARL_103_1, the
detector_id in the client's UCComptageDetector table. The detectors are then
summed per traverse with the detector -> traverse map (util.aggregate_by_traverse).
Lanes the table does not know (e.g. lane 100, the camera's own total) have no
traverse and are skipped.

Time handling: <Time> is taken as the interval START (it is minute-aligned and
matches the filename), in local Brussels time; the end is Time + PeriodSec.
"""
import xml.etree.ElementTree as ET
from datetime import timedelta

from util import aggregate_by_traverse, parse_ts

FEED = "dai"
_TS = "%Y/%m/%d %H:%M:%S"   # "2026/02/20 13:50:00"


def preprocess(text, traverse_map):
    """dai_*.xml text -> list of per-traverse measurement rows."""
    root = ET.fromstring(text)

    # Per (camera, lane, time): sum #Cn counts, keep the speed.
    agg = {}
    for m in root.findall(".//Measure"):
        get = lambda t: (m.findtext(t) or "").strip()  # noqa: E731
        cam_name, lane, tm = get("CameraName"), get("LaneId"), get("Time")
        start = parse_ts(tm, _TS)
        if start is None or not cam_name or not lane:
            continue
        try:
            period_s = int(get("PeriodSec"))
        except ValueError:
            period_s = 60
        detector = f"{cam_name.replace(' ', '_')}_{lane}"
        rec = agg.setdefault((detector, start), {
            "detector": detector, "start": start,
            "end": start + timedelta(seconds=period_s),
            "count": None, "speed": None,
        })
        try:
            value = float(get("Value"))
        except ValueError:
            continue
        mtype = get("Type")
        if mtype in ("#C1", "#C2", "#C3"):
            rec["count"] = (rec["count"] or 0.0) + value
        elif mtype == "Speed":
            rec["speed"] = value if value >= 0 else None   # -1 = no valid speed
        # Occupancy intentionally ignored.

    return aggregate_by_traverse(FEED, list(agg.values()), traverse_map)
