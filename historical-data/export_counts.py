"""Export historical count aggregations to OSLO Verkeersmeting RDF.

    python export_counts.py --table _15_veh_traverse_2026 --limit 100 -o out.ttl

No Django here: this connects to the `counts` Postgres directly (psycopg2),
fetches the N most recent rows of ONE aggregations.* table, splits each row into
per-characteristic measurements (volume -> aantal, speed -> tijdsgemiddelde_snelheid),
and runs RMLMapper to emit OSLO RDF -- the same preprocess -> RML pipeline used by
the Django connector.

Connection settings come from the environment with NO defaults (fails loudly):
    PG_COUNTS_HOST, PG_COUNTS_PORT, PG_COUNTS_DB, PG_USER, PGPASSWORD
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAPPING = HERE / "measurements.rml.ttl"
# The RMLMapper jar is expected at the repo root (one level up); override with
# RMLMAPPER_JAR to place it elsewhere.
DEFAULT_JAR = HERE.parent / "rmlmapper.jar"

# OSLO VkmVerkeersKenmerkType codes (mirrors the tlc mapping).
FOI_COUNT = "aantal"
FOI_SPEED = "tijdsgemiddelde_snelheid"

# system column -> VkmMeetInstrumentType (sensor type), per the codelist mapping
# (oslo-mapping/traffic_counts_codelist_mapping.md):
#   BIKE  -> glasvezel        (SL20007 v0 PUR SENSOR)
#   DAI   -> standaard_Camera (CAMERA)
#   ISAFE -> radar            (Icom)
#   TLC   -> inductielus      (LOOP)
# NOTE: the mapping is really by *detector type*, not system, and TLC spans two:
# LOOP -> inductielus and XSTREAM -> standaard_Camera. The counts tables carry
# only `system` (no detector-type column), so TLC cannot be split here; we map it
# to inductielus (LOOP, the dominant type -- 44 detectors vs 2 XSTREAM). Revisit
# if a detector-type column becomes available.
SENSOR_BY_SYSTEM = {
    "BIKE": "glasvezel",
    "DAI": "standaard_Camera",
    "ISAFE": "radar",
    "TLC": "inductielus",
}
DEFAULT_SENSOR = "inductielus"

# system column -> VkmVoertuigType (vehicle type). DAI/TLC/ISAFE count motor
# vehicles, BIKE counts bicycles. HARD-CODED per system -- the tables carry no
# per-row vehicle classification, so this is inferred from the system, not
# measured. TODO: revisit if a real per-row vehicle type becomes available.
VEHICLE_BY_SYSTEM = {"TLC": "auto", "DAI": "auto", "ISAFE": "auto", "BIKE": "fiets"}
DEFAULT_VEHICLE = "auto"

# Only allow plain aggregations table names (no schema-qualified / injection).
TABLE_RE = re.compile(r"^[A-Za-z0-9_]+$")

OSLO_PREFIXES = {
    "ex": "http://example.org/id/measurement/",
    "loc": "http://example.org/id/measureLocation/",
    "verkeer": "https://data.vlaanderen.be/ns/verkeersmetingen#",
    "impl": "https://implementatie.data.vlaanderen.be/ns/verkeersmetingen-uitwisseling#",
    "iso19156-ob": "http://def.isotc211.org/iso19156/2011/Observation#",
    "iso19103-mp": "http://def.isotc211.org/iso19103/2005/MeasureProfile#",
    "sosa": "http://www.w3.org/ns/sosa/",
    "terms": "http://purl.org/dc/terms/",
    "schema": "http://schema.org/",
    "cdt": "https://w3id.org/cdt/",
    "time": "http://www.w3.org/2006/time#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "VkmVerkeersKenmerkType": "https://data.vlaanderen.be/id/concept/VkmVerkeersKenmerkType/",
    "VkmMeetInstrumentType": "https://data.vlaanderen.be/id/concept/VkmMeetInstrumentType/",
    "VkmVoertuigType": "https://data.vlaanderen.be/id/concept/VkmVoertuigType/",
    "LinkDirectionValue": "http://inspire.ec.europa.eu/codelist/LinkDirectionValue/",
}


def require_env(name):
    try:
        return os.environ[name]
    except KeyError:
        sys.exit(f"Required environment variable {name!r} is not set (no defaults).")


def fetch_rows(table, limit):
    """Most-recent `limit` rows of aggregations.<table> as list[dict]."""
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(
        host=require_env("PG_COUNTS_HOST"), port=require_env("PG_COUNTS_PORT"),
        dbname=require_env("PG_COUNTS_DB"), user=require_env("PG_USER"),
        password=require_env("PGPASSWORD"),
        options="-c default_transaction_read_only=on",  # read-only session
    )
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # table name is validated against TABLE_RE; quoted as an identifier.
            cur.execute(
                f'SELECT * FROM aggregations."{table}" ORDER BY from_ts DESC LIMIT %s',
                (limit,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def iso_duration(from_ts, to_ts):
    """Whole-minute ISO 8601 duration between two datetimes, rounded up."""
    secs = max(0, int(round((to_ts - from_ts).total_seconds())))
    minutes = (secs + 59) // 60
    return f"PT{minutes}M"


def uri_safe(value):
    """Make a column value safe for use in a URI path segment.

    Strips the characters that are invalid/awkward in a URI -- space, ':' and
    '.' -- by deleting them (not replacing), so "SB020_BDout2026-04-08 21:45:00.000"
    becomes "SB020_BDout2026-04-082145000". Existing '_'/'-' are kept as-is.
    """
    return re.sub(r"[ :.]", "", str(value))


def brussels_offset(utc_ts, brussels_ts):
    """ISO offset like '+02:00' derived from the two stored timestamps.

    Both columns are 'timestamp without time zone' (no offset stored), but
    brussels_ts - utc_ts IS the local offset in effect at that instant -- so we
    read DST correctly without guessing it from the date. Falls back to 'Z' if
    the Brussels column is missing.
    """
    if brussels_ts is None:
        return "Z"
    secs = int(round((brussels_ts - utc_ts).total_seconds()))
    sign = "+" if secs >= 0 else "-"
    secs = abs(secs)
    return f"{sign}{secs // 3600:02d}:{(secs % 3600) // 60:02d}"


_warned_systems = set()


def _warn_unknown_system(system):
    """Warn once per unmapped system, so fallback defaults don't pass silently."""
    if system not in _warned_systems:
        _warned_systems.add(system)
        print(f"WARNING: system {system!r} not in SENSOR_BY_SYSTEM -- "
              f"falling back to sensor={DEFAULT_SENSOR!r}, vehicle={DEFAULT_VEHICLE!r}",
              file=sys.stderr)


