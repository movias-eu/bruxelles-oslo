"""Shared helpers for the per-feed preprocessors.

Every feed preprocessor returns a list of rows in ONE schema, so a single RML
mapping (mapping/live.rml.ttl) serves all feeds. Each row:
    locId, measSubject, foi, kind, value, startISO, durationISO, sensor, vehicle
where measSubject is the URI-safe measurement key (identity + start id).
"""
from datetime import datetime

FOI_COUNT = "aantal"
FOI_SPEED = "tijdsgemiddelde_snelheid"

# Per-feed OSLO codelist values (from traffic_counts_codelist_mapping.md).
SENSOR = {"tlc": "inductielus", "dai": "standaard_Camera", "bike_live": "inductielus"}
VEHICLE = {"tlc": "auto", "dai": "auto", "bike_live": "fiets"}


def ids(dt):
    """datetime -> (ISO string, URI-safe compact id)."""
    iso = dt.strftime("%Y-%m-%dT%H:%M:%S")
    return iso, iso.replace(":", "").replace("-", "")


def parse_ts(value, fmt):
    """Parse a timestamp string with the given format, or None if it fails."""
    try:
        return datetime.strptime(value, fmt)
    except (TypeError, ValueError):
        return None


def row(feed, loc, subj, foi, kind, value, start_iso, duration):
    """Build one measurement row in the unified schema."""
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
