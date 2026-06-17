"""Turn traverse ORM rows into the GeoJSON FeatureCollection the RML mapping maps.

This mirrors the oslo-mapping/preprocessing/* scripts: the engine stays
declarative, and this module does the things RML cannot -- coordinate
reprojection, codelist decode, WKT assembly. The output shape matches
oslo-mapping/bike_devices_prepared.json (a FeatureCollection of measure-location
features), so the traverse RML mapping is a close sibling of bike_devices.

Pure function of ORM rows -> dict; no DB access here (the command does the query
and hands rows in), so it is trivially testable and reusable.
"""
from functools import lru_cache

# Traverse coordinates are Belgian Lambert 72; OSLO/GeoSPARQL wants WGS84 lon/lat.
_LB72 = "EPSG:31370"
_WGS84 = "EPSG:4326"


@lru_cache(maxsize=1)
def _transformer():
    # Imported lazily so importing this module never hard-fails if pyproj is
    # absent; the transform is only needed when we actually have coordinates.
    from pyproj import Transformer

    return Transformer.from_crs(_LB72, _WGS84, always_xy=True)


def lb72_to_wgs84_wkt(co_x, co_y):
    """(co_x, co_y) in Lambert 72 -> 'POINT(lon lat)' WKT, or None if no coords."""
    if co_x is None or co_y is None:
        return None
    lon, lat = _transformer().transform(co_x, co_y)
    return f"POINT({lon:.6f} {lat:.6f})"


def wkb_to_geometry(hex_wkb):
    """Hex-WKB string -> {'line': WKT, 'start': POINT WKT, 'end': POINT WKT}.

    The segment match carries its road-segment geometry as a (hex-encoded) WKB
    in the ``wkb`` text column. OSLO/bike_devices wants three derived WKT values:
    the line itself plus its begin and end nodes. RML can do none of this, so we
    decode here. Handles LineString and MultiLineString (begin = first coord of
    the first part, end = last coord of the last part). Returns None if absent or
    unparseable.
    """
    if not hex_wkb:
        return None
    from shapely import wkb as _wkb

    try:
        geom = _wkb.loads(hex_wkb, hex=True)
    except Exception:
        return None

    if geom.geom_type == "LineString":
        coords = list(geom.coords)
    elif geom.geom_type == "MultiLineString":
        parts = list(geom.geoms)
        # begin = first coord of the first part, end = last coord of the last part
        coords = list(parts[0].coords[:1]) + list(parts[-1].coords[-1:])
    else:
        return None

    # lineType drives the sf: class in the mapping, so a MultiLineString is typed
    # sf:MultiLineString -- not hard-coded to sf:LineString.
    return {
        "line": geom.wkt,
        "lineType": geom.geom_type,        # "LineString" | "MultiLineString"
        "start": f"POINT({coords[0][0]} {coords[0][1]})",
        "end": f"POINT({coords[-1][0]} {coords[-1][1]})",
    }


def traverse_to_feature(t):
    """One traverse ORM object -> one GeoJSON Feature dict.

    ``t`` must expose the traverse fields and ``veh_type_label`` (the decoded
    VEH_TYPES value) and ``Traverse`` (its prefetched segment matches).
    """
    wkt = lb72_to_wgs84_wkt(t.co_x, t.co_y)
    matches = [
        {
            "match_id": m.match_id,
            "status": m.status,
            "segment_id": m.segment_id,
            # WKB (hex text) -> {line, start, end} WKT; None if no/invalid geometry.
            "geometry": wkb_to_geometry(getattr(m, "wkb", None)),
            # offset (metres from segment start) is NOT yet a real DB column, so
            # the query defers it -> getattr returns None for real rows. Mock
            # data supplies it directly. Reads cleanly today, automatically once
            # the column exists.
            "offset": getattr(m, "offset", None),
        }
        for m in t.Traverse.all()
    ]
    feature = {
        "type": "Feature",
        # WKT promoted to a top-level scalar: RMLMapper's JSONPath references a
        # flat field reliably, whereas nested geometry.wkt does not resolve.
        "wkt": wkt,
        "properties": {
            "name": t.traverse_id,
            "vehType": t.veh_type_label,            # VEH / BIKE / RADAR
            "zone": t.zone_geographic or None,
            "orientation": t.orientation,           # degrees, North=0
            "segmentMatches": matches,              # empty now, populated later
        },
    }
    return feature


def flatten_segment_matches(features):
    """Collect every feature's matches into one top-level array for the mapping.

    The RML segment-match maps iterate ``$.segmentMatches[*]`` and join back to
    their traverse on ``parentName`` -- a flat list is far simpler for RMLMapper
    than a nested per-feature array with a cross-level join. ``matchId`` is the
    per-match key used to join Rijrichting -> Wegsegment.
    """
    out = []
    for feat in features:
        name = feat["properties"]["name"]
        for m in feat["properties"].get("segmentMatches", []):
            geom = m.get("geometry") or {}
            out.append({
                "parentName": name,
                "matchId": m["match_id"],
                "segmentId": m["segment_id"],
                "status": m["status"],
                # Flattened WKT scalars (RMLMapper can't reach nested fields).
                "lineWkt": geom.get("line"),
                "lineType": geom.get("lineType"),   # LineString | MultiLineString
                "startWkt": geom.get("start"),
                "endWkt": geom.get("end"),
                "offset": m.get("offset"),          # metres from segment start
            })
    return out


def preprocess(traverses):
    """Iterable of traverse ORM objects -> GeoJSON FeatureCollection dict.

    Also carries a top-level ``segmentMatches`` array (flattened across all
    features) that the segment-match RML maps iterate and join on.
    """
    features = [traverse_to_feature(t) for t in traverses]
    return {
        "type": "FeatureCollection",
        "features": features,
        "segmentMatches": flatten_segment_matches(features),
    }
