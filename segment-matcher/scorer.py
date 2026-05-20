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


def _segment_bearing_wgs84(coords_wgs84):
    flat = [c for ring in coords_wgs84 for c in ring]
    if len(flat) < 2:
        return 0.0
    return _bearing(flat[0][0], flat[0][1], flat[-1][0], flat[-1][1])


def _with_score_and_dist(candidate: Candidate, score: float, dist: float) -> Candidate:
    return candidate.model_copy(update={"score": round(score, 4), "dist": round(dist, 1)})


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
    bear = int(orientation) if orientation is not None else 0
    wanted_fow = FOW_MAP.get(MORPHOLOGY_TO_FOW.get(road_type, "MULTIPLE_CARRIAGEWAY"), FOW.MULTIPLE_CARRIAGEWAY)
    synthetic_lrp = LocationReferencePoint(lon, lat, FRC.FRC1, wanted_fow, bear, FRC.FRC1, 100)
    config = Config(search_radius=MATCH_RADIUS)

    scored = []
    for c in candidates:
        line = WfsLine(c)
        proj = line.geometry.project(Point(lon, lat), normalized=True)
        proj = max(0.001, min(0.999, proj))
        try:
            score = score_lrp_candidate(synthetic_lrp, PointOnLine(line, proj), config, False)
        except Exception:
            logger.warning("OpenLR scoring failed for gid=%s", c.gid, exc_info=True)
            score = 0.0
        dist = _distance_to_segment(x_3812, y_3812, c.geom_3812)
        scored.append(_with_score_and_dist(c, score, dist))

    return sorted(scored, key=lambda c: -c.score)


def score_naive(
    candidates: list[Candidate], x_3812: float, y_3812: float,
    orientation: float | None, road_type: int | None,
) -> list[Candidate]:
    scored = []
    for c in candidates:
        dist = _distance_to_segment(x_3812, y_3812, c.geom_3812)
        geo = max(0.0, 1.0 - dist / MATCH_RADIUS)

        if orientation is not None:
            bd = _bearing_diff(_segment_bearing_wgs84(c.geom_wgs84), orientation)
            score = 0.5 * geo + 0.5 * max(0.0, 1.0 - bd / 180)
        else:
            score = geo

        scored.append(_with_score_and_dist(c, score, dist))

    return sorted(scored, key=lambda c: -c.score)


def classify(candidates: list[Candidate]) -> Literal["none", "auto", "tie"]:
    if not candidates:
        return "none"

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
