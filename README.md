# bruxelles-oslo

Integrating Brussels Mobility traffic-measurement data with standards-based representations.

The repository makes two distinct contributions:

- **Semantic mapping** — translating Brussels Mobility's source values (sensor systems, vehicle types, measurements, directions) to the [OSLO Traffic Measurements](https://data.vlaanderen.be/doc/applicatieprofiel/verkeersmetingen/) and [INSPIRE](https://inspire.ec.europa.eu/) codelists.
- **Locational mapping** — [`segment-matcher`](segment-matcher/), a service that resolves measurement locations (counters, cameras, sensors) to specific directed road segments on the Brussels road network.

Both pieces are needed to publish Brussels Mobility counts as interoperable Linked Data: one fixes *what* each value means, the other fixes *where* it was measured.

## Repository layout

| Path | What it is |
|---|---|
| [`oslo-mapping/`](oslo-mapping/) | RML pipeline (preprocessors + YARRRML mappings) turning the source feeds into OSLO Verkeersmetingen RDF, plus the source-to-target codelist analysis. See its [README](oslo-mapping/README.md). |
| [`segment-matcher/`](segment-matcher/) | FastAPI service: WFS candidate lookup, OpenLR-based scoring, optional human-in-the-loop review. See its [README](segment-matcher/README.md). |
| [`data-samples/`](data-samples/) | Excerpts of real Brussels Mobility data used during the analysis (see below). |

## Data samples

Small, representative slices of source data — enough to illustrate the shapes the mapping deals with, not a full dataset.

| File | Description |
|---|---|
| `Straatassen.json` | GeoJSON of street axes (the road network used by the matcher). |
| `bike_devices.json` | GeoJSON inventory of bike-counting devices. |
| `bike_summary_20260215.json` | One day of aggregated bike counts. |
| `tlc_20260313_1300.json` | One hour of TLC system vehicle counts. |
| `dai_202602201350.xml` | CitiEvent XML excerpt from the DAI camera system. |
| `schema.json` | Tabular-schema description of count records. |

## Quick start — segment matcher

```bash
cd segment-matcher
uv sync --extra openlr
python app.py
```

Then `POST` to `/match`:

```bash
curl -X POST http://localhost:8000/match \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "SCHUMAN_TEST",
    "x": 4.3815, "y": 50.8431,
    "orientation": 45,
    "object_type": "traffic",
    "road_type": 102,
    "post_url": "http://localhost:9999/cb"
  }'
```

Full configuration, API reference, and review-workflow docs are in [`segment-matcher/README.md`](segment-matcher/README.md).

## License

TBD.
