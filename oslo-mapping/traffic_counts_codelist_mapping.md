# Traffic Counts Database Analysis & Codelist Mapping

## Overview

This document describes the analysis of the Brussels Mobility traffic counting databases and the mapping of source values to Linked Data codelists for the OSLO Traffic Measurements model.

---

## 1. Database Infrastructure

### Connection Details

| Database | Host | Port | Name | Purpose |
|----------|------|------|------|---------|
| Counts | 10.1.10.180 | 5434 | counts | Time-series measurement data |
| Webtools | 10.1.10.180 | 5432 | webtools | Reference/metadata tables |

### Database Statistics

| Database | Tables |
|----------|--------|
| counts | 136 |
| webtools | 504 (10 accessible traffic_* tables) |

---

## 2. Counts Database Structure

### Schema: aggregations

Tables are partitioned by:
- **Time interval**: 5, 15, or 60 minutes
- **Vehicle type**: `veh` (vehicles) or `bike` (bicycles)
- **Data source**: `detector` or `traverse`
- **Year**: 2015-2026

#### Example Tables
- `aggregations._15_veh_detector_2024` — 15-min vehicle detector data
- `aggregations._60_bike_traverse_2024` — hourly bike traverse data
- `aggregations._15_bike_type_detector_2024` — 15-min bike type data

#### Common Columns

| Column | Type | Description |
|--------|------|-------------|
| uid | text | Unique identifier (detector + timestamp) |
| system | text | Sensor system (DAI, TLC, BIKE, ISAFE) |
| detector / trav_id | text | Detector or traverse identifier |
| from_ts, to_ts | timestamp | Measurement time window |
| volume | double | Vehicle/bike count |
| flow_rate | double | Flow rate |
| speed | double | Average speed |
| occupancy | double | Occupancy percentage (vehicles only) |
| completeness | double | Data completeness indicator |

---

## 3. Webtools Database Structure

### Accessible traffic_* Tables

| Table | Rows | Purpose |
|-------|------|---------|
| traffic_uccomptagesystem | 4 | Sensor system lookup |
| traffic_uccomptagetype | 5 | Detector type lookup |
| traffic_uccomptagetypeb | 3 | Secondary type classification |
| traffic_uccomptagetimespan | 3 | Time interval lookup |
| traffic_uccomptagelink | 161 | Location/link definitions |
| traffic_uccomptagedetector | 308 | Detector configurations |
| traffic_uccomptagetraverses | 171 | Traverse definitions with direction/orientation |
| traffic_uccomptagedetectortraverse | 273 | Detector-to-traverse mapping |
| traffic_ucdetectorout | 197 | Detector outage records |

### Inaccessible Tables (permission denied)
- traffic_author
- traffic_countingloops
- traffic_counttime
- traffic_counttypes
- traffic_counttypesb
- traffic_punctualcounts
- traffic_source
- traffic_traverses

---

## 4. Source Data Values

### 4.1 Sensor Systems (traffic_uccomptagesystem)

| ID | Code | Detector Count |
|----|------|----------------|
| 56 | DAI | 118 |
| 57 | TLC | 46 |
| 59 | BIKE | 50 |
| 60 | ISAFE | 94 |

### 4.2 Detector Types (traffic_uccomptagetype)

| ID | Code |
|----|------|
| 75 | CAMERA |
| 76 | LOOP |
| 77 | XSTREAM |
| 78 | SL20007 v0 PUR SENSOR |
| 79 | Icom |

### 4.3 System to Detector Type Relationship

The `traffic_uccomptagesystem` and `traffic_uccomptagetype` tables are linked indirectly via `traffic_uccomptagedetector`, which has both `count_system_id` and `count_type_id` foreign keys.

| System | Detector Type | Detector Count |
|--------|---------------|----------------|
| BIKE | SL20007 v0 PUR SENSOR | 50 |
| DAI | CAMERA | 118 |
| ISAFE | Icom | 94 |
| TLC | LOOP | 44 |
| TLC | XSTREAM | 2 |

**Summary:** Each system uses specific detector types (1:1 relationship), except TLC which uses both LOOP and XSTREAM.

