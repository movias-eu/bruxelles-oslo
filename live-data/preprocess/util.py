"""Shared helpers for the per-feed preprocessors.

Every feed preprocessor returns a list of rows in ONE schema, so the RML
mappings stay feed-agnostic. Each row:
    locId, measSubject, foi, kind, value, startISO, endISO, sensor, vehicle
    [, direction]
where measSubject is the URI-safe measurement key (identity + start id) and
direction is an INSPIRE LinkDirectionValue local name (optional).
"""
import csv
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta

FOI_COUNT = "aantal"
FOI_SPEED = "tijdsgemiddelde_snelheid"

# Per-feed OSLO codelist values (from traffic_counts_codelist_mapping.md).
SENSOR = {"tlc": "standaard_Camera", "dai": "standaard_Camera",
          "bike_live": "glasvezel", "bike_summary": "glasvezel"}
VEHICLE = {"tlc": "auto", "dai": "auto", "bike_live": "fiets", "bike_summary": "fiets"}

# INSPIRE LinkDirectionValue local names accepted as a traverse direction.
LINK_DIRECTIONS = ("inDirection", "inOppositeDirection", "bothDirections")


# --- Time ---------------------------------------------------------------------
# The live feeds carry naive local Brussels wall-clock times. OSLO's
# time:inXSDDateTimeStamp needs an explicit offset, so we add the one in effect
# (CET +01:00 / CEST +02:00). Python 3.8 (the client's runtime) has no zoneinfo,
# so the EU rule is applied directly: summer time runs from the last Sunday of
# March 02:00 local to the last Sunday of October 03:00 local.

def _last_sunday(year, month):
    """Date of the last Sunday of a 31-day month (March or October)."""
    d = date(year, month, 31)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def brussels_utc_offset(local_dt):
    """Naive Brussels wall-clock datetime -> its UTC offset (timedelta).

    The hour that occurs twice when summer time ends (last Sunday of October,
    02:00-02:59) is read as the first occurrence (+02:00): the naive value does
    not say which one is meant. The hour skipped in March (02:00-02:59, which
    no clock shows) gets +01:00, like zoneinfo does.
    """
    start = datetime.combine(_last_sunday(local_dt.year, 3), time(3))
    end = datetime.combine(_last_sunday(local_dt.year, 10), time(3))
    return timedelta(hours=2) if start <= local_dt < end else timedelta(hours=1)


def brussels_now():
    """The current naive Brussels wall-clock time."""
    utc = datetime.utcnow().replace(microsecond=0)
    summer = utc + timedelta(hours=2)
    return summer if brussels_utc_offset(summer) == timedelta(hours=2) else utc + timedelta(hours=1)


