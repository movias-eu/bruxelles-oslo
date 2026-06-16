# oslo-mapping

Transforms Brussels Mobility traffic-measurement feeds (JSON and XML) into
[OSLO Verkeersmetingen](https://data.vlaanderen.be/doc/applicatieprofiel/verkeersmetingen/)
RDF, using [RML](https://rml.io/) mappings executed by
[Morph-KGC](https://morph-kgc.readthedocs.io/).

Each feed goes through **two steps**: a small Python *preprocessor* reshapes the
source data into a flat per-measurement list, then a *mapping* turns that list
into RDF. The pipeline is in-process Python (embeddable in Django) and reads its
input in memory, mirroring an API response.

## Pipeline

```
source (JSON/XML)  ──preprocess_<feed>.py──▶  flat measurement JSON  ──rml_map.py + <feed>.yml──▶  RDF
```

| Component | Path | Role |
|---|---|---|
| Mapping runner | [`rml_map.py`](rml_map.py) | Generic engine: loads the data in memory and runs Morph-KGC against a YARRRML mapping. Feed-agnostic. |
| Preprocessors | [`preprocessing/`](preprocessing/) | One script per feed; reshapes source data for the RML engine. |
| Mappings | [`rml-mappings/`](rml-mappings/) | One YARRRML mapping per feed → OSLO RDF. |
| Codelist analysis | [`traffic_counts_codelist_mapping.md`](traffic_counts_codelist_mapping.md) | Source→codelist analysis the mappings are based on. |

### Running each feed

All commands run from this directory.

```bash
# bike_summary (bike counts + speed)
uv run preprocessing/preprocess_bike_summary.py ../data-samples/bike_summary_20260215.json -o measurements.json
uv run rml_map.py rml-mappings/bike_summary.yml measurements.json -o bike_summary.ttl

# dai (camera counts + speed, XML)
uv run preprocessing/preprocess_dai.py ../data-samples/dai_202602201350.xml -o dai_measurements.json
uv run rml_map.py rml-mappings/dai.yml dai_measurements.json -o dai.ttl

# tlc (loop/camera counts + speed)
uv run preprocessing/preprocess_tlc.py ../data-samples/tlc_20260313_1300.json -o tlc_measurements.json
uv run rml_map.py rml-mappings/tlc.yml tlc_measurements.json -o tlc.ttl

# bike_devices (device inventory / locations — no preprocessing needed)
uv run rml_map.py rml-mappings/bike_devices.yml ../data-samples/bike_devices.json -o bike_devices.ttl
```

## Why a preprocessing step exists

Morph-KGC's in-memory JSON handling has limits that the preprocessors work
around so the mappings stay declarative:

- **No parent-context.** A reference cannot reach a parent field from a deep
  `rml:iterator` (e.g. `device` is unreachable from
  `$[*].data[*].detections[*]`). Preprocessors flatten nesting and stamp parent
  identity onto each row.
- **No indexed/filtered references.** `coordinates[0]` and
  `streetName[?(@.language=='nl')]` are dropped in-memory. Preprocessors expand
  or restructure such values; mappings iterate arrays instead.
- **Top-level JSON arrays.** A parsed Python `list` is mistaken for a flat
  DataFrame, ignoring the iterator. `rml_map.py` passes JSON as a raw string to
  avoid this.
- **Null-row drop.** A row is dropped if any referenced field is null, so
  optional fields (speed, temperature) live in their own triples maps.

These are Morph-KGC engine limitations, not RML or YARRRML limitations. See the
commit history / mapping comments for the empirical verification.

## What each preprocessor does

| Feed | Source shape | Key transforms |
|---|---|---|
| `bike_summary` | nested device → track → detections | flatten to one row per detection; explode into one row per feature-of-interest (count `aantal`, speed `tijdsgemiddelde_snelheid`); ISO timestamp; URI-safe `startId`/`trackId`; expand `classesCount` → `classes[]` |
| `dai` | flat XML `<Measure>` stream | classify `Type`: `#C1/#C2/#C3` → count (with vehicle class), `Speed` → speed, **drop `Occupancy`**; ISO timestamp; duration from `PeriodSec` |
| `tlc` | flat JSON `counts[]` | split `volume` → count and `speed` → speed, **drop `occupancy`**; **drop `validity == 0`**; ISO timestamp; duration from from/to window |

## Modelling decisions

Applied consistently across the count/speed feeds (bike_summary, dai, tlc):

- **Two measurements per reading.** A count (`impl:Verkeerstelling`, kenmerk
  `aantal`) and a speed (`impl:Verkeerssnelheidsmeting`, kenmerk
  `tijdsgemiddelde_snelheid`) are **separate** observations with distinct
  subject URIs. The feature-of-interest is part of the URI.
- **Measurement URI**: `…/{identity}-{featureOfInterest}-{startId}`;
  **`terms:isVersionOf`**: `…/{identity}-{featureOfInterest}` (time-independent,
  per characteristic). Identity is device+track (bike), camera+lane+class (dai),
  or detector (tlc).
- **Time**: naive ISO `dateTime` (no timezone — the sources carry none);
  `time:hasXSDDuration` from the source window (`PT15M` bike, `PT1M`/`PT60S`
  dai/tlc).
- **Direction**: `LinkDirectionValue:bothDirections` for all feeds.
- **Sub-resources** (phenomenonTime, instant, sensor, result, kenmerk) are
  blank nodes.
- **Sensor type**: `inductielus` (bike, tlc), `standaard_Camera` (dai).
- **Vehicle type**: `fiets` (bike), `auto` (dai, tlc).
- **Dropped, no OSLO codelist match**: `occupancy` (dai, tlc).

## Open questions — to verify

These are assumptions made to produce a runnable mapping; confirm or correct
them with the data owner.

1. **DAI vehicle classes `#C1` / `#C2` / `#C3`.** Their meaning is undocumented.
   We currently map **all three to `voertuigType auto`** and keep the raw class
   in the measurement URI (`…-C1-…`). If the classes distinguish vehicle types
   (e.g. car / van / truck), `dai.yml`'s `countKenmerk` should branch
   `voertuigType` per class. **Need: the `#Cn` → vehicle-type definitions.**

2. **TLC sensor type (LOOP vs XSTREAM).** Per the codelist, TLC detectors are
   either LOOP (`inductielus`) or XSTREAM (`standaard_Camera`), but the detector
   name doesn't reveal which. We default **all TLC sensors to `inductielus`**
   (LOOP is 44 of 46 detectors). **Need: a detector → sensor-type lookup** to
   correctly tag the ~2 XSTREAM detectors.

3. **Timezone.** Source timestamps carry no timezone; we emit naive ISO
   `dateTime`. The reference OSLO examples used `+00:00` (UTC). **Need: confirm
   whether source times are UTC, Brussels local, or genuinely zone-less.**

4. **Direction (`rijrichting` / `meetrichting`).** Hard-coded to
   `bothDirections`. The codelist analysis shows direction can sometimes be
   derived from traverse orientation / naming (`*_RING_INT`, opposite-direction
   traverse pairs). **Need: confirm whether per-detector direction should be
   derived instead of defaulting to bothDirections.**

5. **`bike_summary` per-class counts.** The preprocessor expands `classesCount`
   into a `classes[]` list, but `bike_summary.yml` does **not** yet map the
   per-vehicle-class breakdown (only the total `count`). **Need: confirm whether
   per-class counts should be emitted, and how the class codes map to
   `VkmVoertuigType`.**

6. **`bike_devices` geometry.** Coordinates are emitted as two separate
   `locn:coordinate` literals rather than a single `geo:asWKT "POINT(lon lat)"`,
   because Morph-KGC cannot combine indexed array elements in a template
   in-memory without preprocessing. **Need: confirm two-literal geometry is
   acceptable, or allow a one-line WKT derivation in preprocessing.**

7. **TLC `validity`.** Rows with `validity == 0` are **dropped**. The invalid
   reading is therefore absent from the graph rather than recorded as invalid.
   **Need: confirm dropping is preferred over recording a validity flag.**
