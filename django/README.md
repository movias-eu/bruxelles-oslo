# `webtools` Django mimic (read-only)

A minimal, **read-only** Django project that mirrors part of the client's
`webtools` application so we can read the real Postgres `traffic_*` tables through
the Django ORM — the same way the client's app does. This is a probe for designing
the OSLO "connector", **not** a copy of the client's full app.

- Runtime: **Python 3.8 + Django 3.2 LTS** (matches the client's server).
- Models: the traverse subgraph (`UCComptageTraverses` + `UCComptageLink`),
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

## The RMLMapper jar

`export_oslo` shells out to the **RMLMapper** fat jar (a JVM tool — this is why
the Python version doesn't matter to the mapping engine). It is a ~176 MB binary
and is **gitignored** (`vendor/*.jar`), so each checkout must provide it once.

**Where to get it:** download the `-all` (all-dependencies) jar from the RMLMapper
releases. Validated with **7.3.3-r374**:

```bash
# from the django/ project root
curl -L -o vendor/rmlmapper.jar \
  https://github.com/RMLio/rmlmapper-java/releases/download/v7.3.3/rmlmapper-7.3.3-r374-all.jar
```

**Where to put it:** `django/vendor/rmlmapper.jar`. The command resolves the jar
in this order:

1. `--jar <path>` argument
2. `RMLMAPPER_JAR` environment variable
3. default: `django/vendor/rmlmapper.jar`

So no configuration is needed if the jar sits there with that name. To use a
different location, set `RMLMAPPER_JAR` (or pass `--jar`); the default path itself
is defined as `DEFAULT_JAR` near the top of
`traffic/management/commands/export_oslo.py` if you want to change it permanently.

A JRE must be on `PATH` (`java -version`); validated with OpenJDK 17.

## OSLO mapping: current state & open items

To produce a complete OSLO record matching `oslo-mapping/bike_devices.ttl`, a few
things are **scaffolded for now and must be revisited before production**:

- **Segment matches are mocked.** `traffic_segmentmatch` is currently empty, so
  `export_oslo` injects one fake match into the first feature purely to exercise
  the segment-match → RDF path end to end. This is fenced with a loud warning and
  a `TEMPORARY MOCK — REMOVE` comment in `export_oslo.py`. **Delete it the moment
  real match rows exist**, or it will fabricate a bogus match on top of real data.

- **The `offset` column does not exist yet.** OSLO needs the distance (in metres)
  of the traverse location from the start of the segment line — the
  `Puntreferentie → opPositie` shape. The real `traffic_segmentmatch` table has no
  such column, so:
  - it is declared on the `SegmentMatch` model but **deferred** in the query
    (`.defer("offset")`) so Postgres is never asked for it, and
  - its value currently comes **only from the mock**.

  **This column still needs to be added to the real table.** Once it exists, drop
  the `.defer("offset")` in `export_oslo.py` and remove the field comment — the
  value then flows automatically.

- **Segment geometry is derived from a single WKB.** `traffic_segmentmatch` stores
  the segment geometry as one WKB blob (the `wkb` column). OSLO wants the line
  plus its **begin and end points** separately. We compute those at runtime:
  decode the WKB → WKT, then take the first and last coordinates as the begin/end
  nodes (`preprocess.wkb_to_geometry`, handling `LineString` and
  `MultiLineString`). **This is a design decision.** The alternative is to add
  explicit columns (e.g. `start_point`, `end_point`) to the table instead of
  deriving them — cheaper at query time, but more columns to maintain and keep
  consistent with the WKB.
