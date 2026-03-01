# FAA Airspace Postgres/PostGIS Schema + Ingestion Pipeline

This package provides:

1. A concrete PostgreSQL/PostGIS schema for FAA airspace entities.
2. A staging + merge pipeline for deterministic cycle ingestion.
3. A CLI ingester that loads normalized FAA files and upserts curated tables.
4. A Docker Compose setup using the official PostGIS runtime image.
5. A source-level PostGIS build workflow pinned to a Git submodule.

## Files

- `sql/001_airspace_core.sql`: core schema and operational tables.
- `sql/002_ingest_pipeline.sql`: ingestion run audit, staging tables, merge/purge functions.
- `ingest_faa_airspace.py`: ingestion CLI.
- `extract_nasr_to_normalized.py`: raw NASR ZIP/dir extractor to normalized files.
- `templates/features_template.csv`: starter feature CSV header.
- `templates/schedules_template.csv`: starter schedule CSV header.
- `docker-compose.postgis.yml`: local PostGIS service.
- `docker-compose.postgis-source.yml`: source-level local PostGIS service.
- `Dockerfile.postgis-source`: build PostGIS from source against PostgreSQL 16.
- `.env.example`: env template for local PostGIS + DSN.
- `scripts/bootstrap_postgis.sh`: bootstrap helper.
- `scripts/bootstrap_postgis_source.sh`: build + bootstrap source-level PostGIS.
- `scripts/apply_schema_via_container.sh`: apply SQL via `psql` in running container.
- `samples/`: normalized FAA sample inputs (`features.csv`, `volumes.geojson`, `schedules.csv`).
- `Makefile`: shortcuts for compose lifecycle.
- `vendor/postgis` (submodule): pinned upstream PostGIS source.

## PostGIS Upstream Integration Scope

This project supports two integration modes:

- use the official PostGIS runtime image (`postgis/postgis`) to run trusted upstream code;
- build PostGIS from pinned upstream source (`vendor/postgis`) in `Dockerfile.postgis-source`.

Both modes use the same project-owned schema and ingestion code.

## 0) Use Existing `conda` `langchain` Env (Merged)

If you already use a `langchain` conda env, install FAA pipeline deps into that env:

```bash
conda run -n langchain python -m pip install -r backend/requirements.txt
```

Or activate first:

```bash
conda activate langchain
python -m pip install -r backend/requirements.txt
```

Notes:

- `pyshp` 3.x is supported by this extractor (`backend/requirements.txt` allows `<4.0`).
- `psycopg` is required for `ingest_faa_airspace.py`.

## 1) Start local PostGIS quickly

```bash
cd backend/airspace_faa
cp .env.example .env
./scripts/bootstrap_postgis.sh
```

Or with Makefile:

```bash
cd backend/airspace_faa
cp .env.example .env
make up
```

The first container initialization automatically applies all `.sql` files in `sql/`.

## 2) Source-level PostGIS workflow (pinned submodule)

From repo root:

```bash
git submodule add --depth 1 https://github.com/postgis/postgis.git backend/airspace_faa/vendor/postgis
git -C backend/airspace_faa/vendor/postgis rev-parse --short HEAD
```

From `backend/airspace_faa`:

```bash
cp .env.example .env
./scripts/bootstrap_postgis_source.sh
```

This builds `Dockerfile.postgis-source` and starts the `postgis-source` service on `${POSTGRES_SOURCE_PORT}` (default `5433`).

## 3) Apply DB schema manually in order via psql

Using host `psql`:

```bash
psql "$FAA_AIRSPACE_DSN" -f backend/airspace_faa/sql/001_airspace_core.sql
psql "$FAA_AIRSPACE_DSN" -f backend/airspace_faa/sql/002_ingest_pipeline.sql
```

Or via container (works even when host `psql` is missing):

```bash
cd backend/airspace_faa
./scripts/apply_schema_via_container.sh
```

Required DB capabilities:

- PostgreSQL 14+ (recommended)
- PostGIS extension enabled
- Permissions to create schema/tables/functions
- `pyshp` Python package (installed via `backend/requirements.txt`) for NASR shapefile extraction

## 4) Input contract

### Features CSV (required)

Required columns:

- `published_id`
- `airspace_type`
- `valid_from` (`YYYY-MM-DD`)

Optional columns:

- `authority` (defaults to `FAA`)
- `feature_name`
- `class_code`
- `designator`
- `controlling_agency`
- `using_agency`
- `status` (`active|inactive|deprecated`)
- `valid_to` (`YYYY-MM-DD`, default `9999-12-31`)
- `attrs_json` (JSON object string)

Unknown extra columns are preserved into `attrs`.

### Volumes GeoJSON (required)

GeoJSON `FeatureCollection`, where each feature has:

- `geometry`: Polygon or MultiPolygon (WGS84 lon/lat)
- `properties.published_id` (required)
- `properties.airspace_type` (required)
- `properties.valid_from` (`YYYY-MM-DD`, required)

Recommended optional properties:

- `authority` (default `FAA`)
- `volume_ordinal` (default `1`)
- `lower_limit_value`, `lower_limit_uom`, `lower_limit_ref`
- `upper_limit_value`, `upper_limit_uom`, `upper_limit_ref`
- `attrs_json` (JSON object string)

Unknown extra properties are preserved into `attrs`.

