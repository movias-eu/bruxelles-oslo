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
| `bike_live` | `bike_live.json` | measurements (running totals) | device | `glasvezel` |
| `bike_summary` | `bike_summary_*.json` | measurements (closed 15 min) | traverse (counter tracks summed) | `glasvezel` |
| `bike_devices` | `bike_devices_extended.json` | measure location | name | — |

**Measurement feeds** (tlc / dai / bike_live / bike_summary) emit per-characteristic
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
- **bike_summary** — `start` / `end` of each 15-minute period (both in the data).

Per-feed specifics:
- **tlc** — drops `validity==0` rows and occupancy; only volume + speed mapped.
- **dai** — sums `#C1/#C2/#C3` into one count per lane, keeps Speed (a Speed of
  `-1` means "no valid speed" and is ignored), drops Occupancy. Camera + lane is
  the detector: "ARL 103" lane 1 → `ARL_103_1`.
- **bike_live** — ignores the `tracks` property; vehicle = `fiets`.
- **bike_summary** — `count` per track (the sum of `classesCount`) and
  `averageSpeed` (absent when nothing passed); `classesCount` and
  `averageTemperature` are dropped. Device + track is the detector: "CEK049" +
  "Track1.1" → `CEK049_Track1.1`. Periods that end after the current time are
  still running and are skipped.

### bike_live or bike_summary for `live_bike`

`bike_live` gives running totals (cyclists so far this hour, today, this year).
Each new file gives the same measurement a higher value and a later end time,
so a consumer that harvests the collection gets two values for one
measurement. Brussels Mobility chose (20/11) to publish closed periods only.
`bike_summary` has those: one count per track per finished 15 minutes, which
never changes afterwards. It is the intended source for `live_bike`; `bike_live`
stays available until the switch.

### Per traverse (tlc / dai / bike_summary)

OSLO publishes cross-section counts (doorsnedetellingen), so the per-lane
detectors, and the tracks of a bike counter, are summed per **traverse** with
the client's `UCComptageDetectorTraverse` table. `--traverse-map` takes that
table as CSV, exported with `django/manage.py export_live_traverse_map`:

```
detector_id,traverse_id,link_direction
ARL_103_1,ARL_103,inOppositeDirection
ARL_103_2,ARL_103,inOppositeDirection
```

Per traverse and period (a minute for tlc/dai, 15 minutes for bike_summary):
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

A bike track can count towards several traverses, e.g. `CB1101_Track1.1`
towards `CB1101` (the total) and `CB1101_inner_ring` (one direction).

Detectors without a traverse (e.g. DAI lane 100, the camera total) are skipped;
the run lists them, and the traverses skipped for missing detectors, on stderr.

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
├── export_live.py            # driver: --feed tlc|dai|bike_live|bike_summary|bike_devices
├── preprocess/               # one module per feed
│   ├── util.py               # shared row schema + codelist maps + helpers
│   ├── tlc.py
│   ├── dai.py
│   ├── bike_live.py
│   ├── bike_summary.py
│   └── bike_devices.py
├── mapping/                  # one RML mapping per feed
│   ├── tlc.rml.ttl           # begin/end measurements
│   ├── dai.rml.ttl           # begin/end measurements
│   ├── bike_live.rml.ttl     # begin/end measurements
│   ├── bike_summary.rml.ttl  # begin/end measurements
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
.venv/bin/python export_live.py --feed bike_summary --traverse-map traverse_map.csv ../data-samples/bike_summary_20260215.json
.venv/bin/python export_live.py --feed bike_devices ../data-samples/bike_devices_extended.json
```

Output defaults to `output/<feed>.ttl`. Reads files only — no DB, no VPN; the
traverse map comes from the DB via the django command.

## RMLMapper jar

Expected at the **repo root** as `rmlmapper.jar` (shared by all folders; see the
django README for how to obtain it). Override with `RMLMAPPER_JAR` or `--jar`.
A JRE must be on `PATH` (validated with OpenJDK 17).

## Hard-coded, pending real data

- **Vehicle type** — `auto` (tlc/dai), `fiets` (bike_live/bike_summary). No per-row vehicle
  classification in the samples; inferred per feed.
- **Measure direction** — tlc/dai/bike_summary take `meetrichting` from the traverse map;
  `bike_live` still hard-codes `bothDirections` (device totals).
- **Local time** — TLC and DAI timestamps are local Brussels time: in the API
  snapshot fetched on 17/11 at 11:07 local time, TLC ran to 11:06 and DAI to
  10:48, which would lie in the future as UTC. bike_summary is assumed local
  too, but its sample of 15/02 runs from 14/02 23:00 to 15/02 22:45, which
  would also fit UTC (the local day 15/02 written in UTC). To confirm.
- **DAI `<Time>`** is assumed to be the interval **start** — verify against the
  DAI spec/provider.
