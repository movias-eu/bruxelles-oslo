"""Preprocess bike device data (bike_devices*_extended.json) into measure-location rows.

Unlike tlc/dai (which produce measurement observations), bike_devices is the
DEVICE/LOCATION registry: each Feature is a verkeer:Verkeersmeetpunt with a point
geometry, and (in the extended file) a road segment + measure direction.

Source Feature shape (extended)::

    { "type": "Feature",
      "geometry": { "coordinates": [lon, lat] },              # the point
      "roadSegmentGeometry": { "line": [[lon,lat],...], "offset": N },
      "properties": { "name": "CEK049", "measureDirection": "bothDirections", ... } }

RML cannot index coordinate arrays or derive begin/end nodes, so this builds the
WKT here: the point, the segment LINESTRING, and its start/end POINTs (first/last
coords of the line). Output row schema (one per feature):
    name, pointWkt, measureDirection, lineWkt, startWkt, endWkt, offset
Fields tied to the road segment are omitted when roadSegmentGeometry is absent
(so the non-extended file still maps to a bare Verkeersmeetpunt + point).
"""
import json


def _point_wkt(coords):
    """[lon, lat] -> 'POINT(lon lat)', or None."""
    if isinstance(coords, list) and len(coords) == 2:
        return f"POINT({coords[0]} {coords[1]})"
    return None


def _line_wkt(line):
    """[[lon,lat], ...] -> 'LINESTRING(lon lat, ...)', or None if < 2 points."""
    if not isinstance(line, list) or len(line) < 2:
        return None
    pts = ", ".join(f"{p[0]} {p[1]}" for p in line if isinstance(p, list) and len(p) == 2)
    return f"LINESTRING({pts})" if pts else None


def preprocess(text):
    """bike_devices_extended.json text -> list of measure-location rows."""
    data = json.loads(text)
    features = data.get("features", []) if isinstance(data, dict) else data
    rows = []
    for feat in features:
        props = feat.get("properties", {})
        name = props.get("name")
        point = _point_wkt((feat.get("geometry") or {}).get("coordinates"))

        row = {"name": name, "pointWkt": point}

        # Road segment + direction are optional (only in the extended file).
        rsg = feat.get("roadSegmentGeometry")
        if rsg and rsg.get("line"):
            line = rsg["line"]
            row["lineWkt"] = _line_wkt(line)
            # begin/end nodes = first/last coordinate of the line (RML can't derive these).
            row["startWkt"] = _point_wkt(line[0])
            row["endWkt"] = _point_wkt(line[-1])
            if rsg.get("offset") is not None:
                row["offset"] = rsg["offset"]
            if props.get("measureDirection") is not None:
                row["measureDirection"] = props["measureDirection"]

        rows.append(row)
    return rows
