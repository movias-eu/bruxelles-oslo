# `webtools` Django mimic (read-only)

A minimal, **read-only** Django project that mirrors part of the client's
`webtools` application so we can read the real Postgres `traffic_*` tables through
the Django ORM — the same way the client's app does. This is a probe for designing
the OSLO "connector", **not** a copy of the client's full app.

- Runtime: **Python 3.8 + Django 3.2 LTS** (matches the client's server).
- Models: the traverse subgraph (`UCComptageTraverses` + `UCComptageLink`) and
  the detector links (`UCComptageDetector` + `UCComptageDetectorTraverse`),
  all `managed = False` so Django **never** creates/alters/drops tables.
- **`SegmentMatch` we wrote ourselves.** The original `traffic_models.txt` /
  `traffic_admin.txt` the client provided did **not** include it, because the
  `traffic_segmentmatch` table did not exist yet at that time. We mirrored it
  from the live DB schema (also `managed = False`) so the connector can map the
  traverse ↔ segment-match relation.

## Prerequisites

1. **VPN up.** The DB (`10.1.10.180`) is only reachable over the FortiClient
   (openfortivpn) tunnel. Check with `ip -br addr show | grep ppp` — you want a
   `ppp0` interface. If it's gone, re-establish the tunnel before running anything.
2. **DB connection vars set.** All connection settings come from the environment
   ([`../.envrc`](../.envrc), loaded by direnv) — nothing is hardcoded in
   `settings.py`, and there are **no defaults**: a missing variable fails loudly.
   All of these are **required**:
   `PG_METADATA_DB`, `PG_USER`, `PGPASSWORD`, `PG_METADATA_HOST`, `PG_METADATA_PORT`.
3. **Java + the RMLMapper jar** — only needed for `export_oslo`. See
   [The RMLMapper jar](#the-rmlmapper-jar) below.

## How to run

No need to "activate" the venv — call the venv's Python directly. From the repo
(direnv exports `PGPASSWORD` automatically):

```bash
cd ~/bruxelles-oslo/django
.venv/bin/python manage.py <command>
```

Or explicitly from anywhere:

```bash
direnv exec ~/bruxelles-oslo \
  ~/bruxelles-oslo/django/.venv/bin/python ~/bruxelles-oslo/django/manage.py <command>
```

## Commands

All commands are read-only against the DB and safe to run repeatedly.

### `export_oslo` — the connector

Materialises OSLO Verkeersmetingen RDF for the traverses. Reads traverses (and
their segment matches) via the ORM, reprojects/decodes in Python, then runs
RMLMapper to emit the RDF.

```bash
.venv/bin/python manage.py export_oslo -o traverses.ttl
```

Options:

| Flag | Meaning |
|---|---|
| `-o, --output` | Output file (default `traverses.ttl`). |
| `-f, --format` | RDF serialization: `turtle` (default), `nquads`, `json-ld`, … |
| `--jar` | Path to the RMLMapper jar (overrides the default / `RMLMAPPER_JAR`). |
| `--keep-json` | Also write the intermediate FeatureCollection JSON next to the output. |

Pipeline: `ORM rows → preprocess() (reproject + decode) → GeoJSON → RMLMapper → RDF`.

### `check_db` — connection sanity check

Connects, counts traverses, shows the `veh_type` breakdown decoded via
`VEH_TYPES`, and prints the first few rows with their joined link description.

```bash
.venv/bin/python manage.py check_db
```

### `check_segmentmatch` — traverses with their segment matches

Lists traverses together with their segment matches over the FK relation. Returns
every traverse (matches empty today, populated automatically once the table fills).

```bash
.venv/bin/python manage.py check_segmentmatch --limit 5
```

### `compute_link_direction` — propose each traverse's direction

Computes the INSPIRE direction of every segment-matched traverse from its
`orientation` and the matched segment (see
[Implementation notes](#implementation-notes-for-brussels-mobility)) and writes a
CSV report: orientation, segment heading, angle, current and proposed value,
reason. Read-only; `--all` also lists traverses without a match.

```bash
.venv/bin/python manage.py compute_link_direction -o proposals.csv
```

### `export_live_traverse_map` — detector → traverse map for the live export

Writes the active `UCComptageDetectorTraverse` links as
`detector_id,traverse_id,link_direction`, the `--traverse-map` input of
`live-data/export_live.py` (TLC/DAI are summed per traverse).

```bash
.venv/bin/python manage.py export_live_traverse_map -o traverse_map.csv
```

## The RMLMapper jar

`export_oslo` shells out to the **RMLMapper** fat jar (a JVM tool — this is why
the Python version doesn't matter to the mapping engine). It is a ~176 MB binary
and is **gitignored**, so each checkout must provide it once. The jar lives at the
**repo root** and is shared by all folders (`django/`, `historical-data/`,
`live-data/`).

**Where to get it:** download the `-all` (all-dependencies) jar from the RMLMapper
releases. Validated with **7.3.3-r374**:

```bash
# from the repo root
curl -L -o rmlmapper.jar \
  https://github.com/RMLio/rmlmapper-java/releases/download/v7.3.3/rmlmapper-7.3.3-r374-all.jar
```

**Where to put it:** `<repo-root>/rmlmapper.jar`. The command resolves the jar
in this order:

1. `--jar <path>` argument
2. `RMLMAPPER_JAR` environment variable
3. default: `<repo-root>/rmlmapper.jar`

So no configuration is needed if the jar sits at the repo root with that name. To
use a different location, set `RMLMAPPER_JAR` (or pass `--jar`); the default path
itself is defined as `DEFAULT_JAR` near the top of
`traffic/management/commands/export_oslo.py` if you want to change it permanently.

A JRE must be on `PATH` (`java -version`); validated with OpenJDK 17.

## OSLO mapping: current state & open items

To produce a complete OSLO record matching `oslo-mapping/bike_devices.ttl`, a few
things are **scaffolded for now and must be revisited before production**:

- **New columns are read only when they exist.** `SegmentMatch.offset` and
  `UCComptageTraverses.link_direction` / `link_direction_manual` are declared on
  the models ahead of the client's migration. The commands check the table
  (`traffic.columns.missing_columns`) and `.defer()` whatever is missing, so they
  work before and after the columns are added; missing values come out empty.

- **Segment geometry is derived from a single WKB.** `traffic_segmentmatch` stores
  the segment geometry as one WKB blob (the `wkb` column). OSLO wants the line
  plus its **begin and end points** separately. We compute those at runtime:
  decode the WKB → WKT, then take the first and last coordinates as the begin/end
  nodes (`preprocess.wkb_to_geometry`, handling `LineString` and
  `MultiLineString`). **This is a design decision.** The alternative is to add
  explicit columns (e.g. `start_point`, `end_point`) to the table instead of
  deriving them — cheaper at query time, but more columns to maintain and keep
  consistent with the WKB.

## Implementation notes for Brussels Mobility

What to add on the client's side so the export can publish the driving
direction of each traverse. Values follow INSPIRE `LinkDirectionValue`, the
codelist OSLO uses for `weg:rijrichting` (on the traverse) and `meetrichting`
(on each measurement).

### 1. New fields on `UCComptageTraverses`

```python
LINK_DIRECTION_CHOICES = (
    ("inDirection", "In digitisation direction of the segment"),
    ("inOppositeDirection", "Against digitisation direction of the segment"),
    ("bothDirections", "Both directions"),
)

class UCComptageTraverses(models.Model):
    ...
    link_direction = models.CharField(
        "Link direction", max_length=20, null=True, blank=True,
        choices=LINK_DIRECTION_CHOICES,
    )
    link_direction_manual = models.BooleanField(
        "Link direction set manually", default=False,
    )
```

Then `makemigrations` + `migrate`. Both fields are additive; existing rows get
`link_direction = NULL` and `link_direction_manual = False`.

| Value | Meaning |
|---|---|
| `inDirection` | The traverse counts traffic driving the way the matched segment is digitised. |
| `inOppositeDirection` | The traverse counts traffic driving against the digitisation direction. |
| `bothDirections` | The traverse counts both directions together (e.g. a bike total). |
| empty | Unknown: the export then publishes no direction for this traverse. |

The value is **relative to the matched segment** (`traffic_segmentmatch`), not a
compass direction: when a traverse gets another segment, recompute it.

### 2. Automatic computation

`traffic/oslo/direction.py` holds the rule as plain functions (no DB access);
copy it into the client app. `orientation` is the heading of the measured
traffic (0 = north, 90 = east). It is compared with the heading of the matched
segment at the traverse location:

- angle ≤ 60° → `inDirection`
- angle ≥ 120° → `inOppositeDirection`
- in between, no orientation, no segment match, or matches that disagree →
  empty, to be set by hand.

Run it for every traverse that was not set by hand, e.g. after segment matches
change or nightly:

```python
from traffic.oslo.direction import combine, compute_link_direction

for t in (UCComptageTraverses.objects.filter(link_direction_manual=False)
          .prefetch_related("Traverse")):           # reverse accessor of SegmentMatch
    matches = list(t.Traverse.all())
    d = combine([compute_link_direction(t.orientation, m.wkb, t.co_x, t.co_y)
                 for m in matches])
    if t.link_direction != d.value:
        t.link_direction = d.value
        t.save(update_fields=["link_direction"])
```

`manage.py compute_link_direction` prints the same proposals as CSV without
writing, to review them first. On the 11 traverses matched today it proposes
`inOppositeDirection` for ARL_103, DEL_103 and VP_203, `inDirection` for ARL_203
and VP_103, and leaves GH_104 and the five radars empty (no orientation).

### 3. Manual values

Set `link_direction` in the admin and tick `link_direction_manual`, so the
computation leaves it alone. Needed for:

- **bike totals** (traverses that sum the tracks of both directions, e.g.
  `CB1101`, `CB1143`): `bothDirections`;
- **directional bike traverses** (e.g. `CB1101_inner_ring`) and **radars**
  (`orientation` is empty for all of them): the direction compared with the
  matched segment, or fill `orientation` and let the computation do it;
- traverses the computation leaves empty with "check manually".

### 4. Live data

The live TLC/DAI export sums the detectors per traverse and gives each
measurement the traverse's direction. It needs the detector → traverse map:

```bash
python manage.py export_live_traverse_map -o traverse_map.csv
python live-data/export_live.py --feed tlc --traverse-map traverse_map.csv <tlc file>
python live-data/export_live.py --feed dai --traverse-map traverse_map.csv <dai file>
```

Re-export the map whenever detectors, traverse links or directions change.