def preprocess(rows):
    """Count rows -> flat measurement list (one entry per kept characteristic).

    Each row yields up to two measurements: volume->aantal and speed->speed.
    None/absent values are skipped. rowId ties the joined sub-maps together.
    """
    out = []
    for i, r in enumerate(rows):
        trav = r.get("trav_id")
        from_ts, to_ts = r.get("from_ts"), r.get("to_ts")
        if trav is None or from_ts is None:
            continue
        # Emit Brussels-local wall-clock with the correct offset (derived from the
        # UTC vs Brussels columns, so DST is exact). startId stays UTC-based to
        # keep URIs stable across the DST changeover.
        b_from = r.get("from_ts_brussels")
        offset = brussels_offset(from_ts, b_from)
        local_from = b_from if b_from is not None else from_ts
        start_iso = local_from.strftime("%Y-%m-%dT%H:%M:%S") + offset
        duration = iso_duration(from_ts, to_ts) if to_ts else "PT0M"
        # Measurement URI = uid + feature of interest. uid contains spaces/colons
        # (e.g. "SB020_BDout2026-04-08 21:45:00.000"), so make it URI-safe.
        uid_safe = uri_safe(r.get("uid") or trav)
        system = r.get("system")
        if system not in SENSOR_BY_SYSTEM:
            _warn_unknown_system(system)   # surface silent fallbacks once per system
        sensor = SENSOR_BY_SYSTEM.get(system, DEFAULT_SENSOR)       # VkmMeetInstrumentType
        vehicle = VEHICLE_BY_SYSTEM.get(system, DEFAULT_VEHICLE)    # VkmVoertuigType
        # kind tags count vs speed so the mapping can route each to its own
        # kenmerk class/predicate (Verkeerstellingkenmerk vs
        # Verkeerssnelheidsmetingkenmerk). Both share the observation shape.
        for foi, kind, val in (
            (FOI_COUNT, "count", r.get("volume")),
            (FOI_SPEED, "speed", r.get("speed")),
        ):
            if val is None:
                continue
            out.append({
                "rowId": f"{i}-{foi}",
                "measId": f"{uid_safe}-{foi}",
                "travId": trav,
                "foi": foi,
                "kind": kind,
                "value": val,
                "startISO": start_iso,
                "durationISO": duration,
                "sensor": sensor,       # system -> VkmMeetInstrumentType
                "vehicle": vehicle,     # system -> VkmVoertuigType (hard-coded)
            })
    # Also expose per-kind arrays so the two kenmerk maps (count vs speed) each
    # iterate ONLY their own rows -- avoids emitting stray kenmerk nodes for the
    # other kind. `measurements` stays for the shared observation/time/sensor maps.
    return {
        "measurements": out,
        "counts": [m for m in out if m["kind"] == "count"],
        "speeds": [m for m in out if m["kind"] == "speed"],
    }