### Schedules CSV (optional)

Required columns:

- `published_id`
- `airspace_type`
- `valid_from`
- `schedule_key`

Optional:

- `authority` (default `FAA`)
- `timezone_name` (default `UTC`)
- `active_from` / `active_to` (ISO-8601 timestamp)
- `recurrence_rule` (RRULE string)
- `notam_id`
- `status` (`scheduled|active|cancelled|expired`)
- `notes`
- `attrs_json`

## 5) Run ingestion

```bash
python backend/airspace_faa/ingest_faa_airspace.py \
  --dsn "$FAA_AIRSPACE_DSN" \
  --dataset NASR \
  --dataset-version 2025-11-27 \
  --airac-cycle 2025-11-27 \
  --effective-from 2025-11-27 \
  --effective-to 2025-12-24 \
  --source-url "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/2025-11-27/" \
  --features-csv /data/faa/features.csv \
  --volumes-geojson /data/faa/volumes.geojson \
  --schedules-csv /data/faa/schedules.csv
```

The command prints JSON with:

- `run_id`
- `source_snapshot_id`
- `load_batch_id`
- staged row counts
- merged row counts

Sample normalized files are included:

- `samples/features.csv`
- `samples/volumes.geojson`
- `samples/schedules.csv`

Sample ingestion command:

```bash
cd backend/airspace_faa
python ingest_faa_airspace.py \
  --dsn "$FAA_AIRSPACE_DSN" \
  --dataset NASR \
  --dataset-version 2025-11-27 \
  --airac-cycle 2025-11-27 \
  --effective-from 2025-11-27 \
  --effective-to 2025-12-24 \
  --source-url "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/2025-11-27/" \
  --features-csv samples/features.csv \
  --volumes-geojson samples/volumes.geojson \
  --schedules-csv samples/schedules.csv
```

## 6) NASR Raw Extractor (Automatic Normalization)

Extract from an FAA NASR ZIP URL:

```bash
cd backend/airspace_faa
python extract_nasr_to_normalized.py \
  --nasr-zip-url "https://.../NASR_YYYYMMDD.zip" \
  --effective-from 2025-11-27 \
  --effective-to 2025-12-24 \
  --work-dir ./work/nasr \
  --output-dir ./work/normalized
```

Extract from a local NASR ZIP:

```bash
cd backend/airspace_faa
python extract_nasr_to_normalized.py \
  --nasr-zip-path /data/faa/NASR_20251127.zip \
  --effective-from 2025-11-27 \
  --effective-to 2025-12-24 \
  --work-dir ./work/nasr \
  --output-dir ./work/normalized
```

Outputs:

- `work/normalized/features.csv`
- `work/normalized/volumes.geojson`
- `work/normalized/schedules.csv` (header-only if no schedule fields are inferred)
- `work/normalized/extract_metadata.json`

Then ingest:

```bash
cd backend/airspace_faa
python ingest_faa_airspace.py \
  --dsn "$FAA_AIRSPACE_DSN" \
  --dataset NASR \
  --dataset-version 2025-11-27 \
  --airac-cycle 2025-11-27 \
  --effective-from 2025-11-27 \
  --effective-to 2025-12-24 \
  --source-url "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/2025-11-27/" \
  --features-csv work/normalized/features.csv \
  --volumes-geojson work/normalized/volumes.geojson \
  --schedules-csv work/normalized/schedules.csv
```

Makefile shortcuts:

```bash
cd backend/airspace_faa
make extract-nasr-url NASR_ZIP_URL="https://.../NASR_YYYYMMDD.zip" NASR_EFFECTIVE_FROM=2025-11-27 NASR_EFFECTIVE_TO=2025-12-24
make ingest-extracted NASR_DATASET_VERSION=2025-11-27 NASR_AIRAC_CYCLE=2025-11-27 NASR_EFFECTIVE_FROM=2025-11-27 NASR_EFFECTIVE_TO=2025-12-24 NASR_SOURCE_URL="https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/2025-11-27/"
```

Notes:

- The extractor auto-discovers `.shp` files and filters by name keywords (`sua`, `airspace`, `special`, etc.).
- Field mapping is heuristic because NASR field names vary by layer/cycle.
- Non-polygon records are skipped by design.

## 7) Query examples

Current effective airspace:

```sql
SELECT published_id, airspace_type, feature_name, valid_from, valid_to
FROM faa_airspace.v_airspace_current
ORDER BY published_id;
```

Find geometries intersecting a point:

```sql
SELECT f.published_id, f.airspace_type, v.volume_ordinal
FROM faa_airspace.airspace_feature f
JOIN faa_airspace.airspace_volume v ON v.feature_pk = f.feature_pk
WHERE ST_Intersects(
  v.lateral_geom,
  ST_SetSRID(ST_MakePoint(-77.0379, 38.8521), 4326)
)
  AND f.valid_from <= CURRENT_DATE
  AND f.valid_to >= CURRENT_DATE
  AND f.status = 'active';
```

## 8) FAA-specific mapping notes

- Use FAA published identifiers directly as `published_id` (`R-####`, `P-##`, `W-###`, named MOA, etc.).
- Use `dataset` as `NASR` for baseline cycle loads; use `is_delta=true` for NOTAM-driven overlays.
- Keep each cycle as a distinct `source_snapshot` and do not overwrite historical records.
