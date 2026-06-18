"""Preprocess live feed samples into a unified measurement list for RML.

Two feeds, one output schema (so a single RML mapping serves both):

  TLC  (tlc_*.json)  -- flat per-detector counts; volume->aantal, speed->snelheid,
                        drop validity==0 rows and occupancy. Identity = detector.
  DAI  (dai_*.xml)   -- flat <Measure> stream; SUM #C1/#C2/#C3 per (camera,lane,time)
                        into one aantal, keep Speed->snelheid, drop Occupancy.
                        Identity = camera-lane.

Both ported from the Morph-KGC/YARRRML pipeline in PR #1 (closed), now feeding
RMLMapper. Each output row:
    locId, measSubject, foi, kind, value, startISO, durationISO, sensor, vehicle
where measSubject is the URI-safe measurement key (identity + startId).
"""
import re
import xml.etree.ElementTree as ET
from datetime import datetime

FOI_COUNT = "aantal"
FOI_SPEED = "tijdsgemiddelde_snelheid"

# Per-feed OSLO codelist values (from traffic_counts_codelist_mapping.md).
SENSOR = {"tlc": "inductielus", "dai": "standaard_Camera"}
VEHICLE = {"tlc": "auto", "dai": "auto"}

# Source timestamp formats.
_TLC_TS = "%Y-%m-%d %H:%M"      # "2026-03-13 12:58"
_DAI_TS = "%Y/%m/%d %H:%M:%S"   # "2026/02/20 13:50:00"


def _ids(dt):
    """datetime -> (ISO string, URI-safe compact id)."""
    iso = dt.strftime("%Y-%m-%dT%H:%M:%S")
    return iso, iso.replace(":", "").replace("-", "")


def _row(loc, subj, foi, kind, value, start_iso, duration, feed):
    return {
        "locId": loc,
        "measSubject": subj,
        "foi": foi,
        "kind": kind,
        "value": value,
        "startISO": start_iso,
        "durationISO": duration,
        "sensor": SENSOR[feed],
        "vehicle": VEHICLE[feed],
    }


def preprocess_tlc(text):
    """tlc_*.json text -> measurement rows. Identity = detector."""
    import json

    data = json.loads(text)
    rows = []
    for c in data.get("counts", []):
        if c.get("validity") == 0:
            continue  # invalid reading dropped
        try:
            start = datetime.strptime(c["from_timestamp"], _TLC_TS)
            end = datetime.strptime(c["to_timestamp"], _TLC_TS)
        except (KeyError, ValueError):
            continue
        start_iso, start_id = _ids(start)
        mins = max(1, int(round((end - start).total_seconds() / 60)))
        duration = f"PT{mins}M"
        det = c.get("detector")
        base = (det, f"{det}-{{foi}}-{start_id}")
        if c.get("volume") is not None:
            rows.append(_row(det, f"{det}-{FOI_COUNT}-{start_id}", FOI_COUNT,
                             "count", c["volume"], start_iso, duration, "tlc"))
        if c.get("speed") is not None:
            rows.append(_row(det, f"{det}-{FOI_SPEED}-{start_id}", FOI_SPEED,
                             "speed", c["speed"], start_iso, duration, "tlc"))
    return rows


def preprocess_dai(text):
    """dai_*.xml text -> measurement rows. Identity = camera-lane.

    Sums #C1/#C2/#C3 per (camera, lane, time) into one aantal; keeps Speed;
    drops Occupancy (a percentage, no OSLO codelist match).
    """
    root = ET.fromstring(text)
    # aggregate per (camera, lane, time)
    agg = {}
    for m in root.findall(".//Measure"):
        g = lambda t: (m.findtext(t) or "").strip()
        mtype, cam, lane, period, tm, val = (
            g("Type"), g("CameraId"), g("LaneId"), g("PeriodSec"), g("Time"), g("Value"))
        try:
            start = datetime.strptime(tm, _DAI_TS)
        except ValueError:
            continue
        key = (cam, lane, tm)
        rec = agg.setdefault(key, {"count": 0.0, "has_count": False, "speed": None,
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
    for (cam, lane, _tm), rec in agg.items():
        start_iso, start_id = _ids(rec["start"])
        try:
            duration = f"PT{int(rec['period'])}S"
        except (ValueError, TypeError):
            duration = "PT60S"
        loc = cam
        cl = f"{cam}-{lane}"
        if rec["has_count"]:
            rows.append(_row(loc, f"{cl}-{FOI_COUNT}-{start_id}", FOI_COUNT,
                             "count", rec["count"], start_iso, duration, "dai"))
        if rec["speed"] is not None:
            rows.append(_row(loc, f"{cl}-{FOI_SPEED}-{start_id}", FOI_SPEED,
                             "speed", rec["speed"], start_iso, duration, "dai"))
    return rows


def preprocess(feed, text):
    """Dispatch to the feed-specific preprocessor; return the JSON the mapping wants."""
    rows = {"tlc": preprocess_tlc, "dai": preprocess_dai}[feed](text)
    return {
        "measurements": rows,
        "counts": [r for r in rows if r["kind"] == "count"],
        "speeds": [r for r in rows if r["kind"] == "speed"],
    }
