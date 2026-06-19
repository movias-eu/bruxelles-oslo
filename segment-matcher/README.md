# Segment Matcher

A human-in-the-loop service that matches measure locations (traffic counters, bike counters, etc.) to directed road segments from a WFS source. When multiple candidate segments exist at different vertical levels (tunnel, surface, overpass), the service presents them on a map for an operator to pick the right one.

## How it works

1. An external system sends a `POST /match` with a device name, coordinates, and a callback URL.
2. The service queries a WFS endpoint for road segments within a configurable radius.
3. Candidates are scored. The configured scorer (`openlr` by default) is used only when the request provides everything it needs — a real bearing (`orientation`) **and** `road_type`; otherwise the service falls back to the naive distance/bearing scorer for that request (see [Scoring strategies](#scoring-strategies)).
4. The top candidates are classified: a clear winner resolves automatically; a close call is held for human review (see [Disambiguation](#wfs-lvl--vertical-level)).
5. If a clear winner exists, the result is POSTed to the callback URL immediately.
6. Otherwise the request is held for human review at `/review`; the operator picks a segment on the map, and the result is POSTed to the callback URL.

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
| `SCORER` | `openlr` | Scoring strategy: `openlr` or `naive`. With `openlr`, a request missing `orientation` or `road_type` falls back to `naive` automatically |
| `TIE_MODE` | `always` | When the tie check applies: `always` (top two candidates, any level) or `cross-level` (only a surface vs a non-surface candidate) |
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
{"name": "LEO201", "segment_id": 1252, "wkb": "0105000000...", "offset": 99.0}
```

- `name`, `segment_id` — the device and the chosen segment
- `wkb` — the chosen segment's geometry (hex WKB)
- `offset` — distance in **metres**, along the chosen segment, from its start to
  the point nearest the input. Computed on the EPSG:3812 (metric) geometry via
  `project()`. "Start" is the segment's coordinate-order start (same caveat as the
  direction markers — not necessarily the real travel-direction start).

### `GET /review`

Disambiguation UI. Shows the pending match on a Leaflet map with candidate segments color-coded by level. Each segment carries a direction marker — a dot at the start and a single arrowhead at the end (`leaflet-polylinedecorator`, loaded from unpkg alongside Leaflet). The marker direction follows the segment's coordinate order (how it was digitized), which is not necessarily the real travel direction — see the note below. Candidates whose best score assumed a reversed bearing are flagged with a ⇄ reverse badge in the table. Each candidate has a "Select" button (plain HTML form). If nothing is pending, shows a "No pending reviews" page.

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

How this field is used depends on `TIE_MODE`:

- **`always`** (default): the top two candidates are compared regardless of level. If their score gap is below `TIE_THRESHOLD`, it's a tie and the operator must choose. This also catches two same-level roads that score almost identically.
- **`cross-level`**: only a surface (`0`) vs a non-surface (tunnel `-1` / overpass `1`) candidate triggers review, and only when their gap is below `TIE_THRESHOLD`. Same-level near-ties resolve automatically to the higher score.

## Scoring strategies

The scorer is chosen **per request**. `SCORER` sets the preferred strategy, but `openlr` is only used when the request supplies the inputs it depends on:

> **OpenLR needs a real bearing and road class.** When `orientation` *or* `road_type` is missing, OpenLR would fabricate defaults — bearing `0` (due North) and `MULTIPLE_CARRIAGEWAY` — and a wrong default bearing can rank a worse-positioned segment above the correct nearer one. So with `SCORER=openlr`, a request lacking either field **falls back to the naive scorer** for that request (logged at INFO). `SCORER=naive` always uses naive.

### OpenLR (`SCORER=openlr`, both `orientation` and `road_type` present)

Uses `openlr-dereferencer`'s `score_lrp_candidate` function. For each candidate segment, constructs a synthetic Location Reference Point from the request coordinates and scores it against the candidate. The composite score factors in:

- **Geo distance**: exponential decay from distance to the measure point
- **FRC match**: penalty for Functional Road Class mismatch between wanted (FRC1 default) and actual
- **FOW match**: penalty for Form of Way mismatch between wanted (from `road_type`) and actual
- **Bearing**: angular difference between request `orientation` and segment direction

### Naive (`SCORER=naive`, or the OpenLR fallback above)

Distance + bearing scorer with no external dependencies:

```
score = 0.5 * (1 - dist/radius) + 0.5 * (1 - bearing_diff/180)
```

If no `orientation` is provided, scores by distance only — `score = 1 - dist/radius`.

### Bidirectional bearing scoring

The bearing in the source system may have been entered the wrong way round. So
whenever an `orientation` **is** provided, every candidate is scored twice — at
the request bearing **and** at its 180° reverse — and the **better** of the two
becomes the candidate's score. This rescues the correct segment when the bearing
was reversed upstream, at the cost of making bearing less discriminating between
the two directions of a road (those near-ties surface for review under
`TIE_MODE=always`).

Each candidate then carries:

- `score_forward` / `score_reversed` — the two scores
- `bearing_reversed` — `true` if the reverse won (the segment's direction opposes
  the request orientation)

Both scorers (OpenLR and naive) do this. When no `orientation` is provided, there
is no bearing comparison and these fields are unset.

The disambiguation UI flags reverse-winners with a **⇄ reverse** badge in the
candidate table (tooltip shows both the as-entered and reversed scores). The
reverse state is intentionally **not** drawn on the map — colouring it would
clash with the orange selection highlight — so the table badge is the single
indicator.

> **Known limitation — direction comes from coordinate order, not the data's
> direction fields.** Both the bearing comparison (`_segment_bearing_wgs84`,
> start→end of the line) and the map's start-dot/end-arrow markers use the
> segment's *digitization* order, which does not necessarily match real travel
> direction. The WFS layer actually carries explicit direction attributes
> (`bm_direction`, `one_way`, `auto_direction`, `from_node`/`to_node`) that we do
> not yet use. A follow-up should orient both the scorer and the markers from
> those fields (and handle bidirectional segments) instead of coordinate order.

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
