import logging
import os
from math import atan2, cos, degrees, radians, sin
from typing import Literal

from shapely.geometry import MultiLineString, Point

from coords import to_wgs84
from models import Candidate

logger = logging.getLogger(__name__)

MATCH_RADIUS = float(os.environ.get("MATCH_RADIUS", "50"))
TIE_THRESHOLD = float(os.environ.get("TIE_THRESHOLD", "0.05"))

# Which scorer to use. "openlr" needs a real bearing + road_type to score well;
# when either is missing it fabricates defaults (bearing 0 / MULTIPLE_CARRIAGEWAY)
# that can bias the ranking, so we fall back to the naive scorer per-request.
SCORER = os.environ.get("SCORER", "openlr")

# When to apply the score-margin tie check (configurable, like SCORER):
#   "always"      -> compare the top two candidates regardless of level (default)
#   "cross-level" -> only when a surface and a non-surface candidate compete
TIE_MODE = os.environ.get("TIE_MODE", "always")

LVL_LABELS = {"-1": "tunnel", "0": "surface", "1": "overpass"}


def _bearing(lon1, lat1, lon2, lat2):
    p1, p2 = radians(lat1), radians(lat2)
    dl = radians(lon2 - lon1)
    y = sin(dl) * cos(p2)
    x = cos(p1) * sin(p2) - sin(p1) * cos(p2) * cos(dl)
    return degrees(atan2(y, x)) % 360