### 4.4 Vehicle Types (veh_type field)

The `veh_type` field exists in `traffic_uccomptagedetector` and `traffic_uccomptagetraverses` tables.

| Value | Traverse Count | Detector Count |
|-------|----------------|----------------|
| 1 | 89 | 164 |
| 2 | 33 | 50 |
| 3 | 49 | 94 |

#### veh_type to System Relationship

| veh_type | System(s) | Detector Type(s) |
|----------|-----------|------------------|
| 1 | DAI, TLC | CAMERA, LOOP, XSTREAM |
| 2 | BIKE | SL20007 v0 PUR SENSOR |
| 3 | ISAFE | Icom |

#### What Each veh_type Actually Measures

Analysis of the counts database shows which tables each system's detectors appear in:

| System | veh_type | Appears in counts table | Measures |
|--------|----------|------------------------|----------|
| DAI | 1 | `veh_detector` | vehicles |
| TLC | 1 | `veh_detector` | vehicles |
| BIKE | 2 | `bike_detector` | bicycles |
| ISAFE | 3 | `veh_detector` | vehicles |

**Conclusion:** Both veh_type=1 and veh_type=3 measure vehicles (they appear in `veh_detector` tables). The `veh_type` field represents a system grouping rather than a true vehicle type classification:
- veh_type=1: DAI/TLC systems (vehicles)
- veh_type=2: BIKE system (bicycles)
- veh_type=3: ISAFE system (vehicles)

### 4.5 Bike Subtypes (in bike_type tables)

| Type | Record Count (2024) |
|------|---------------------|
| 1.0 | 1,440,576 |
| 2.0 | 1,440,576 |
| 3.0 | 1,440,576 |
| 4.0 | 1,440,576 |
| 5.0 | 1,440,576 |

*Note: Meaning of bike subtypes 1-5 is undocumented.*

### 4.6 Direction Values (traffic_uccomptagetraverses)

| Field | Coverage | Values |
|-------|----------|--------|
| direction (text) | 3.5% (6/171) | inner_ring, outer_ring, out, Sortie_ville, Entree_ville |
| orientation (degrees) | 50.9% (87/171) | 10, 15, 20, 30, 45, 50, 60, 70, 80, 100, 105, 110, 115, 140, 145, 170, 190, 195, 200, 205, 230, 244, 250, 280, 285, 290, 295, 300, 305, 310, 320, 325, 350, 360, 370, 375 |

#### Orientation Coverage by Vehicle Type

| Vehicle Type | With Orientation | Without Orientation | Coverage |
|--------------|------------------|---------------------|----------|
| veh_type=1 (vehicles) | 87 | 2 | 98% |
| veh_type=2 (bikes) | 0 | 33 | 0% |
| veh_type=3 (ICOM/all) | 0 | 49 | 0% |

**Note:** Orientation (compass degrees) is only available for vehicle traverses (DAI/TLC). Bike traverses sometimes have the `direction` text field instead (e.g., `inner_ring`, `outer_ring`). ISAFE traverses have no direction data.

#### How Counts Link to Direction

**Structure:**
- A **traverse** = road section + direction (has orientation in degrees)
- A **detector** = single lane within a traverse (suffix `_1`, `_2` = lane number)
- Multiple detectors share the same traverse and therefore the same orientation

**Naming Convention:**
- Traverse ID format: `LOCATION_DIRECTION[_suffix]` (e.g., `ARL_103`, `ARL_203`)
- Detector ID format: `{traverse_id}_{lane_number}` (e.g., `ARL_103_1`, `ARL_103_2`)
- The direction code in traverse ID often indicates direction group (e.g., `1xx` vs `2xx` = opposite directions, 180° apart)

**Opposite Direction Pairs (examples):**

| Traverse A | Orientation A | Traverse B | Orientation B | Angle Diff |
|------------|---------------|------------|---------------|------------|
| ARL_103 | 200° | ARL_203 | 20° | 180° |
| BAI_TD1 | 325° | BAI_TD2 | 145° | 180° |
| CIN_TD1 | 290° | CIN_TD2 | 110° | 180° |
| HAL_191 | 290° | HAL_292 | 110° | 180° |
| LEO_117 | 115° | LEO_210 | 295° | 180° |

