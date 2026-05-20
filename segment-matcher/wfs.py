import logging
import os

import httpx
from shapely.geometry import shape

from coords import transform_coords
from models import Candidate

logger = logging.getLogger(__name__)

DEFAULT_WFS_URL = os.environ.get("WFS_URL", "https://data.mobility.brussels/geoserver/bm_network/wfs")
WFS_LAYER = os.environ.get("WFS_LAYER", "bm_network:brr_segments_direction")


async def fetch_candidates(
    client: httpx.AsyncClient,
    x_3812: float,
    y_3812: float,
    radius: float,
    wfs_url: str = DEFAULT_WFS_URL,
) -> list[Candidate]:
    bbox = f"{x_3812 - radius},{y_3812 - radius},{x_3812 + radius},{y_3812 + radius},EPSG:3812"
    params = {
        "service": "wfs",
        "version": "1.1.0",
        "request": "GetFeature",
        "typeName": WFS_LAYER,
        "outputFormat": "json",
        "srsName": "EPSG:3812",
        "bbox": bbox,
    }
    try:
        resp = await client.get(wfs_url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.exception("WFS request failed")
        return []

    return [c for f in data.get("features", []) if (c := _parse_feature(f)) is not None]


def _parse_feature(feature: dict) -> Candidate | None:
    props = feature.get("properties", {})
    geom_raw = feature.get("geometry")
    if not geom_raw:
        return None

    coords_3812 = geom_raw.get("coordinates", [])
    coords_wgs84 = [transform_coords(ring, "EPSG:3812", "EPSG:4326") for ring in coords_3812]

    return Candidate(
        gid=props.get("gid", 0),
        lvl=str(props.get("lvl", "0")),
        score=0.0,
        dist=0.0,
        morphology=str(props.get("morphology", "")),
        typology=str(props.get("typology", "")),
        length=props.get("length", 0.0),
        geom_wgs84=coords_wgs84,
        geom_3812=coords_3812,
        wkb_hex=shape(geom_raw).wkb_hex,
    )
