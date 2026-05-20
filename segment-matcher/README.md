# Segment Matcher

A human-in-the-loop service that matches measure locations (traffic counters, bike counters, etc.) to directed road segments from a WFS source. When multiple candidate segments exist at different vertical levels (tunnel, surface, overpass), the service presents them on a map for an operator to pick the right one.

## How it works

1. An external system sends a `POST /match` with a device name, coordinates, and a callback URL.
2. The service queries a WFS endpoint for road segments within a configurable radius.
3. Candidates are scored by spatial proximity and bearing match (using OpenLR scoring or a naive fallback).
4. If there's a clear winner, the result is POSTed to the callback URL immediately.
5. If multiple vertical levels compete, the request is held for human review at `/review`.
6. The operator picks a segment on the map. The result is POSTed to the callback URL.

All requests return `202 Accepted` immediately. Only one pending review is allowed at a time — a second `POST /match` returns `409` until the current review is resolved.

## Setup

```bash
cd segment-matcher
uv sync --extra openlr
```

Or without uv:

```bash
cd segment-matcher
python -m venv .venv
source .venv/bin/activate
pip install ".[openlr]"
```

The `openlr` extra installs the OpenLR scoring packages. Omit it if using `SCORER=naive`.

## Running

```bash
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

Or directly:

```bash
python app.py
```

## Configuration

All via environment variables. See `.env.example` for defaults.

| Variable | Default | Description |
|---|---|---|
| `WFS_URL` | `https://data.mobility.brussels/geoserver/bm_network/wfs` | WFS endpoint (also changeable at runtime via `/settings`) |
| `WFS_LAYER` | `bm_network:brr_segments_direction` | WFS layer name |
| `MATCH_RADIUS` | `50` | Search radius in meters |
| `TIE_THRESHOLD` | `0.05` | Score gap below which human review is needed |
| `SCORER` | `openlr` | Scoring strategy: `openlr` or `naive` |
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8000` | Bind port |

## API

### `POST /match`

Submit a measure location for matching.

```json
{
  "name": "LEO201",
  "x": 600000,
  "y": 700000,
  "orientation": 45,
  "object_type": "traffic",
  "road_type": 102,
  "post_url": "https://example.com/callback"
}
```

- `name` (required): device identifier
- `x`, `y` (required): coordinates — auto-detected as Lambert 72 (EPSG:31370), Lambert 2008 (EPSG:3812), or WGS84 (EPSG:4326)
- `orientation`: bearing in degrees, improves scoring
- `object_type`: device metadata, not used in scoring
- `road_type`: morphology code, used to set the "wanted" Form of Way in OpenLR scoring (see property mapping below)
- `post_url` (required): where to POST the result

**Responses:**

- `202` with `{"status": "resolved", "segment_id": 1252}` — matched automatically
- `202` with `{"status": "pending_review", "review_url": "/review"}` — needs human review
- `202` with `{"status": "no_candidates"}` — nothing found in radius
- `409` — another review is already pending

### Callback payload

POSTed to `post_url` after resolution:

```json
{"name": "LEO201", "segment_id": 1252, "wkb": "0105000000..."}
```

### `GET /review`

Disambiguation UI. Shows the pending match on a Leaflet map with candidate segments color-coded by level. Each candidate has a "Select" button (plain HTML form). If nothing is pending, shows a "No pending reviews" page.

### `GET /settings`

View and change the WFS URL at runtime.

## Property mapping

The WFS layer `brr_segments_direction` exposes properties that map to OpenLR concepts used in scoring. The request's `road_type` parameter also maps into this scheme.

### WFS `morphology` / request `road_type` to OpenLR Form of Way (FOW)

Both the WFS `morphology` field and the request's `road_type` parameter use the same code system:

| Code | Description | OpenLR FOW |
|---|---|---|
| 101 | Motorway | MOTORWAY |
| 102 | Multiple carriageway | MULTIPLE_CARRIAGEWAY |
| 103 | Single carriageway | SINGLE_CARRIAGEWAY |
| 104 | Roundabout | ROUNDABOUT |
| 107 | Slip road | SLIPROAD |
| 110 | Slip road | SLIPROAD |
| 111 | Single carriageway | SINGLE_CARRIAGEWAY |
| 112 | Traffic square | TRAFFICSQUARE |
| 113 | Single carriageway | SINGLE_CARRIAGEWAY |
| 114 | Other | OTHER |
| 116 | Other | OTHER |
| 120 | Other | OTHER |
| 125 | Other | OTHER |
| 130 | Undefined | UNDEFINED |

When `road_type` is provided in the request, it sets the "wanted" FOW for OpenLR scoring — candidates matching this FOW score higher. When absent, defaults to MULTIPLE_CARRIAGEWAY.

### WFS `typology` to OpenLR Functional Road Class (FRC)

| Typology | OpenLR FRC |
|---|---|
| A0, A0b | FRC0 (main road) |
| A1 | FRC1 |
| A2 | FRC2 |
| A3 | FRC3 |
| A4 | FRC4 |
| A5 | FRC5 |
| B1, C | FRC6 |
| B2, B3, D | FRC7 (minor road) |

### WFS `lvl` — vertical level

| Value | Label | Description |
|---|---|---|
| -1 | tunnel | Below surface |
| 0 | surface | Ground level |
| 1 | overpass | Above surface |

This is the field that drives disambiguation. When candidates span multiple levels and scores are close (gap < `TIE_THRESHOLD`), the operator must choose.

## Scoring strategies

### OpenLR (`SCORER=openlr`)

Uses `openlr-dereferencer`'s `score_lrp_candidate` function. For each candidate segment, constructs a synthetic Location Reference Point from the request coordinates and scores it against the candidate. The composite score factors in:

- **Geo distance**: exponential decay from distance to the measure point
- **FRC match**: penalty for Functional Road Class mismatch between wanted (FRC1 default) and actual
- **FOW match**: penalty for Form of Way mismatch between wanted (from `road_type` or MULTIPLE_CARRIAGEWAY default) and actual
- **Bearing**: angular difference between request `orientation` and segment direction

### Naive (`SCORER=naive`)

Distance + bearing scorer with no external dependencies:

```
score = 0.5 * (1 - dist/radius) + 0.5 * (1 - bearing_diff/180)
```

If no `orientation` is provided, scores by distance only.

## Coordinate auto-detection

The service auto-detects the coordinate reference system from value ranges:

| CRS | EPSG | x range | y range |
|---|---|---|---|
| Lambert 72 | 31370 | 20,000 - 300,000 | 20,000 - 300,000 |
| Lambert 2008 | 3812 | 480,000 - 800,000 | 560,000 - 800,000 |
| WGS84 | 4326 | 2 - 7 | 49 - 52 |

Coordinates are internally normalized to EPSG:3812 for WFS queries (the layer's native CRS).

## File structure

```
app.py              FastAPI routes and request lifecycle
models.py           Pydantic models (MatchRequest, Candidate, MatchResult, PendingMatch)
coords.py           CRS auto-detection (LB72 / LB2008 / WGS84) and pyproj transforms
wfs.py              Async WFS client with bbox spatial filter
scorer.py           OpenLR and naive scoring strategies, tie classification
openlr_adapter.py   Line/Node adapters for openlr-dereferencer
templates/          Jinja2 HTML templates (map UI, confirmation, settings)
```