**Two Paths from Count to Direction:**

1. **Detector-level counts** (`_detector` tables):
   ```
   counts.detector (e.g., "ARL_103_1")
        ↓ join on detector_id
   webtools.traffic_uccomptagedetector
        ↓ via traffic_uccomptagedetectortraverse
   webtools.traffic_uccomptagetraverses
        → orientation (e.g., 200°)
   ```

2. **Traverse-level counts** (`_traverse` tables):
   ```
   counts.trav_id (e.g., "ARL_103")
        ↓ direct match on traverse_id
   webtools.traffic_uccomptagetraverses
        → orientation (e.g., 200°)
   ```

**Example Tracing:**

| Count Table | ID in counts | Traverse | Orientation |
|-------------|--------------|----------|-------------|
| _veh_detector | ARL_103_1 | ARL_103 | 200° |
| _veh_detector | ARL_103_2 | ARL_103 | 200° |
| _veh_detector | ARL_203_1 | ARL_203 | 20° |
| _veh_traverse | ARL_103 | ARL_103 | 200° |
| _veh_traverse | BAI_TD1 | BAI_TD1 | 325° |

### 4.7 Traffic Characteristics (Measurement Types)

There is no metadata table in the webtools database for traffic characteristics. The available measurements are defined as **columns in the counts database tables**.

#### Measurement Columns by Table Type

| Column | veh_detector | bike_detector | veh_traverse | bike_traverse | Description |
|--------|--------------|---------------|--------------|---------------|-------------|
| volume | ✓ | ✓ | ✓ | ✓ | Count of vehicles/bikes |
| speed | ✓ | ✓ | ✓ | ✓ | Average speed |
| flow_rate | ✓ | ✓ | ✓ | ✓ | Flow rate |
| occupancy | ✓ | — | ✓ | — | Occupancy percentage |
| completeness | ✓ | ✓ | — | — | Data completeness indicator |

**Note:** These column names are the source for mapping to VkmVerkeersKenmerkType. There is no codelist or lookup table in the source database defining these measurement types.

---

## 5. Target Codelists

### 5.1 VkmMeetInstrumentType (Sensor Type)

Source: https://github.com/Informatievlaanderen/OSLOthema-verkeersmetingen/blob/main/codelijsten/VkmMeetInstrumentType.ttl

| Code | Label (NL) | URI |
|------|------------|-----|
| radar | radar | `https://data.vlaanderen.be/id/concept/VkmMeetInstrumentType/radar` |
| rubberslang | rubberslang | `https://data.vlaanderen.be/id/concept/VkmMeetInstrumentType/rubberslang` |
| piezzo | piezzo | `https://data.vlaanderen.be/id/concept/VkmMeetInstrumentType/piezzo` |
| glasvezel | glasvezel | `https://data.vlaanderen.be/id/concept/VkmMeetInstrumentType/glasvezel` |
| inductielus | inductielus | `https://data.vlaanderen.be/id/concept/VkmMeetInstrumentType/inductielus` |
| standaard_Camera | standaard camera | `https://data.vlaanderen.be/id/concept/VkmMeetInstrumentType/standaard_Camera` |
| ANPR_camera | ANPR camera | `https://data.vlaanderen.be/id/concept/VkmMeetInstrumentType/ANPR_camera` |
| manuele_telling | manuele telling | `https://data.vlaanderen.be/id/concept/VkmMeetInstrumentType/manuele_telling` |
| telraam | telraam | `https://data.vlaanderen.be/id/concept/VkmMeetInstrumentType/telraam` |

### 5.2 VkmVoertuigType (Vehicle Type)

Source: https://github.com/Informatievlaanderen/OSLOthema-verkeersmetingen/blob/main/codelijsten/VkmVoertuigType.ttl

