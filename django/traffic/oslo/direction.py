"""Compute a traverse's INSPIRE LinkDirectionValue from its orientation.

OSLO expresses direction relative to the road segment the traverse is matched
to (``weg:rijrichting``, INSPIRE LinkDirectionValue):

  * inDirection          -- traffic drives the way the segment is digitised
  * inOppositeDirection  -- traffic drives against it
  * bothDirections       -- both directions are counted together

``orientation`` on the traverse is the compass heading of the measured traffic
(0 = north, 90 = east; confirmed by Brussels Mobility). Comparing it with the
heading of the matched segment AT the traverse location gives the value:

  angle <= 60 deg   -> inDirection
  angle >= 120 deg  -> inOppositeDirection
  in between        -> None: the orientation is nearly perpendicular to the
                       segment, which points at a wrong match or a wrong
                       orientation; leave it for a manual check.

Traverses without orientation (bike, radar) get None: their direction has to be
set by hand (bothDirections for bike totals, see the implementation notes).

Pure functions: no DB access, so the client can call them from their own code.
"""
import math
from functools import lru_cache
from typing import NamedTuple, Optional

LINK_DIRECTIONS = ("inDirection", "inOppositeDirection", "bothDirections")

SAME_MAX_DEG = 60.0
OPPOSITE_MIN_DEG = 120.0

_LB72 = "EPSG:31370"          # traverse co_x / co_y
_LAMBERT2008 = "EPSG:3812"    # metric CRS the bearings are computed in
_WGS84 = "EPSG:4326"


class Direction(NamedTuple):
    value: Optional[str]              # one of LINK_DIRECTIONS, or None
    reason: str
    segment_bearing: Optional[float] = None
    angle: Optional[float] = None


@lru_cache(maxsize=4)
def _to_lambert2008(src):
    from pyproj import Transformer

    return Transformer.from_crs(src, _LAMBERT2008, always_xy=True)


def _segment_geometry(hex_wkb):
    """Hex WKB of the matched segment -> shapely geometry in EPSG:3812, or None.

    The stored geometry is either lon/lat (EPSG:4326) or already metric
    (EPSG:3812, as the segment matcher requests it from the WFS); lon/lat is
    recognised by its coordinate range and reprojected.
    """
    from shapely import wkb
    from shapely.ops import transform

    try:
        geom = wkb.loads(hex_wkb, hex=True)
    except Exception:
        return None
    if geom.is_empty or geom.geom_type not in ("LineString", "MultiLineString"):
        return None
    minx, miny, maxx, maxy = geom.bounds
    if -180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90:
        geom = transform(_to_lambert2008(_WGS84).transform, geom)
    return geom


def bearing(x1, y1, x2, y2):
    """Compass bearing (0 = north, clockwise) from (x1, y1) to (x2, y2)."""
    return (math.degrees(math.atan2(x2 - x1, y2 - y1)) + 360.0) % 360.0


def angle_between(a, b):
    """Smallest angle between two bearings, 0..180."""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def local_bearing(geom, point):
    """Digitised heading of the segment at the point nearest to ``point``.

    Uses the vertex pair the point projects onto, not start -> end, so curved
    segments are handled. For a MultiLineString the part nearest the point is used.
    """
    if geom.geom_type == "MultiLineString":
        geom = min(geom.geoms, key=lambda part: part.distance(point))
    coords = [c[:2] for c in geom.coords]
    target = geom.project(point)
    walked = 0.0
    pairs = [(a, b) for a, b in zip(coords, coords[1:]) if a != b]
    for a, b in pairs:
        length = math.dist(a, b)
        if walked + length >= target:
            return bearing(*a, *b)
        walked += length
    a, b = pairs[-1]
    return bearing(*a, *b)


def compute_link_direction(orientation, segment_wkb, co_x, co_y):
    """One traverse + one matched segment -> Direction.

    ``orientation``: traverse heading in degrees (None / negative = unknown;
    values above 360 are taken modulo 360). ``segment_wkb``: hex WKB of the
    matched segment. ``co_x`` / ``co_y``: traverse location, Lambert 72.
    """
    if orientation is None or orientation < 0:
        return Direction(None, "no orientation: set manually")
    if not segment_wkb:
        return Direction(None, "no segment match")
    if co_x is None or co_y is None:
        return Direction(None, "no traverse location")
    geom = _segment_geometry(segment_wkb)
    if geom is None:
        return Direction(None, "unreadable segment geometry")

    from shapely.geometry import Point

    point = Point(*_to_lambert2008(_LB72).transform(co_x, co_y))
    seg = local_bearing(geom, point)
    angle = angle_between(orientation % 360, seg)
    if angle <= SAME_MAX_DEG:
        return Direction("inDirection", "orientation follows the segment", seg, angle)
    if angle >= OPPOSITE_MIN_DEG:
        return Direction("inOppositeDirection", "orientation against the segment", seg, angle)
    return Direction(None, "orientation nearly perpendicular to the segment: check manually",
                     seg, angle)


def combine(directions):
    """Directions computed for each segment match of one traverse -> one Direction.

    Matches that agree give that value; disagreeing matches give None.
    """
    if not directions:
        return Direction(None, "no segment match")
    values = {d.value for d in directions}
    if len(values) == 1:
        return directions[0]
    return Direction(None, "segment matches disagree: check manually")
