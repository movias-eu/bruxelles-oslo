# live-data → OSLO RDF

Transforms the **live feed** samples in `../data-samples/` into OSLO
Verkeersmetingen RDF via RMLMapper. Ported from the Morph-KGC/YARRRML mappings in
the (closed) PR #1, now using RMLMapper (a JVM tool) + the same preprocess → RML
pipeline as `django/` and `historical-data/`.

## Feeds

| `--feed` | Sample file | Maps to | Identity | Sensor |
|---|---|---|---|---|
| `tlc` | `tlc_*.json` | measurements | traverse (detectors summed) | `standaard_Camera` |
| `dai` | `dai_*.xml` | measurements | traverse (camera lanes summed) | `standaard_Camera` |
| `bike_live` | `bike_live.json` | measurements | device | `glasvezel` |
| `bike_devices` | `bike_devices_extended.json` | measure location | name | — |

**Measurement feeds** (tlc / dai / bike_live) emit per-characteristic
observations:
- **volume → aantal** as `impl:Verkeerstelling` (`tellingresultaat`)
- **speed → tijdsgemiddelde_snelheid** as `impl:Verkeerssnelheidsmeting`
  (`OM_Observation.result` → `iso19103-mp:Speed`, km/h)

The temporal entity carries explicit **begin + end instants** (`time:hasBeginning`
/ `time:hasEnd`, no duration). The feeds give local Brussels time without an
offset; every instant is published with the offset in effect (`+01:00` /
`+02:00`, typed `xsd:dateTimeStamp`), and measurement IRIs use the UTC time so
they stay unique across the October clock change:
- **tlc** — `from_timestamp` / `to_timestamp` (both in the data).
- **dai** — `Time` (start; assumed, minute-aligned & matches the filename) and
  `Time + PeriodSec` (end).
- **bike_live** — three calendar-aligned cumulative counts per device; begin =
  start of the hour / day / year, end = the device `timestamp`.

Per-feed specifics:
- **tlc** — drops `validity==0` rows and occupancy; only volume + speed mapped.
- **dai** — sums `#C1/#C2/#C3` into one count per lane, keeps Speed (a Speed of
  `-1` means "no valid speed" and is ignored), drops Occupancy. Camera + lane is
  the detector: "ARL 103" lane 1 → `ARL_103_1`.

### Per traverse (tlc / dai)

OSLO publishes cross-section counts (doorsnedetellingen), so the per-lane
detectors are summed per **traverse** with the client's
`UCComptageDetectorTraverse` table. `--traverse-map` takes that table as CSV,
exported with `django/manage.py export_live_traverse_map`:

```
detector_id,traverse_id,link_direction
ARL_103_1,ARL_103,inOppositeDirection
ARL_103_2,ARL_103,inOppositeDirection
```

Per traverse and minute:
- **count** = sum of the detector counts, published only when **every** detector
  of the traverse delivered a valid count (a partial sum would understate the
  cross-section);
- **speed** = vehicle-weighted mean of the detector speeds (the time-mean speed
  over all vehicles), published only when there were vehicles and every
  detector that counted vehicles has a valid speed;
- `geobserveerdObject` is the traverse (`loc:ARL_103`), the same IRI as in the
  traverse collection; the measurement IRI is
  `<traverse>-<feed>-<kenmerk>-<UTC start>`;
- `meetrichting` is the traverse's `link_direction` (omitted while empty, so
  consumers fall back to the meetpunt's `rijrichting`).

Detectors without a traverse (e.g. DAI lane 100, the camera total) are skipped;
the run lists them, and the traverses skipped for missing detectors, on stderr.
- **bike_live** — ignores the `tracks` property; vehicle = `fiets`.

**`bike_devices`** is the measure-LOCATION registry (a `verkeer:Verkeersmeetpunt`
with point geometry). The `_extended.json` file adds a road segment + measure
direction: `roadSegmentGeometry` (sibling of `geometry`, a `line` coordinate
array + `offset`) and `measureDirection` (in `properties`). These render as the
`bemonsterdObject → Rijrichting → Wegsegment` chain (line + begin/end nodes) plus
the offset `Puntreferentie`. Begin/end nodes are derived from the line's first/last
coordinates. Without `roadSegmentGeometry`, a feature maps to a bare
Verkeersmeetpunt + point.

## Layout

```
live-data/
├── export_live.py            # driver: --feed tlc|dai|bike_live|bike_devices
├── preprocess/               # one module per feed
│   ├── util.py               # shared row schema + codelist maps + helpers
│   ├── tlc.py
│   ├── dai.py
│   ├── bike_live.py
│   └── bike_devices.py
├── mapping/                  # one RML mapping per feed
│   ├── tlc.rml.ttl           # begin/end measurements
│   ├── dai.rml.ttl           # begin/end measurements
│   ├── bike_live.rml.ttl     # begin/end measurements
│   └── bike_devices.rml.ttl  # measure location (Verkeersmeetpunt)
└── output/                   # generated .ttl (gitignored)
```

To add a feed: drop a `preprocess/<feed>.py` exposing `preprocess(text)`, add a
`mapping/<feed>.rml.ttl`, and register it in `FEEDS` in `export_live.py`.

## Run

```bash
python3.8 -m venv .venv && .venv/bin/python -m pip install rdflib

.venv/bin/python export_live.py --feed tlc --traverse-map traverse_map.csv ../data-samples/tlc_20260313_1300.json
.venv/bin/python export_live.py --feed dai --traverse-map traverse_map.csv ../data-samples/dai_202602201350.xml
.venv/bin/python export_live.py --feed bike_live    ../data-samples/bike_live.json
.venv/bin/python export_live.py --feed bike_devices ../data-samples/bike_devices_extended.json
```

Output defaults to `output/<feed>.ttl`. Reads files only — no DB, no VPN; the
traverse map comes from the DB via the django command.

## RMLMapper jar

Expected at the **repo root** as `rmlmapper.jar` (shared by all folders; see the
django README for how to obtain it). Override with `RMLMAPPER_JAR` or `--jar`.
A JRE must be on `PATH` (validated with OpenJDK 17).

## Hard-coded, pending real data

- **Vehicle type** — `auto` (tlc/dai), `fiets` (bike_live). No per-row vehicle
  classification in the samples; inferred per feed.
- **Measure direction** — tlc/dai take `meetrichting` from the traverse map;
  `bike_live` still hard-codes `bothDirections` (device totals).
- **Local time** — TLC and DAI timestamps are assumed to be local Brussels time.
- **DAI `<Time>`** is assumed to be the interval **start** — verify against the
  DAI spec/provider.
