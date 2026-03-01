BEGIN;

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS faa_airspace;

CREATE OR REPLACE FUNCTION faa_airspace.touch_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

CREATE TABLE IF NOT EXISTS faa_airspace.source_snapshot (
  snapshot_id BIGSERIAL PRIMARY KEY,
  authority TEXT NOT NULL DEFAULT 'FAA',
  dataset TEXT NOT NULL,
  dataset_version TEXT NOT NULL,
  airac_cycle TEXT,
  source_url TEXT,
  source_sha256 CHAR(64) NOT NULL,
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  effective_from DATE NOT NULL,
  effective_to DATE NOT NULL DEFAULT DATE '9999-12-31',
  is_delta BOOLEAN NOT NULL DEFAULT FALSE,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (effective_to >= effective_from)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_source_snapshot_identity
  ON faa_airspace.source_snapshot (
    authority,
    dataset,
    dataset_version,
    effective_from,
    effective_to,
    source_sha256
  );

CREATE INDEX IF NOT EXISTS idx_source_snapshot_effective_window
  ON faa_airspace.source_snapshot (effective_from, effective_to);

CREATE TABLE IF NOT EXISTS faa_airspace.airspace_feature (
  feature_pk BIGSERIAL PRIMARY KEY,
  system_id UUID NOT NULL DEFAULT gen_random_uuid(),
  authority TEXT NOT NULL DEFAULT 'FAA',
  published_id TEXT NOT NULL,
  feature_name TEXT,
  airspace_type TEXT NOT NULL,
  class_code TEXT,
  designator TEXT,
  controlling_agency TEXT,
  using_agency TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  valid_from DATE NOT NULL,
  valid_to DATE NOT NULL DEFAULT DATE '9999-12-31',
  source_snapshot_id BIGINT NOT NULL REFERENCES faa_airspace.source_snapshot(snapshot_id) ON DELETE RESTRICT,
  attrs JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (status IN ('active', 'inactive', 'deprecated')),
  CHECK (valid_to >= valid_from),
  UNIQUE (system_id),
  UNIQUE (authority, published_id, airspace_type, valid_from, source_snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_airspace_feature_lookup
  ON faa_airspace.airspace_feature (authority, published_id, airspace_type);

CREATE INDEX IF NOT EXISTS idx_airspace_feature_snapshot
  ON faa_airspace.airspace_feature (source_snapshot_id);

CREATE INDEX IF NOT EXISTS idx_airspace_feature_valid_window
  ON faa_airspace.airspace_feature (valid_from, valid_to);

DROP TRIGGER IF EXISTS trg_airspace_feature_touch_updated_at ON faa_airspace.airspace_feature;
CREATE TRIGGER trg_airspace_feature_touch_updated_at
  BEFORE UPDATE ON faa_airspace.airspace_feature
  FOR EACH ROW
  EXECUTE FUNCTION faa_airspace.touch_updated_at();

CREATE TABLE IF NOT EXISTS faa_airspace.airspace_volume (
  volume_pk BIGSERIAL PRIMARY KEY,
  feature_pk BIGINT NOT NULL REFERENCES faa_airspace.airspace_feature(feature_pk) ON DELETE CASCADE,
  volume_ordinal SMALLINT NOT NULL DEFAULT 1,
  lower_limit_value NUMERIC(9, 2),
  lower_limit_uom TEXT NOT NULL DEFAULT 'FT',
  lower_limit_ref TEXT NOT NULL DEFAULT 'MSL',
  upper_limit_value NUMERIC(9, 2),
  upper_limit_uom TEXT,
  upper_limit_ref TEXT,
  lateral_geom geometry(MULTIPOLYGON, 4326) NOT NULL,
  attrs JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (volume_ordinal > 0),
  CHECK (lower_limit_uom IN ('FT', 'M', 'FL', 'SFC')),
  CHECK (lower_limit_ref IN ('MSL', 'AGL', 'STD', 'SFC', 'UNL')),
  CHECK (upper_limit_uom IS NULL OR upper_limit_uom IN ('FT', 'M', 'FL', 'SFC')),
  CHECK (upper_limit_ref IS NULL OR upper_limit_ref IN ('MSL', 'AGL', 'STD', 'SFC', 'UNL')),
  CHECK (
    upper_limit_value IS NULL
    OR lower_limit_value IS NULL
    OR upper_limit_value >= lower_limit_value
  ),
  UNIQUE (feature_pk, volume_ordinal)
);

CREATE INDEX IF NOT EXISTS idx_airspace_volume_feature
  ON faa_airspace.airspace_volume (feature_pk);

CREATE INDEX IF NOT EXISTS idx_airspace_volume_geom
  ON faa_airspace.airspace_volume
  USING GIST (lateral_geom);

DROP TRIGGER IF EXISTS trg_airspace_volume_touch_updated_at ON faa_airspace.airspace_volume;
CREATE TRIGGER trg_airspace_volume_touch_updated_at
  BEFORE UPDATE ON faa_airspace.airspace_volume
  FOR EACH ROW
  EXECUTE FUNCTION faa_airspace.touch_updated_at();

CREATE TABLE IF NOT EXISTS faa_airspace.airspace_schedule (
  schedule_pk BIGSERIAL PRIMARY KEY,
  feature_pk BIGINT NOT NULL REFERENCES faa_airspace.airspace_feature(feature_pk) ON DELETE CASCADE,
  schedule_key TEXT NOT NULL,
  timezone_name TEXT NOT NULL DEFAULT 'UTC',
  active_from TIMESTAMPTZ,
  active_to TIMESTAMPTZ,
  recurrence_rule TEXT,
  notam_id TEXT,
  status TEXT NOT NULL DEFAULT 'scheduled',
  notes TEXT,
  attrs JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (active_to IS NULL OR active_from IS NULL OR active_to >= active_from),
  CHECK (status IN ('scheduled', 'active', 'cancelled', 'expired')),
  UNIQUE (feature_pk, schedule_key)
);

CREATE INDEX IF NOT EXISTS idx_airspace_schedule_feature
  ON faa_airspace.airspace_schedule (feature_pk);

CREATE INDEX IF NOT EXISTS idx_airspace_schedule_active_window
  ON faa_airspace.airspace_schedule (active_from, active_to);

DROP TRIGGER IF EXISTS trg_airspace_schedule_touch_updated_at ON faa_airspace.airspace_schedule;
CREATE TRIGGER trg_airspace_schedule_touch_updated_at
  BEFORE UPDATE ON faa_airspace.airspace_schedule
  FOR EACH ROW
  EXECUTE FUNCTION faa_airspace.touch_updated_at();

CREATE TABLE IF NOT EXISTS faa_airspace.airspace_change_log (
  change_pk BIGSERIAL PRIMARY KEY,
  feature_pk BIGINT REFERENCES faa_airspace.airspace_feature(feature_pk) ON DELETE SET NULL,
  source_snapshot_id BIGINT REFERENCES faa_airspace.source_snapshot(snapshot_id) ON DELETE SET NULL,
  change_type TEXT NOT NULL,
  changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  previous_record JSONB,
  new_record JSONB,
  diff JSONB,
  run_id UUID,
  CHECK (change_type IN (
    'insert',
    'update',
    'retire',
    'reactivate',
    'geometry_change',
    'schedule_change'
  ))
);

CREATE INDEX IF NOT EXISTS idx_airspace_change_log_feature_changed_at
  ON faa_airspace.airspace_change_log (feature_pk, changed_at DESC);

CREATE INDEX IF NOT EXISTS idx_airspace_change_log_snapshot
  ON faa_airspace.airspace_change_log (source_snapshot_id);

CREATE OR REPLACE VIEW faa_airspace.v_airspace_current AS
SELECT
  f.feature_pk,
  f.system_id,
  f.authority,
  f.published_id,
  f.feature_name,
  f.airspace_type,
  f.class_code,
  f.designator,
  f.controlling_agency,
  f.using_agency,
  f.status,
  f.valid_from,
  f.valid_to,
  f.source_snapshot_id,
  f.attrs,
  f.created_at,
  f.updated_at
FROM faa_airspace.airspace_feature f
WHERE f.status = 'active'
  AND f.valid_from <= CURRENT_DATE
  AND f.valid_to >= CURRENT_DATE;

COMMIT;