| Code | Label (NL) | Label (EN) | URI |
|------|------------|------------|-----|
| voetganger | voetganger | pedestrian | `https://data.vlaanderen.be/id/concept/VkmVoertuigType/voetganger` |
| fiets | fiets | bicycle | `https://data.vlaanderen.be/id/concept/VkmVoertuigType/fiets` |
| auto | auto | car | `https://data.vlaanderen.be/id/concept/VkmVoertuigType/auto` |
| vrachtwagen | gelede vrachtwagens | articulated trucks | `https://data.vlaanderen.be/id/concept/VkmVoertuigType/vrachtwagen` |

### 5.3 VkmVerkeersKenmerkType (Traffic Characteristic Type)

Source: https://github.com/Informatievlaanderen/OSLOthema-verkeersmetingen/blob/main/codelijsten/VkmVerkeersKenmerkType.ttl

| Code | Label (NL) | Description | URI |
|------|------------|-------------|-----|
| V85 | V85 | 85th percentile speed | `https://data.vlaanderen.be/id/concept/VkmVerkeersKenmerkType/V85` |
| tijdsgemiddelde_snelheid | tijdsgemiddeld | Time-averaged speed | `https://data.vlaanderen.be/id/concept/VkmVerkeersKenmerkType/tijdsgemiddelde_snelheid` |
| plaatsgemiddelde_snelheid | plaatsgemiddeld | Space-averaged speed | `https://data.vlaanderen.be/id/concept/VkmVerkeersKenmerkType/plaatsgemiddelde_snelheid` |
| mediaan_snelheid | mediaan | Median speed | `https://data.vlaanderen.be/id/concept/VkmVerkeersKenmerkType/mediaan_snelheid` |
| aantal | aantal | Count/volume | `https://data.vlaanderen.be/id/concept/VkmVerkeersKenmerkType/aantal` |

### 5.4 INSPIRE LinkDirectionValue (Measure Direction)

Source: https://inspire.ec.europa.eu/codelist/LinkDirectionValue

| Code | URI |
|------|-----|
| bothDirections | `http://inspire.ec.europa.eu/codelist/LinkDirectionValue/bothDirections` |
| inDirection | `http://inspire.ec.europa.eu/codelist/LinkDirectionValue/inDirection` |
| inOppositeDirection | `http://inspire.ec.europa.eu/codelist/LinkDirectionValue/inOppositeDirection` |

---

## 6. Codelist Mapping

### 6.1 Sensor Type Mapping

| Source System | Source Detector Type | Target Code | Target URI |
|---------------|---------------------|-------------|------------|
| BIKE | SL20007 v0 PUR SENSOR | glasvezel | `https://data.vlaanderen.be/id/concept/VkmMeetInstrumentType/glasvezel` |
| DAI | CAMERA | standaard_Camera | `https://data.vlaanderen.be/id/concept/VkmMeetInstrumentType/standaard_Camera` |
| ISAFE | Icom | radar | `https://data.vlaanderen.be/id/concept/VkmMeetInstrumentType/radar` |
| TLC | LOOP | inductielus | `https://data.vlaanderen.be/id/concept/VkmMeetInstrumentType/inductielus` |
| TLC | XSTREAM | standaard_Camera | `https://data.vlaanderen.be/id/concept/VkmMeetInstrumentType/standaard_Camera` |

### 6.2 Vehicle Type Mapping

| Source Value | System(s) | Measures | Target Code | Target URI |
|--------------|-----------|----------|-------------|------------|
| veh_type=1 | DAI, TLC | vehicles | auto | `https://data.vlaanderen.be/id/concept/VkmVoertuigType/auto` |
| veh_type=2 | BIKE | bicycles | fiets | `https://data.vlaanderen.be/id/concept/VkmVoertuigType/fiets` |
| veh_type=3 | ISAFE | vehicles | auto | `https://data.vlaanderen.be/id/concept/VkmVoertuigType/auto` |
| bike types 1-5 | BIKE | bicycles | fiets | `https://data.vlaanderen.be/id/concept/VkmVoertuigType/fiets` |

### 6.3 Traffic Characteristic Mapping