def ids(dt):
    """Naive Brussels datetime -> (ISO string with offset, URI-safe UTC id).

    ISO: '2026-03-13T12:58:00+01:00'. The id is built from UTC
    ('20260313T115800Z') so it stays unique across the October clock change.
    """
    offset = brussels_utc_offset(dt)
    hours, rest = divmod(int(offset.total_seconds()), 3600)
    iso = f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}+{hours:02d}:{rest // 60:02d}"
    return iso, (dt - offset).strftime("%Y%m%dT%H%M%SZ")


def parse_ts(value, fmt):
    """Parse a timestamp string with the given format, or None if it fails."""
    try:
        return datetime.strptime(value, fmt)
    except (TypeError, ValueError):
        return None


def row(feed, loc, subj, foi, kind, value, start_iso, end_iso, direction=None):
    """Build one measurement row in the unified schema."""
    r = {
        "locId": loc,
        "measSubject": subj,
        "foi": foi,
        "kind": kind,
        "value": value,
        "startISO": start_iso,
        "endISO": end_iso,
        "sensor": SENSOR[feed],
        "vehicle": VEHICLE[feed],
    }
    if direction:
        r["direction"] = direction
    return r


def warn(msg):
    print(f"WARNING: {msg}", file=sys.stderr)


# --- Detector -> traverse -----------------------------------------------------
# The live TLC/DAI feeds report per detector (one lane), bike_summary per track
# of a bike counter. OSLO publishes cross-section counts (doorsnedetellingen)
# per traverse, so each detector is mapped to its traverse with the client's
# UCComptageDetectorTraverse table, exported to CSV by
# `django/manage.py export_live_traverse_map`.

class TraverseMap:
    """Detector <-> traverse links, plus each traverse's direction.

    CSV columns: detector_id, traverse_id[, link_direction]. One row per active
    UCComptageDetectorTraverse link; link_direction is the traverse's INSPIRE
    LinkDirectionValue (empty while unknown). A detector can belong to several
    traverses (e.g. a bike track counts towards the total and towards one
    direction); vehicle detectors belong to exactly one.
    """

    def __init__(self, rows):
        self.traverses_of = defaultdict(set)
        self.detectors_of = defaultdict(set)
        self.direction_of = {}
        for r in rows:
            det = (r.get("detector_id") or "").strip()
            trav = (r.get("traverse_id") or "").strip()
            if not det or not trav:
                continue
            self.traverses_of[det].add(trav)
            self.detectors_of[trav].add(det)
            direction = (r.get("link_direction") or "").strip()
            if direction in LINK_DIRECTIONS:
                self.direction_of[trav] = direction
            elif direction:
                warn(f"traverse {trav}: ignoring unknown link_direction {direction!r}")

    @classmethod
    def from_csv(cls, path):
        with open(path, newline="", encoding="utf-8-sig") as f:
            return cls(csv.DictReader(f))


def aggregate_by_traverse(feed, readings, traverse_map):
    """Per-detector readings -> per-traverse count and speed rows.

    ``readings``: dicts with detector, start, end (naive datetimes), count
    (float or None) and speed (km/h, float or None; negative = invalid).

    Per traverse and time window:
      * count = sum of the detector counts. Only published when EVERY detector
        linked to the traverse delivered a count for that window; a partial sum
        would understate the cross-section.
      * speed = vehicle-weighted mean of the detector speeds, i.e. the
        time-mean speed over all vehicles that passed. Only published when the
        window has vehicles and every detector that counted vehicles has a
        valid speed (>= 0). DAI uses -1 for "no valid speed".
    Readings of detectors without a traverse are skipped (reported once).
    """
    windows = defaultdict(dict)   # (traverse, start, end) -> {detector: reading}
    unmapped = set()
    for rd in readings:
        travs = traverse_map.traverses_of.get(rd["detector"])
        if not travs:
            unmapped.add(rd["detector"])
            continue
        for trav in travs:
            windows[(trav, rd["start"], rd["end"])][rd["detector"]] = rd
    if unmapped:
        warn(f"{feed}: {len(unmapped)} detector(s) without a traverse, skipped: "
             + ", ".join(sorted(unmapped)))

    rows, incomplete = [], set()
    for (trav, start, end), by_det in sorted(windows.items()):
        expected = traverse_map.detectors_of[trav]
        counted = {d: r for d, r in by_det.items() if r.get("count") is not None}
        if set(counted) != expected:
            incomplete.add(trav)
            continue
        total = sum(r["count"] for r in counted.values())
        start_iso, start_id = ids(start)
        end_iso, _ = ids(end)
        direction = traverse_map.direction_of.get(trav)
        subj = f"{trav}-{feed}"
        rows.append(row(feed, trav, f"{subj}-{FOI_COUNT}-{start_id}", FOI_COUNT,
                        "count", total, start_iso, end_iso, direction))

        moving = [r for r in counted.values() if r["count"] > 0]
        if total > 0 and all(r.get("speed") is not None and r["speed"] >= 0 for r in moving):
            speed = sum(r["speed"] * r["count"] for r in moving) / total
            rows.append(row(feed, trav, f"{subj}-{FOI_SPEED}-{start_id}", FOI_SPEED,
                            "speed", round(speed, 1), start_iso, end_iso, direction))
    if incomplete:
        warn(f"{feed}: skipped windows with missing detectors for {len(incomplete)} "
             "traverse(s): " + ", ".join(sorted(incomplete)))
    return rows
