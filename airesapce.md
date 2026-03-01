# Real-World Airspace ID and Database Construction

## Quick Summary

To model real-world airspace correctly, use:

1. A legal/public identifier from the authority publication (`published_id`).
2. A separate immutable internal identifier (`system_id`, typically UUID).
3. Time-versioning by validity window and cycle (`valid_from`, `valid_to`, AIRAC/effective cycle).

This keeps regulatory identity stable while allowing safe technical change tracking over time.

## Practical ID Construction Standard

For each airspace feature:

1. `published_id`: the official designation from the source publication.
2. `system_id`: immutable UUID used only by internal systems.
3. `version_key`: source cycle + effective period.

Common FAA-style examples:

- Prohibited area: `P-47`
- Restricted area: `R-2309`
- Warning area: `W-291`
- MOA: named pattern, e.g., `Dome MOA`

## Database Design Standard (Recommended)

### 1) `airspace_feature`

Core legal/business identity and metadata.

- `system_id` (UUID, PK, immutable)
- `published_id` (text, from authority)
- `type` (CTR/TMA/R/P/W/MOA/etc.)
- `name`
- `authority`
- `status`
- `valid_from`
- `valid_to`

### 2) `airspace_volume`

Geometric + vertical definition.

- `feature_system_id` (FK -> `airspace_feature.system_id`)
- `geometry` (PostGIS polygon/multipolygon)
- `lower_limit_value`, `lower_limit_ref` (MSL/AGL/FL)
- `upper_limit_value`, `upper_limit_ref` (MSL/AGL/FL)

### 3) `airspace_activity`

Operational activation behavior.

- `feature_system_id`
- schedule windows
- activation state
- controlling/using agency
- NOTAM linkage (if available)

### 4) `airspace_source_snapshot`

Reproducibility and audit trail.

- source URL
- source hash/checksum
- AIRAC/cycle identifier
- ingestion timestamp

### 5) `airspace_change_log`

Change history for replay and auditing.

- previous values / delta
- changed fields
- change timestamp
- source snapshot reference

## Authoritative Standards and Data Foundations

1. ICAO AIM / Annex 15 process and AIRAC cycle cadence.
2. AIXM feature model for airspace structure and exchange fields.
3. AIXM feature identification guidance for UUID-style identity management.
4. National/state legal publications (example: FAA JO 7400.11 series in U.S.).
5. Operational data feeds with 28-day cycles (NASR, CIFP in U.S.).
6. Regional AIS databases (example: EUROCONTROL EAD in Europe).

## Source Links

### ICAO / AIXM Standards

- ICAO AIM / AIRAC:
  - https://www.icao.int/airnavigation/aeronautical-information-management
- AIXM 5.1.1 Airspace class:
  - https://aixm.aero/sites/default/files/imce/AIXM511HTML/AIXM/Class_Airspace.html
- AIXM Feature Identification (UUID guidance):
  - https://aixm.aero/sites/default/files/imce/AIXM51/aixm_feature_identification_and_reference-1.0.pdf

### U.S. Regulatory + Data Sources (FAA)

- FAA Part 71 update reference to JO 7400.11K (published Aug 28, 2025):
  - https://public-inspection.federalregister.gov/2025-16493.pdf
- FAA NASR subscription cycles:
  - https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/2025-11-27/
- FAA CIFP digital products:
  - https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/cifp/

### FAA Naming/Designation Pattern References

- Prohibited areas:
  - https://www.faa.gov/air_traffic/publications/atpubs/pham_html/chap22_section_1.html
- Restricted areas:
  - https://www.faa.gov/air_traffic/publications/atpubs/pham_html/chap23_section_1.html
- Warning areas:
  - https://www.faa.gov/air_traffic/publications/atpubs/pham_html/chap24_section_1.html
- MOAs:
  - https://www.faa.gov/air_traffic/publications/atpubs/pham_html/chap25_section_1.html

### Europe

- EUROCONTROL EAD:
  - https://www.eurocontrol.int/service/european-ais-database

## Implementation Notes

- Keep `published_id` mutable only when the regulator changes it; never overwrite history.
- Keep `system_id` immutable forever.
- Ingest by cycle and never "flatten" historical states into one record.
- Use PostGIS for geometry and spatial query support.