| Source Measurement | Target Code | Target URI | Status |
|--------------------|-------------|------------|--------|
| volume | aantal | `https://data.vlaanderen.be/id/concept/VkmVerkeersKenmerkType/aantal` | ✅ Match |
| speed | tijdsgemiddelde_snelheid | `https://data.vlaanderen.be/id/concept/VkmVerkeersKenmerkType/tijdsgemiddelde_snelheid` | ✅ Match |
| occupancy | — | — | ❌ No match in codelist |
| flow_rate | — | — | ❌ No match (derived from volume) |

### 6.4 Direction Mapping

The `orientation` field in `traffic_uccomptagetraverses` contains compass bearings (degrees). These need to be converted to INSPIRE LinkDirectionValue codes.

#### How to Get Direction from a Count

To determine the direction of a traffic count measurement:

**For detector-level counts:**
```sql
SELECT c.*, t.orientation, t.direction
FROM aggregations._15_veh_detector_2024 c
JOIN webtools.traffic_uccomptagedetector d ON d.detector_id = c.detector
JOIN webtools.traffic_uccomptagedetectortraverse dt ON dt.detector_id_id = d.id
JOIN webtools.traffic_uccomptagetraverses t ON dt.traverse_id_id = t.id
```

**For traverse-level counts:**
```sql
SELECT c.*, t.orientation, t.direction
FROM aggregations._15_veh_traverse_2024 c
JOIN webtools.traffic_uccomptagetraverses t ON t.traverse_id = c.trav_id
```

#### Conversion Logic Required

The orientation value represents the compass direction of traffic flow. To map to INSPIRE codes:

1. **Define reference direction**: Establish what "inDirection" means for each traverse (e.g., the digitized direction of the road segment)
2. **Compare orientation**:
   - If orientation aligns with reference → `inDirection`
   - If orientation is opposite (±180°) → `inOppositeDirection`
   - If both directions measured → `bothDirections`

#### Proposed Conversion Rules

```
# Conceptual mapping based on orientation ranges
# Assumes "inDirection" = towards city center / clockwise on ring

# For ring roads (Petite_ceinture, etc.):
#   Clockwise (inner_ring): 0-180° roughly
#   Counter-clockwise (outer_ring): 180-360° roughly

# For radial roads:
#   Towards center (Entree_ville): orientation pointing inward
#   Away from center (Sortie_ville): orientation pointing outward
```

#### Both-Direction Measurements (BIKE system)

The BIKE system has traverses that measure **both directions combined**:

| Traverse ID | Type | Example Volumes (2024-06-01 08:00) |
|-------------|------|-----------------------------------|
| CB1101 | Both directions | 18 bikes |
| CB1101_RING_INT | Inward direction | 6 bikes |
| CB1101_RING_EXT | Outward direction | 12 bikes |

**Verification:** `CB1101` volume = `CB1101_RING_INT` + `CB1101_RING_EXT` (exact match)

**Naming patterns for directional BIKE traverses:**
- `*_RING_INT` / `*_RING_EXT` (in counts database)
- `*_inner_ring` / `*_outer_ring` (in webtools metadata)

**Note:** Some BIKE locations only have the combined count (e.g., `CB1142` has no directional breakdown).

#### Single-Direction Measurements (DAI/TLC/ISAFE)

Vehicle systems only measure **one direction per traverse**:
- Opposite directions have separate traverse IDs (e.g., `ARL_103` at 200° vs `ARL_203` at 20°)
- No combined "both directions" traverse exists for vehicle counts

#### Direction Mapping Summary

| Source Pattern | System | Target INSPIRE Code |
|----------------|--------|---------------------|
| Base BIKE traverse (no suffix) | BIKE | `bothDirections` |
| `*_RING_INT`, `*_inner_ring` | BIKE | `inDirection` (towards center) |
| `*_RING_EXT`, `*_outer_ring` | BIKE | `inOppositeDirection` (away from center) |
| Traverse with orientation | DAI/TLC | Calculate from degrees vs reference |
| ISAFE traverse | ISAFE | Unknown (no direction data) |

---

*Document generated: 2026-05-13*
*Data sources: Brussels Mobility counts & webtools databases*
