# FAA Airspace Pipeline Work Log

Date: 2026-03-01

## Completed So Far

1. Created FAA/PostGIS airspace ingestion project assets.
2. Added source-level PostGIS workflow using `postgis/postgis` as a pinned submodule.
3. Built and ran a live PostgreSQL/PostGIS container from source (`faa-postgis-source` on port `5433`).
4. Applied SQL schema/migration files in order via `psql`.
5. Implemented NASR raw extractor (`extract_nasr_to_normalized.py`) for ZIP/URL/dir inputs.
6. Downloaded latest FAA NASR cycle ZIP and ran full extraction + ingestion.

## Latest NASR Download

- Source URL:
  - `https://nfdc.faa.gov/webContent/28DaySub/28DaySubscription_Effective_2026-03-19.zip`
- Saved file:
  - `/home/dang/agent_test/backend/airspace_faa/work/raw/28DaySubscription_Effective_2026-03-19.zip`
- Size:
  - `242 MB`
- SHA256:
  - `de51fef44927a3d445c92fbf1e7234e1229aec7e4bac22ea3d2958ecc4a964f6`

## Extraction Run (Latest)

- Command path:
  - `make extract-nasr-zip ...`
- Effective window used:
  - `2026-03-19` to `2026-04-15`
- Selected shapefile:
  - `Additional_Data/Shape_Files/Class_Airspace.shp`
- Stats:
  - `records_read=5610`
  - `records_kept=5610`
  - `features_written=3828`
  - `volumes_written=5610`
  - `schedules_written=0`
- Output files:
  - `/home/dang/agent_test/backend/airspace_faa/work/normalized/features.csv`
  - `/home/dang/agent_test/backend/airspace_faa/work/normalized/volumes.geojson`
  - `/home/dang/agent_test/backend/airspace_faa/work/normalized/schedules.csv`
  - `/home/dang/agent_test/backend/airspace_faa/work/normalized/extract_metadata.json`

## Ingestion Run (Latest)

- Command path:
  - `make ingest-extracted ...`
- Dataset metadata:
  - `dataset=NASR`
  - `dataset_version=2026-03-19`
  - `airac_cycle=2026-03-19`
- Run IDs:
  - `run_id=5aaf5392-0bd8-49f7-9c34-fa2b0f6ff1e7`
  - `load_batch_id=2f9040c4-4e89-45fd-9bcc-c89c0f5a22e8`
  - `source_snapshot_id=5`
- Merge result:
  - `inserted_features=3828`
  - `updated_features=0`
  - `upserted_volumes=5610`
  - `upserted_schedules=0`

## Post-Load Verification

- Verified in live PostGIS:
  - snapshot `5` feature count = `3828`
  - snapshot `5` volume count = `5610`

## Issues Found and Fixed

1. Makefile env sourcing under `/bin/sh` failed (`.env: not found`).
   - Fix: changed `. $(ENV_FILE)` to `. ./$(ENV_FILE)` in:
     - `backend/airspace_faa/Makefile` (`ingest-sample-source`, `ingest-extracted`).

2. Duplicate staging key on real FAA data due repeated `volume_ordinal`.
   - Fix: extractor now enforces unique positive ordinals per normalized feature key:
     - `backend/airspace_faa/extract_nasr_to_normalized.py`.

3. DB check constraint failure from FAA sentinel/invalid altitude values (example: `-9998` ceiling).
   - Fix: extractor now sanitizes extreme negative sentinel altitudes and drops invalid ceilings where `upper < lower`:
     - `backend/airspace_faa/extract_nasr_to_normalized.py`.

## Key Files Added/Updated

- `backend/airspace_faa/README.md`
- `backend/airspace_faa/Makefile`
- `backend/airspace_faa/extract_nasr_to_normalized.py`
- `backend/airspace_faa/ingest_faa_airspace.py`
- `backend/airspace_faa/sql/001_airspace_core.sql`
- `backend/airspace_faa/sql/002_ingest_pipeline.sql`
- `backend/airspace_faa/docker-compose.postgis.yml`
- `backend/airspace_faa/docker-compose.postgis-source.yml`
- `backend/airspace_faa/Dockerfile.postgis-source`
- `backend/airspace_faa/scripts/bootstrap_postgis_source.sh`
- `backend/airspace_faa/scripts/apply_schema_via_container.sh`
- `backend/airspace_faa/templates/features_template.csv`
- `backend/airspace_faa/templates/schedules_template.csv`
- `backend/airspace_faa/work/raw/28DaySubscription_Effective_2026-03-19.zip`
- `backend/airspace_faa/work/normalized/*`

## Conda `langchain` Env Merge (2026-03-01)

1. Verified existing conda env:
   - `langchain` at `/home/dang/anaconda3/envs/langchain`
2. Compatibility check:
   - Existing env already satisfied `fastapi`, `uvicorn`, `pydantic`, `httpx`.
   - Existing env had `pyshp==3.0.3`.
3. Requirement alignment:
   - Updated `backend/requirements.txt`:
     - `pyshp>=2.3,<3.0` -> `pyshp>=2.3,<4.0`
4. Installed missing deps into the same env:
   - `pytest==8.4.2`
   - `psycopg==3.3.3`
   - `psycopg-binary==3.3.3`
5. Runtime validation in `langchain` env:
   - `extract_nasr_to_normalized.py --help` OK
   - `ingest_faa_airspace.py --help` OK
   - Full extractor run against latest NASR extracted directory succeeded (`3828` features, `5610` volumes).