def materialize(data, output, fmt, jar):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        data_path = tmp / "counts.json"
        data_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        mapping_path = tmp / "mapping.ttl"
        mapping_path.write_text(
            MAPPING.read_text(encoding="utf-8").replace("SOURCE.json", str(data_path)),
            encoding="utf-8",
        )
        nt_path = tmp / "out.nt"
        cmd = ["java", "-jar", str(jar), "-m", str(mapping_path),
               "-o", str(nt_path), "-s", "ntriples"]
        print("running:", " ".join(cmd), file=sys.stderr)
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            sys.exit(f"rmlmapper failed (exit {res.returncode}):\n{res.stderr}")

        import rdflib
        g = rdflib.Graph()
        g.parse(str(nt_path), format="nt")
        for prefix, ns in OSLO_PREFIXES.items():
            g.bind(prefix, ns)
        g.serialize(destination=str(output), format=fmt)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--table", required=True,
                   help="aggregations table name, e.g. _15_veh_traverse_2026")
    p.add_argument("--limit", type=int, required=True,
                   help="number of most-recent rows (ORDER BY from_ts DESC).")
    p.add_argument("-o", "--output", type=Path, default=Path("counts.ttl"))
    p.add_argument("-f", "--format", default="turtle")
    p.add_argument("--jar", default=os.environ.get("RMLMAPPER_JAR", str(DEFAULT_JAR)))
    args = p.parse_args(argv)

    if not TABLE_RE.match(args.table):
        sys.exit(f"invalid table name: {args.table!r} (letters, digits, underscore only)")
    if args.limit <= 0:
        sys.exit("--limit must be > 0")
    jar = Path(args.jar)
    if not jar.is_file():
        sys.exit(f"rmlmapper jar not found: {jar} (set RMLMAPPER_JAR or --jar)")

    rows = fetch_rows(args.table, args.limit)
    print(f"fetched {len(rows)} rows from aggregations.{args.table}", file=sys.stderr)
    data = preprocess(rows)
    print(f"prepared {len(data['measurements'])} measurements", file=sys.stderr)
    materialize(data, args.output, args.format, jar)
    print(f"wrote OSLO RDF -> {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
