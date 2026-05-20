"""Lightweight adapters to satisfy openlr-dereferencer's Line/Node interfaces using WFS data."""

from openlr import Coordinates, FRC, FOW
from openlr_dereferencer.maps import Line as BaseLine, Node as BaseNode
from shapely.geometry import LineString, Point

from coords import haversine
from models import Candidate

FRC_MAP = {f"FRC{i}": getattr(FRC, f"FRC{i}") for i in range(8)}

FOW_MAP = {
    name: getattr(FOW, name)
    for name in [
        "MOTORWAY", "MULTIPLE_CARRIAGEWAY", "SINGLE_CARRIAGEWAY", "ROUNDABOUT",
        "TRAFFICSQUARE", "SLIPROAD", "OTHER", "UNDEFINED",
    ]
}

MORPHOLOGY_TO_FOW = {
    101: "MOTORWAY",
    102: "MULTIPLE_CARRIAGEWAY",
    103: "SINGLE_CARRIAGEWAY",
    104: "ROUNDABOUT",
    107: "SLIPROAD",
    110: "SLIPROAD",
    111: "SINGLE_CARRIAGEWAY",
    112: "TRAFFICSQUARE",
    113: "SINGLE_CARRIAGEWAY",
    114: "OTHER",
    116: "OTHER",
    120: "OTHER",
    125: "OTHER",
    130: "UNDEFINED",
}

TYPOLOGY_TO_FRC = {
    "A0": "FRC0", "A0b": "FRC0",
    "A1": "FRC1", "A2": "FRC2", "A3": "FRC3",
    "A4": "FRC4", "A5": "FRC5",
    "B1": "FRC6", "B2": "FRC7", "B3": "FRC7",
    "C": "FRC6", "D": "FRC7",
}

class WfsNode(BaseNode):
    def __init__(self, nid: int, lon: float, lat: float):
        self._id, self._lon, self._lat = nid, lon, lat

    @property
    def node_id(self):
        return self._id

    @property
    def coordinates(self):
        return Coordinates(self._lon, self._lat)

    def outgoing_lines(self):
        return iter([])

    def incoming_lines(self):
        return iter([])

    def connected_lines(self):
        return iter([])


class WfsLine(BaseLine):
    def __init__(self, candidate: Candidate):
        self._c = candidate
        flat = [c for ring in candidate.geom_wgs84 for c in ring]
        self._start = flat[0] if flat else [0, 0]
        self._end = flat[-1] if flat else [0, 0]
        self._geometry = LineString(flat)

    @property
    def line_id(self):
        return self._c.gid

    @property
    def start_node(self):
        return WfsNode(0, self._start[0], self._start[1])

    @property
    def end_node(self):
        return WfsNode(1, self._end[0], self._end[1])

    @property
    def frc(self):
        return FRC_MAP.get(TYPOLOGY_TO_FRC.get(self._c.typology, "FRC7"), FRC.FRC7)

    @property
    def fow(self):
        morph = int(self._c.morphology) if self._c.morphology.isdigit() else 130
        return FOW_MAP.get(MORPHOLOGY_TO_FOW.get(morph, "UNDEFINED"), FOW.UNDEFINED)

    @property
    def geometry(self):
        return self._geometry

    @property
    def length(self):
        return self._c.length

    def distance_to(self, coord: Coordinates):
        pt = Point(coord.lon, coord.lat)
        nearest = self._geometry.interpolate(self._geometry.project(pt))
        return int(round(haversine(coord.lon, coord.lat, nearest.x, nearest.y)))