def _bearing_diff(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _distance_to_segment(px, py, coords_3812):
    return MultiLineString(coords_3812).distance(Point(px, py))


def offset_along_segment(px, py, coords_3812) -> float:
    """Distance in metres, along the segment, from its start to the point on the
    segment nearest the input LRP (px, py).

    Uses the EPSG:3812 (metric) geometry, so shapely's project() returns metres
    directly. "Start" is the segment's COORDINATE-ORDER start (how it was
    digitized) -- the same caveat as the direction markers: it is not guaranteed
    to be the real travel-direction start.
    """
    return round(MultiLineString(coords_3812).project(Point(px, py)), 1)


def _segment_bearing_wgs84(coords_wgs84):
    flat = [c for ring in coords_wgs84 for c in ring]
    if len(flat) < 2:
        return 0.0
    return _bearing(flat[0][0], flat[0][1], flat[-1][0], flat[-1][1])


def _with_score_and_dist(candidate: Candidate, score: float, dist: float) -> Candidate:
    return candidate.model_copy(update={"score": round(score, 4), "dist": round(dist, 1)})


def _with_bidirectional(candidate: Candidate, fwd: float, rev: float, dist: float) -> Candidate:
    """Record forward + reversed scores; `score` is the better, with a reversed flag.

    Used when an orientation was provided and the candidate was scored both at the
    request bearing and its 180-degree reverse (the source system may have entered
    the bearing backwards).
    """
    reversed_won = rev > fwd
    return candidate.model_copy(update={
        "score": round(max(fwd, rev), 4),
        "score_forward": round(fwd, 4),
        "score_reversed": round(rev, 4),
        "bearing_reversed": reversed_won,
        "dist": round(dist, 1),
    })


# --- OpenLR scoring ---

if os.environ.get("SCORER", "openlr") == "openlr":
    from openlr import FOW, FRC, LocationReferencePoint
    from openlr_dereferencer import Config
    from openlr_dereferencer.decoding.routes import PointOnLine
    from openlr_dereferencer.decoding.scoring import score_lrp_candidate

    from openlr_adapter import FOW_MAP, MORPHOLOGY_TO_FOW, WfsLine


def score_openlr(
    candidates: list[Candidate], x_3812: float, y_3812: float,
    orientation: float | None, road_type: int | None,
) -> list[Candidate]:
    lon, lat = to_wgs84(x_3812, y_3812)
    wanted_fow = FOW_MAP.get(MORPHOLOGY_TO_FOW.get(road_type, "MULTIPLE_CARRIAGEWAY"), FOW.MULTIPLE_CARRIAGEWAY)
    config = Config(search_radius=MATCH_RADIUS)

    def lrp_score(c, bearing):
        line = WfsLine(c)
        proj = line.geometry.project(Point(lon, lat), normalized=True)
        proj = max(0.001, min(0.999, proj))
        lrp = LocationReferencePoint(lon, lat, FRC.FRC1, wanted_fow, bearing, FRC.FRC1, 100)
        try:
            return score_lrp_candidate(lrp, PointOnLine(line, proj), config, False)
        except Exception:
            logger.warning("OpenLR scoring failed for gid=%s", c.gid, exc_info=True)
            return 0.0

    scored = []
    for c in candidates:
        dist = _distance_to_segment(x_3812, y_3812, c.geom_3812)
        if orientation is None:
            scored.append(_with_score_and_dist(c, lrp_score(c, 0), dist))
        else:
            bear = int(orientation)
            fwd = lrp_score(c, bear)
            rev = lrp_score(c, (bear + 180) % 360)
            scored.append(_with_bidirectional(c, fwd, rev, dist))

    return sorted(scored, key=lambda c: -c.score)


def score_naive(
    candidates: list[Candidate], x_3812: float, y_3812: float,
    orientation: float | None, road_type: int | None,
) -> list[Candidate]:
    scored = []
    for c in candidates:
        dist = _distance_to_segment(x_3812, y_3812, c.geom_3812)
        geo = max(0.0, 1.0 - dist / MATCH_RADIUS)

        if orientation is None:
            scored.append(_with_score_and_dist(c, geo, dist))
            continue

        seg_bearing = _segment_bearing_wgs84(c.geom_wgs84)

        def blended(target):
            bd = _bearing_diff(seg_bearing, target)
            return 0.5 * geo + 0.5 * max(0.0, 1.0 - bd / 180)

        fwd = blended(orientation)
        rev = blended((orientation + 180) % 360)
        scored.append(_with_bidirectional(c, fwd, rev, dist))

    return sorted(scored, key=lambda c: -c.score)


def score(
    candidates: list[Candidate], x_3812: float, y_3812: float,
    orientation: float | None, road_type: int | None,
) -> list[Candidate]:
    """Score candidates, choosing the scorer per request.

    The configured SCORER ("openlr" by default) is used only when it has the
    inputs it relies on -- a real bearing (orientation) AND road_type (FRC/FOW).
    If either is missing, OpenLR would fabricate misleading defaults (bearing 0 =
    North, MULTIPLE_CARRIAGEWAY) that can rank a worse-positioned segment above
    the correct nearer one, so we fall back to the naive scorer (distance, plus
    bearing only when present).
    """
    if SCORER == "openlr" and orientation is not None and road_type is not None:
        return score_openlr(candidates, x_3812, y_3812, orientation, road_type)
    if SCORER == "openlr":
        logger.info(
            "Falling back to naive scorer (orientation=%s, road_type=%s)",
            orientation, road_type,
        )
    return score_naive(candidates, x_3812, y_3812, orientation, road_type)


def classify(candidates: list[Candidate]) -> Literal["none", "auto", "tie"]:
    if not candidates:
        return "none"
    if len(candidates) == 1:
        return "auto"

    if TIE_MODE == "always":
        # Compare the top two candidates regardless of level.
        top, second = candidates[0], candidates[1]
        return "tie" if top.score - second.score < TIE_THRESHOLD else "auto"

    # "cross-level": only disambiguate a surface vs a non-surface (tunnel/overpass).
    lvls = {c.lvl for c in candidates}
    if len(lvls) <= 1:
        return "auto"

    best_surface = max((c for c in candidates if c.lvl == "0"), key=lambda c: c.score, default=None)
    best_non_surface = max((c for c in candidates if c.lvl != "0"), key=lambda c: c.score, default=None)
    if not best_surface or not best_non_surface:
        return "auto"

    if best_surface.score - best_non_surface.score >= TIE_THRESHOLD:
        return "auto"

    return "tie"
