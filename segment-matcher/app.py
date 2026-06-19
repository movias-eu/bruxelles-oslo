import json
import logging
import os
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

from coords import to_epsg3812, to_wgs84
from models import MatchRequest, MatchResult, PendingMatch
from scorer import MATCH_RADIUS, LVL_LABELS, classify, offset_along_segment, score
from wfs import DEFAULT_WFS_URL, fetch_candidates

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient()
    app.state.wfs_url = DEFAULT_WFS_URL
    app.state.pending = None
    yield
    await app.state.http_client.aclose()


app = FastAPI(lifespan=lifespan)
jinja_env = Environment(loader=FileSystemLoader("templates"), autoescape=True)
jinja_env.filters["tojson"] = lambda v: Markup(json.dumps(v))


@app.post("/match", status_code=202)
async def match(req: MatchRequest):
    if app.state.pending is not None:
        raise HTTPException(409, "A match is already pending review. Resolve it before submitting another.")

    try:
        x_3812, y_3812 = to_epsg3812(req.x, req.y)
    except ValueError as e:
        raise HTTPException(400, str(e))
    client = app.state.http_client

    candidates = await fetch_candidates(client, x_3812, y_3812, MATCH_RADIUS, app.state.wfs_url)

    candidates = score(candidates, x_3812, y_3812, req.orientation, req.road_type)

    status = classify(candidates)
    match_id = str(uuid.uuid4())

    if status == "none":
        logger.warning("No candidates found for %s at (%s, %s)", req.name, req.x, req.y)
        return {"id": match_id, "status": "no_candidates"}

    if status == "auto":
        best = candidates[0]
        offset = offset_along_segment(x_3812, y_3812, best.geom_3812)
        result = MatchResult(name=req.name, segment_id=best.gid, wkb=best.wkb_hex, offset=offset)
        await _post_result(client, str(req.post_url), result)
        return {"id": match_id, "status": "resolved", "segment_id": best.gid}

    lon, lat = to_wgs84(x_3812, y_3812)
    app.state.pending = PendingMatch(
        id=match_id,
        request=req,
        candidates=candidates,
        point_wgs84=(lon, lat),
        point_3812=(x_3812, y_3812),
    )
    logger.info("Pending review for %s: %d candidates", req.name, len(candidates))
    return {"id": match_id, "status": "pending_review", "review_url": "/review"}


@app.get("/review", response_class=HTMLResponse)
async def review():
    if app.state.pending is None:
        return HTMLResponse(jinja_env.get_template("no_pending.html").render())
    html = jinja_env.get_template("disambiguate.html").render(
        match=app.state.pending.model_dump(), lvl_labels=LVL_LABELS,
    )
    return HTMLResponse(html)


@app.post("/review/select", response_class=HTMLResponse)
async def select(gid: int = Form()):
    pm = app.state.pending
    if pm is None:
        raise HTTPException(404, "No pending match to resolve")

    chosen = next((c for c in pm.candidates if c.gid == gid), None)
    if not chosen:
        raise HTTPException(400, f"Segment {gid} is not a candidate")

    px, py = pm.point_3812
    offset = offset_along_segment(px, py, chosen.geom_3812)
    result = MatchResult(name=pm.request.name, segment_id=chosen.gid, wkb=chosen.wkb_hex, offset=offset)
    await _post_result(app.state.http_client, str(pm.request.post_url), result)

    name, lvl = pm.request.name, LVL_LABELS.get(chosen.lvl, chosen.lvl)
    app.state.pending = None

    html = jinja_env.get_template("confirmed.html").render(name=name, gid=chosen.gid, lvl=lvl)
    return HTMLResponse(html)


@app.get("/settings", response_class=HTMLResponse)
async def settings_page():
    return HTMLResponse(jinja_env.get_template("settings.html").render(wfs_url=app.state.wfs_url))


@app.post("/settings", response_class=HTMLResponse)
async def settings_update(wfs_url: str = Form()):
    app.state.wfs_url = wfs_url.strip()
    logger.info("WFS URL updated to: %s", app.state.wfs_url)
    html = jinja_env.get_template("settings.html").render(wfs_url=app.state.wfs_url, saved=True)
    return HTMLResponse(html)


@app.get("/health")
async def health():
    return {"status": "ok"}


async def _post_result(client: httpx.AsyncClient, url: str, result: MatchResult):
    try:
        resp = await client.post(url, json=result.model_dump(), timeout=10)
        logger.info("Posted result to %s: %s (status %d)", url, result.model_dump(), resp.status_code)
    except Exception:
        logger.exception("Failed to post result to %s", url)


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app:app", host=host, port=port, reload=True)
