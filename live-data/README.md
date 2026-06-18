# live-data → OSLO RDF

Transforms the **live feed** samples in `../data-samples/` into OSLO
Verkeersmetingen RDF via RMLMapper. Ported from the Morph-KGC/YARRRML mappings in
the (closed) PR #1, now using RMLMapper (a JVM tool) + the same preprocess → RML
pipeline as `django/` and `historical-data/`.

## Feeds

| `--feed` | Sample file | Identity | Sensor (`VkmMeetInstrumentType`) |
|---|---|---|---|
| `tlc` | `tlc_*.json` | detector | `inductielus` |
| `dai` | `dai_*.xml` | camera-lane | `standaard_Camera` |

Each row becomes per-characteristic observations: **volume → aantal**
(`impl:Verkeerstelling`) and **speed → tijdsgemiddelde_snelheid**
(`impl:Verkeerssnelheidsmeting`, with an `OM_Observation.result` Speed in km/h).
TLC drops `validity==0` rows and occupancy; DAI sums `#C1/#C2/#C3` into one count
and drops Occupancy.

> `bike_live.json` is **not** handled yet — it is a cumulative-counter shape
> (day/hour/year totals) that doesn't match any PR #1 mapping; deferred pending a
> modelling decision.

## Layout

```
live-data/
├── export_live.py        # driver: --feed tlc|dai
├── preprocess/
│   └── preprocess.py      # both feed preprocessors -> unified row schema
├── mapping/
│   └── live.rml.ttl       # one feed-agnostic RML mapping
└── output/                # generated .ttl (gitignored)
```

## Run

```bash
python3.8 -m venv .venv && .venv/bin/python -m pip install rdflib

.venv/bin/python export_live.py --feed tlc ../data-samples/tlc_20260313_1300.json
.venv/bin/python export_live.py --feed dai ../data-samples/dai_202602201350.xml
```

Output defaults to `output/<feed>.ttl`. Reads sample files only — no DB, no VPN.

## RMLMapper jar

Expected at the **repo root** as `rmlmapper.jar` (shared by all folders; see the
django README for how to obtain it). Override with `RMLMAPPER_JAR` or `--jar`.
A JRE must be on `PATH` (validated with OpenJDK 17).

## Hard-coded, pending real data

- **Vehicle type** → `auto` for both feeds (no per-row classification in the
  samples).
- These feeds carry no measure direction, so none is emitted (unlike the
  historical-data export, which hard-codes `bothDirections`).
