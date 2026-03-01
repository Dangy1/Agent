BEGIN;

CREATE TABLE IF NOT EXISTS faa_airspace.ingestion_run (
  run_id UUID PRIMARY KEY,
  source_snapshot_id BIGINT NOT NULL REFERENCES faa_airspace.source_snapshot(snapshot_id) ON DELETE RESTRICT,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'running',
  message TEXT,
  metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
  CHECK (status IN ('running', 'succeeded', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_ingestion_run_snapshot_started
  ON faa_airspace.ingestion_run (source_snapshot_id, started_at DESC);

CREATE TABLE IF NOT EXISTS faa_airspace.stg_airspace_feature (
  stage_feature_pk BIGSERIAL PRIMARY KEY,
  load_batch_id UUID NOT NULL,
  source_snapshot_id BIGINT NOT NULL REFERENCES faa_airspace.source_snapshot(snapshot_id) ON DELETE CASCADE,
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
  attrs JSONB NOT NULL DEFAULT '{}'::jsonb,
  row_num INTEGER,
  CHECK (status IN ('active', 'inactive', 'deprecated')),
  CHECK (valid_to >= valid_from),
  UNIQUE (load_batch_id, authority, published_id, airspace_type, valid_from, source_snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_stg_feature_batch
  ON faa_airspace.stg_airspace_feature (load_batch_id);

CREATE TABLE IF NOT EXISTS faa_airspace.stg_airspace_volume (
  stage_volume_pk BIGSERIAL PRIMARY KEY,
  load_batch_id UUID NOT NULL,
  source_snapshot_id BIGINT NOT NULL REFERENCES faa_airspace.source_snapshot(snapshot_id) ON DELETE CASCADE,
  authority TEXT NOT NULL DEFAULT 'FAA',
  published_id TEXT NOT NULL,
  airspace_type TEXT NOT NULL,
  valid_from DATE NOT NULL,
  volume_ordinal SMALLINT NOT NULL DEFAULT 1,
  lower_limit_value NUMERIC(9, 2),
  lower_limit_uom TEXT NOT NULL DEFAULT 'FT',
  lower_limit_ref TEXT NOT NULL DEFAULT 'MSL',
  upper_limit_value NUMERIC(9, 2),
  upper_limit_uom TEXT,
  upper_limit_ref TEXT,
  geom_geojson TEXT NOT NULL,
  attrs JSONB NOT NULL DEFAULT '{}'::jsonb,
  row_num INTEGER,
  CHECK (volume_ordinal > 0),
  CHECK (lower_limit_uom IN ('FT', 'M', 'FL', 'SFC')),
  CHECK (lower_limit_ref IN ('MSL', 'AGL', 'STD', 'SFC', 'UNL')),
  CHECK (upper_limit_uom IS NULL OR upper_limit_uom IN ('FT', 'M', 'FL', 'SFC')),
  CHECK (upper_limit_ref IS NULL OR upper_limit_ref IN ('MSL', 'AGL', 'STD', 'SFC', 'UNL')),
  UNIQUE (
    load_batch_id,
    authority,
    published_id,
    airspace_type,
    valid_from,
    source_snapshot_id,
    volume_ordinal
  )
);

CREATE INDEX IF NOT EXISTS idx_stg_volume_batch
  ON faa_airspace.stg_airspace_volume (load_batch_id);

CREATE TABLE IF NOT EXISTS faa_airspace.stg_airspace_schedule (
  stage_schedule_pk BIGSERIAL PRIMARY KEY,
  load_batch_id UUID NOT NULL,
  source_snapshot_id BIGINT NOT NULL REFERENCES faa_airspace.source_snapshot(snapshot_id) ON DELETE CASCADE,
  authority TEXT NOT NULL DEFAULT 'FAA',
  published_id TEXT NOT NULL,
  airspace_type TEXT NOT NULL,
  valid_from DATE NOT NULL,
  schedule_key TEXT NOT NULL,
  timezone_name TEXT NOT NULL DEFAULT 'UTC',
  active_from TIMESTAMPTZ,
  active_to TIMESTAMPTZ,
  recurrence_rule TEXT,
  notam_id TEXT,
  status TEXT NOT NULL DEFAULT 'scheduled',
  notes TEXT,
  attrs JSONB NOT NULL DEFAULT '{}'::jsonb,
  row_num INTEGER,
  CHECK (status IN ('scheduled', 'active', 'cancelled', 'expired')),
  CHECK (active_to IS NULL OR active_from IS NULL OR active_to >= active_from),
  UNIQUE (
    load_batch_id,
    authority,
    published_id,
    airspace_type,
    valid_from,
    source_snapshot_id,
    schedule_key
  )
);

CREATE INDEX IF NOT EXISTS idx_stg_schedule_batch
  ON faa_airspace.stg_airspace_schedule (load_batch_id);

CREATE OR REPLACE FUNCTION faa_airspace.merge_stage(
  p_load_batch_id UUID,
  p_run_id UUID DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
  v_inserted_features INTEGER := 0;
  v_updated_features INTEGER := 0;
  v_upserted_volumes INTEGER := 0;
  v_upserted_schedules INTEGER := 0;
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM faa_airspace.stg_airspace_feature sf
    WHERE sf.load_batch_id = p_load_batch_id
  ) THEN
    RAISE EXCEPTION 'No staged features found for load_batch_id=%', p_load_batch_id;
  END IF;

  DROP TABLE IF EXISTS pg_temp.tmp_upserted_features;

  CREATE TEMP TABLE tmp_upserted_features ON COMMIT DROP AS
  WITH upserted AS (
    INSERT INTO faa_airspace.airspace_feature (
      authority,
      published_id,
      feature_name,
      airspace_type,
      class_code,
      designator,
      controlling_agency,
      using_agency,
      status,
      valid_from,
      valid_to,
      source_snapshot_id,
      attrs
    )
    SELECT
      sf.authority,
      sf.published_id,
      sf.feature_name,
      sf.airspace_type,
      sf.class_code,
      sf.designator,
      sf.controlling_agency,
      sf.using_agency,
      sf.status,
      sf.valid_from,
      sf.valid_to,
      sf.source_snapshot_id,
      sf.attrs
    FROM faa_airspace.stg_airspace_feature sf
    WHERE sf.load_batch_id = p_load_batch_id
    ON CONFLICT (authority, published_id, airspace_type, valid_from, source_snapshot_id)
    DO UPDATE
      SET feature_name = EXCLUDED.feature_name,
          class_code = EXCLUDED.class_code,
          designator = EXCLUDED.designator,
          controlling_agency = EXCLUDED.controlling_agency,
          using_agency = EXCLUDED.using_agency,
          status = EXCLUDED.status,
          valid_to = EXCLUDED.valid_to,
          attrs = EXCLUDED.attrs,
          updated_at = now()
    RETURNING feature_pk, source_snapshot_id, (xmax = 0) AS inserted
  )
  SELECT
    feature_pk,
    source_snapshot_id,
    inserted
  FROM upserted;

  SELECT
    COUNT(*) FILTER (WHERE inserted),
    COUNT(*) FILTER (WHERE NOT inserted)
  INTO v_inserted_features, v_updated_features
  FROM tmp_upserted_features;

  INSERT INTO faa_airspace.airspace_change_log (
    feature_pk,
    source_snapshot_id,
    change_type,
    new_record,
    run_id
  )
  SELECT
    tuf.feature_pk,
    tuf.source_snapshot_id,
    CASE WHEN tuf.inserted THEN 'insert' ELSE 'update' END,
    to_jsonb(af.*),
    p_run_id
  FROM tmp_upserted_features tuf
  JOIN faa_airspace.airspace_feature af
    ON af.feature_pk = tuf.feature_pk;

  WITH upserted_volume AS (
    INSERT INTO faa_airspace.airspace_volume (
      feature_pk,
      volume_ordinal,
      lower_limit_value,
      lower_limit_uom,
      lower_limit_ref,
      upper_limit_value,
      upper_limit_uom,
      upper_limit_ref,
      lateral_geom,
      attrs
    )
    SELECT
      af.feature_pk,
      sv.volume_ordinal,
      sv.lower_limit_value,
      sv.lower_limit_uom,
      sv.lower_limit_ref,
      sv.upper_limit_value,
      sv.upper_limit_uom,
      sv.upper_limit_ref,
      geom.g,
      sv.attrs
    FROM faa_airspace.stg_airspace_volume sv
    JOIN faa_airspace.airspace_feature af
      ON af.source_snapshot_id = sv.source_snapshot_id
     AND af.authority = sv.authority
     AND af.published_id = sv.published_id
     AND af.airspace_type = sv.airspace_type
     AND af.valid_from = sv.valid_from
    CROSS JOIN LATERAL (
      SELECT ST_Multi(
               ST_CollectionExtract(
                 ST_MakeValid(
                   ST_SetSRID(ST_GeomFromGeoJSON(sv.geom_geojson), 4326)
                 ),
                 3
               )
             )::geometry(MULTIPOLYGON, 4326) AS g
    ) AS geom
    WHERE sv.load_batch_id = p_load_batch_id
      AND NOT ST_IsEmpty(geom.g)
    ON CONFLICT (feature_pk, volume_ordinal)
    DO UPDATE
      SET lower_limit_value = EXCLUDED.lower_limit_value,
          lower_limit_uom = EXCLUDED.lower_limit_uom,
          lower_limit_ref = EXCLUDED.lower_limit_ref,
          upper_limit_value = EXCLUDED.upper_limit_value,
          upper_limit_uom = EXCLUDED.upper_limit_uom,
          upper_limit_ref = EXCLUDED.upper_limit_ref,
          lateral_geom = EXCLUDED.lateral_geom,
          attrs = EXCLUDED.attrs,
          updated_at = now()
    RETURNING volume_pk
  )
  SELECT COUNT(*) INTO v_upserted_volumes
  FROM upserted_volume;

  WITH upserted_schedule AS (
    INSERT INTO faa_airspace.airspace_schedule (
      feature_pk,
      schedule_key,
      timezone_name,
      active_from,
      active_to,
      recurrence_rule,
      notam_id,
      status,
      notes,
      attrs
    )
    SELECT
      af.feature_pk,
      ss.schedule_key,
      ss.timezone_name,
      ss.active_from,
      ss.active_to,
      ss.recurrence_rule,
      ss.notam_id,
      ss.status,
      ss.notes,
      ss.attrs
    FROM faa_airspace.stg_airspace_schedule ss
    JOIN faa_airspace.airspace_feature af
      ON af.source_snapshot_id = ss.source_snapshot_id
     AND af.authority = ss.authority
     AND af.published_id = ss.published_id
     AND af.airspace_type = ss.airspace_type
     AND af.valid_from = ss.valid_from
    WHERE ss.load_batch_id = p_load_batch_id
    ON CONFLICT (feature_pk, schedule_key)
    DO UPDATE
      SET timezone_name = EXCLUDED.timezone_name,
          active_from = EXCLUDED.active_from,
          active_to = EXCLUDED.active_to,
          recurrence_rule = EXCLUDED.recurrence_rule,
          notam_id = EXCLUDED.notam_id,
          status = EXCLUDED.status,
          notes = EXCLUDED.notes,
          attrs = EXCLUDED.attrs,
          updated_at = now()
    RETURNING schedule_pk
  )
  SELECT COUNT(*) INTO v_upserted_schedules
  FROM upserted_schedule;

  IF p_run_id IS NOT NULL THEN
    UPDATE faa_airspace.ingestion_run ir
       SET finished_at = now(),
           status = 'succeeded',
           metrics = jsonb_build_object(
             'inserted_features', v_inserted_features,
             'updated_features', v_updated_features,
             'upserted_volumes', v_upserted_volumes,
             'upserted_schedules', v_upserted_schedules
           )
     WHERE ir.run_id = p_run_id;
  END IF;

  RETURN jsonb_build_object(
    'inserted_features', v_inserted_features,
    'updated_features', v_updated_features,
    'upserted_volumes', v_upserted_volumes,
    'upserted_schedules', v_upserted_schedules
  );
END;
$$;

CREATE OR REPLACE FUNCTION faa_airspace.purge_staging_batch(p_load_batch_id UUID)
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
  DELETE FROM faa_airspace.stg_airspace_schedule WHERE load_batch_id = p_load_batch_id;
  DELETE FROM faa_airspace.stg_airspace_volume WHERE load_batch_id = p_load_batch_id;
  DELETE FROM faa_airspace.stg_airspace_feature WHERE load_batch_id = p_load_batch_id;
END;
$$;

COMMIT;
