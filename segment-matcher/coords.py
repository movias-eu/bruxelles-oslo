from functools import lru_cache
from math import atan2, cos, radians, sin, sqrt

from pyproj import Transformer

EARTH_RADIUS = 6_378_137.0


def haversine(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return EARTH_RADIUS * 2 * atan2(sqrt(a), sqrt(1 - a))


@lru_cache
def _get_transformer(src: str, dst: str) -> Transformer:
    return Transformer.from_crs(src, dst, always_xy=True)


def detect_crs(x: float, y: float) -> str:
    if x < 180 and y < 180:
        return "EPSG:4326"
    if 480_000 <= x <= 800_000:
        return "EPSG:3812"
    if 20_000 <= x <= 300_000:
        return "EPSG:31370"
    raise ValueError(f"Cannot detect CRS for x={x}, y={y}")


def to_epsg3812(x: float, y: float) -> tuple[float, float]:
    crs = detect_crs(x, y)
    if crs == "EPSG:3812":
        return x, y
    return _get_transformer(crs, "EPSG:3812").transform(x, y)


def to_wgs84(x: float, y: float, source_crs: str = "EPSG:3812") -> tuple[float, float]:
    if source_crs == "EPSG:4326":
        return x, y
    return _get_transformer(source_crs, "EPSG:4326").transform(x, y)


def transform_coords(coords: list[list[float]], src: str, dst: str) -> list[list[float]]:
    t = _get_transformer(src, dst)
    return [[*t.transform(c[0], c[1])] for c in coords]
