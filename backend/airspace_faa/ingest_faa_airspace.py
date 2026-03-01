#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import psycopg
    from psycopg import Cursor
    from psycopg.types.json import Jsonb
except ModuleNotFoundError:
    psycopg = None  # type: ignore[assignment]
    Cursor = Any  # type: ignore[misc,assignment]
    Jsonb = None  # type: ignore[assignment]

OPEN_ENDED_DATE = date(9999, 12, 31)

FEATURE_REQUIRED_COLUMNS = {"published_id", "airspace_type", "valid_from"}
FEATURE_KNOWN_COLUMNS = {
    "authority",
    "published_id",
    "feature_name",
    "airspace_type",
    "class_code",
    "designator",
    "controlling_agency",
    "using_agency",
    "status",
    "valid_from",
    "valid_to",
    "attrs_json",
}

VOLUME_KNOWN_PROPERTIES = {
    "authority",
    "published_id",
    "airspace_type",
    "valid_from",
    "volume_ordinal",
    "lower_limit_value",
    "lower_limit_uom",
    "lower_limit_ref",
    "upper_limit_value",
    "upper_limit_uom",
    "upper_limit_ref",
    "attrs_json",
}

SCHEDULE_REQUIRED_COLUMNS = {"published_id", "airspace_type", "valid_from", "schedule_key"}
SCHEDULE_KNOWN_COLUMNS = {
    "authority",
    "published_id",
    "airspace_type",
    "valid_from",
    "schedule_key",
    "timezone_name",
    "active_from",
    "active_to",
    "recurrence_rule",
    "notam_id",
    "status",
    "notes",
    "attrs_json",
}


class IngestionError(RuntimeError):
    pass


@dataclass
class StageCounts:
    features: int = 0
    volumes: int = 0
    schedules: int = 0


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _parse_date(raw: str, *, field: str, row_num: int, file_name: str, allow_empty: bool = False) -> date | None:
    value = _clean(raw)
    if not value:
        if allow_empty:
            return None
        raise IngestionError(f"{file_name} row {row_num}: missing required date field '{field}'")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise IngestionError(
            f"{file_name} row {row_num}: invalid date '{value}' for field '{field}', expected YYYY-MM-DD"
        ) from exc


def _parse_timestamp(
    raw: str, *, field: str, row_num: int, file_name: str, allow_empty: bool = True
) -> datetime | None:
    value = _clean(raw)
    if not value:
        if allow_empty:
            return None
        raise IngestionError(f"{file_name} row {row_num}: missing required timestamp field '{field}'")
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise IngestionError(
            f"{file_name} row {row_num}: invalid timestamp '{value}' for field '{field}'"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_optional_float(raw: Any, *, field: str, row_num: int, file_name: str) -> float | None:
    value = _clean(raw)
    if not value:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise IngestionError(f"{file_name} row {row_num}: invalid numeric value '{value}' for '{field}'") from exc


def _parse_attrs(raw: Any, *, row_num: int, file_name: str) -> dict[str, Any]:
    text = _clean(raw)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise IngestionError(f"{file_name} row {row_num}: attrs_json is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise IngestionError(f"{file_name} row {row_num}: attrs_json must decode to an object")
    return parsed


def _merge_attrs(base_attrs: dict[str, Any], extra_attrs: dict[str, Any]) -> dict[str, Any]:
    if not extra_attrs:
        return base_attrs
    merged = dict(base_attrs)
    for key, value in extra_attrs.items():
        merged.setdefault(key, value)
    return merged


def _validate_csv_columns(reader: csv.DictReader, *, required: set[str], file_name: str) -> None:
    columns = set(reader.fieldnames or [])
    missing = sorted(required - columns)
    if missing:
        raise IngestionError(f"{file_name}: missing required columns: {', '.join(missing)}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _chunked(iterable: Iterable[tuple[Any, ...]], size: int) -> Iterable[list[tuple[Any, ...]]]:
    batch: list[tuple[Any, ...]] = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _parse_metrics(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise IngestionError("merge_stage returned metrics in an unexpected format")


def _ensure_snapshot(
    cur: Cursor[Any],
    *,
    authority: str,
    dataset: str,
    dataset_version: str,
    airac_cycle: str | None,
    source_url: str | None,
    source_sha256: str,
    effective_from: date,
    effective_to: date,
    is_delta: bool,
    metadata: dict[str, Any],
) -> int:
    cur.execute(
        """
        SELECT snapshot_id
        FROM faa_airspace.source_snapshot
        WHERE authority = %s
          AND dataset = %s
          AND dataset_version = %s
          AND effective_from = %s
          AND effective_to = %s
          AND source_sha256 = %s
        LIMIT 1
        """,
        (authority, dataset, dataset_version, effective_from, effective_to, source_sha256),
    )
    row = cur.fetchone()
    if row:
        return int(row[0])

    cur.execute(
        """
        INSERT INTO faa_airspace.source_snapshot (
          authority,
          dataset,
          dataset_version,
          airac_cycle,
          source_url,
          source_sha256,
          effective_from,
          effective_to,
          is_delta,
          metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING snapshot_id
        """,
        (
            authority,
            dataset,
            dataset_version,
            airac_cycle,
            source_url,
            source_sha256,
            effective_from,
            effective_to,
            is_delta,
            Jsonb(metadata),
        ),
    )
    inserted = cur.fetchone()
    if not inserted:
        raise IngestionError("failed to create source snapshot")
    return int(inserted[0])


def _load_feature_stage(
    cur: Cursor[Any],
    *,
    path: Path,
    load_batch_id: uuid.UUID,
    source_snapshot_id: int,
    default_authority: str,
) -> int:
    sql = """
      INSERT INTO faa_airspace.stg_airspace_feature (
        load_batch_id,
        source_snapshot_id,
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
        attrs,
        row_num
      )
      VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
      )
    """

    staged_rows = 0
    with path.open("r", encoding="utf-8", newline="") as file_obj:
        reader = csv.DictReader(file_obj)
        _validate_csv_columns(reader, required=FEATURE_REQUIRED_COLUMNS, file_name=path.name)

        def row_iter() -> Iterable[tuple[Any, ...]]:
            nonlocal staged_rows
            for row_num, row in enumerate(reader, start=2):
                authority = _clean(row.get("authority")) or default_authority
                published_id = _clean(row.get("published_id"))
                airspace_type = _clean(row.get("airspace_type")).upper()
                valid_from = _parse_date(
                    row.get("valid_from"),
                    field="valid_from",
                    row_num=row_num,
                    file_name=path.name,
                )
                valid_to = _parse_date(
                    row.get("valid_to"),
                    field="valid_to",
                    row_num=row_num,
                    file_name=path.name,
                    allow_empty=True,
                ) or OPEN_ENDED_DATE
                status = (_clean(row.get("status")) or "active").lower()

                if not published_id:
                    raise IngestionError(f"{path.name} row {row_num}: missing published_id")
                if not airspace_type:
                    raise IngestionError(f"{path.name} row {row_num}: missing airspace_type")

                attrs = _parse_attrs(row.get("attrs_json"), row_num=row_num, file_name=path.name)
                extra_fields = {
                    key: value
                    for key, value in row.items()
                    if key not in FEATURE_KNOWN_COLUMNS and _clean(value)
                }
                attrs = _merge_attrs(attrs, extra_fields)

                staged_rows += 1
                yield (
                    load_batch_id,
                    source_snapshot_id,
                    authority,
                    published_id,
                    _clean(row.get("feature_name")) or None,
                    airspace_type,
                    _clean(row.get("class_code")) or None,
                    _clean(row.get("designator")) or None,
                    _clean(row.get("controlling_agency")) or None,
                    _clean(row.get("using_agency")) or None,
                    status,
                    valid_from,
                    valid_to,
                    Jsonb(attrs),
                    row_num,
                )

        for batch in _chunked(row_iter(), 1000):
            cur.executemany(sql, batch)

    return staged_rows


def _load_volume_stage(
    cur: Cursor[Any],
    *,
    path: Path,
    load_batch_id: uuid.UUID,
    source_snapshot_id: int,
    default_authority: str,
) -> int:
    sql = """
      INSERT INTO faa_airspace.stg_airspace_volume (
        load_batch_id,
        source_snapshot_id,
        authority,
        published_id,
        airspace_type,
        valid_from,
        volume_ordinal,
        lower_limit_value,
        lower_limit_uom,
        lower_limit_ref,
        upper_limit_value,
        upper_limit_uom,
        upper_limit_ref,
        geom_geojson,
        attrs,
        row_num
      )
      VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
      )
    """

    raw_text = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise IngestionError(f"{path.name}: file is not valid JSON") from exc
    if payload.get("type") != "FeatureCollection":
        raise IngestionError(f"{path.name}: expected a GeoJSON FeatureCollection")
    features = payload.get("features")
    if not isinstance(features, list):
        raise IngestionError(f"{path.name}: missing or invalid FeatureCollection.features")

    rows: list[tuple[Any, ...]] = []
    staged_rows = 0
    for row_num, item in enumerate(features, start=1):
        if not isinstance(item, dict):
            raise IngestionError(f"{path.name} feature {row_num}: feature must be an object")
        properties = item.get("properties") or {}
        if not isinstance(properties, dict):
            raise IngestionError(f"{path.name} feature {row_num}: properties must be an object")
        geometry = item.get("geometry")
        if not isinstance(geometry, dict):
            raise IngestionError(f"{path.name} feature {row_num}: geometry must be an object")

        authority = _clean(properties.get("authority")) or default_authority
        published_id = _clean(properties.get("published_id"))
        airspace_type = _clean(properties.get("airspace_type")).upper()
        valid_from = _parse_date(
            properties.get("valid_from"),
            field="valid_from",
            row_num=row_num,
            file_name=path.name,
        )

        if not published_id:
            raise IngestionError(f"{path.name} feature {row_num}: missing properties.published_id")
        if not airspace_type:
            raise IngestionError(f"{path.name} feature {row_num}: missing properties.airspace_type")

        volume_ordinal_raw = _clean(properties.get("volume_ordinal")) or "1"
        try:
            volume_ordinal = int(volume_ordinal_raw)
        except ValueError as exc:
            raise IngestionError(
                f"{path.name} feature {row_num}: volume_ordinal must be an integer"
            ) from exc

        attrs = _parse_attrs(properties.get("attrs_json"), row_num=row_num, file_name=path.name)
        extra_fields = {
            key: value
            for key, value in properties.items()
            if key not in VOLUME_KNOWN_PROPERTIES and _clean(value)
        }
        attrs = _merge_attrs(attrs, extra_fields)

        rows.append(
            (
                load_batch_id,
                source_snapshot_id,
                authority,
                published_id,
                airspace_type,
                valid_from,
                volume_ordinal,
                _parse_optional_float(
                    properties.get("lower_limit_value"),
                    field="lower_limit_value",
                    row_num=row_num,
                    file_name=path.name,
                ),
                _clean(properties.get("lower_limit_uom")) or "FT",
                _clean(properties.get("lower_limit_ref")) or "MSL",
                _parse_optional_float(
                    properties.get("upper_limit_value"),
                    field="upper_limit_value",
                    row_num=row_num,
                    file_name=path.name,
                ),
                _clean(properties.get("upper_limit_uom")) or None,
                _clean(properties.get("upper_limit_ref")) or None,
                json.dumps(geometry, separators=(",", ":"), ensure_ascii=True),
                Jsonb(attrs),
                row_num,
            )
        )
        staged_rows += 1

    for batch in _chunked(rows, 1000):
        cur.executemany(sql, batch)

    return staged_rows


def _load_schedule_stage(
    cur: Cursor[Any],
    *,
    path: Path,
    load_batch_id: uuid.UUID,
    source_snapshot_id: int,
    default_authority: str,
) -> int:
    sql = """
      INSERT INTO faa_airspace.stg_airspace_schedule (
        load_batch_id,
        source_snapshot_id,
        authority,
        published_id,
        airspace_type,
        valid_from,
        schedule_key,
        timezone_name,
        active_from,
        active_to,
        recurrence_rule,
        notam_id,
        status,
        notes,
        attrs,
        row_num
      )
      VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
      )
    """

    staged_rows = 0
    with path.open("r", encoding="utf-8", newline="") as file_obj:
        reader = csv.DictReader(file_obj)
        _validate_csv_columns(reader, required=SCHEDULE_REQUIRED_COLUMNS, file_name=path.name)

        def row_iter() -> Iterable[tuple[Any, ...]]:
            nonlocal staged_rows
            for row_num, row in enumerate(reader, start=2):
                authority = _clean(row.get("authority")) or default_authority
                published_id = _clean(row.get("published_id"))
                airspace_type = _clean(row.get("airspace_type")).upper()
                valid_from = _parse_date(
                    row.get("valid_from"),
                    field="valid_from",
                    row_num=row_num,
                    file_name=path.name,
                )
                schedule_key = _clean(row.get("schedule_key"))
                if not published_id:
                    raise IngestionError(f"{path.name} row {row_num}: missing published_id")
                if not airspace_type:
                    raise IngestionError(f"{path.name} row {row_num}: missing airspace_type")
                if not schedule_key:
                    raise IngestionError(f"{path.name} row {row_num}: missing schedule_key")

                attrs = _parse_attrs(row.get("attrs_json"), row_num=row_num, file_name=path.name)
                extra_fields = {
                    key: value
                    for key, value in row.items()
                    if key not in SCHEDULE_KNOWN_COLUMNS and _clean(value)
                }
                attrs = _merge_attrs(attrs, extra_fields)

                staged_rows += 1
                yield (
                    load_batch_id,
                    source_snapshot_id,
                    authority,
                    published_id,
                    airspace_type,
                    valid_from,
                    schedule_key,
                    _clean(row.get("timezone_name")) or "UTC",
                    _parse_timestamp(
                        row.get("active_from"),
                        field="active_from",
                        row_num=row_num,
                        file_name=path.name,
                        allow_empty=True,
                    ),
                    _parse_timestamp(
                        row.get("active_to"),
                        field="active_to",
                        row_num=row_num,
                        file_name=path.name,
                        allow_empty=True,
                    ),
                    _clean(row.get("recurrence_rule")) or None,
                    _clean(row.get("notam_id")) or None,
                    (_clean(row.get("status")) or "scheduled").lower(),
                    _clean(row.get("notes")) or None,
                    Jsonb(attrs),
                    row_num,
                )

        for batch in _chunked(row_iter(), 1000):
            cur.executemany(sql, batch)

    return staged_rows


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load FAA airspace data into Postgres/PostGIS staging and merge into curated tables."
    )
    parser.add_argument("--dsn", default=os.getenv("FAA_AIRSPACE_DSN"), help="Postgres DSN. Defaults to FAA_AIRSPACE_DSN.")
    parser.add_argument("--authority", default="FAA", help="Authority value for ingested records (default: FAA).")
    parser.add_argument("--dataset", default="NASR", help="Source dataset identifier (default: NASR).")
    parser.add_argument("--dataset-version", required=True, help="Dataset version label (example: 2025-11-27).")
    parser.add_argument("--airac-cycle", default=None, help="AIRAC cycle label (example: 2025-11-27).")
    parser.add_argument("--source-url", default=None, help="Source URL for the snapshot.")
    parser.add_argument("--effective-from", required=True, help="Snapshot effective start date (YYYY-MM-DD).")
    parser.add_argument(
        "--effective-to",
        default=str(OPEN_ENDED_DATE),
        help=f"Snapshot effective end date (YYYY-MM-DD). Default {OPEN_ENDED_DATE}.",
    )
    parser.add_argument("--is-delta", action="store_true", help="Mark snapshot as delta instead of baseline.")
    parser.add_argument("--metadata-json", default="{}", help="Additional JSON object merged into snapshot metadata.")
    parser.add_argument("--features-csv", required=True, type=Path, help="Normalized features CSV.")
    parser.add_argument("--volumes-geojson", required=True, type=Path, help="Normalized volumes GeoJSON FeatureCollection.")
    parser.add_argument("--schedules-csv", type=Path, default=None, help="Optional normalized schedules CSV.")
    parser.add_argument(
        "--keep-staging",
        action="store_true",
        help="Keep staged rows after merge. Default behavior purges staging for this batch.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    if psycopg is None or Jsonb is None:
        print(
            "error: psycopg is not installed. Install dependencies with: "
            "pip install -r backend/requirements.txt",
            file=sys.stderr,
        )
        return 2

    if not args.dsn:
        print("error: --dsn is required (or set FAA_AIRSPACE_DSN)", file=sys.stderr)
        return 2

    if not args.features_csv.exists():
        print(f"error: features file not found: {args.features_csv}", file=sys.stderr)
        return 2
    if not args.volumes_geojson.exists():
        print(f"error: volumes file not found: {args.volumes_geojson}", file=sys.stderr)
        return 2
    if args.schedules_csv and not args.schedules_csv.exists():
        print(f"error: schedules file not found: {args.schedules_csv}", file=sys.stderr)
        return 2

    try:
        effective_from = date.fromisoformat(args.effective_from)
        effective_to = date.fromisoformat(args.effective_to)
    except ValueError as exc:
        print("error: effective dates must use YYYY-MM-DD", file=sys.stderr)
        return 2
    if effective_to < effective_from:
        print("error: effective_to must be on or after effective_from", file=sys.stderr)
        return 2

    try:
        metadata = json.loads(args.metadata_json)
    except json.JSONDecodeError:
        print("error: --metadata-json must be valid JSON", file=sys.stderr)
        return 2
    if not isinstance(metadata, dict):
        print("error: --metadata-json must decode to a JSON object", file=sys.stderr)
        return 2

    file_hashes = {
        "features_csv": _sha256_file(args.features_csv),
        "volumes_geojson": _sha256_file(args.volumes_geojson),
    }
    if args.schedules_csv:
        file_hashes["schedules_csv"] = _sha256_file(args.schedules_csv)

    combined = hashlib.sha256()
    for key in sorted(file_hashes):
        combined.update(f"{key}:{file_hashes[key]}\n".encode("ascii"))
    source_sha256 = combined.hexdigest()

    metadata = {
        **metadata,
        "files": file_hashes,
        "ingested_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    run_id = uuid.uuid4()
    load_batch_id = uuid.uuid4()
    stage_counts = StageCounts()
    merge_metrics: dict[str, Any] = {}
    snapshot_id = 0

    try:
        with psycopg.connect(args.dsn, autocommit=False) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    snapshot_id = _ensure_snapshot(
                        cur,
                        authority=args.authority,
                        dataset=args.dataset,
                        dataset_version=args.dataset_version,
                        airac_cycle=args.airac_cycle,
                        source_url=args.source_url,
                        source_sha256=source_sha256,
                        effective_from=effective_from,
                        effective_to=effective_to,
                        is_delta=bool(args.is_delta),
                        metadata=metadata,
                    )
                    cur.execute(
                        """
                        INSERT INTO faa_airspace.ingestion_run (
                          run_id,
                          source_snapshot_id,
                          status,
                          message
                        )
                        VALUES (%s, %s, 'running', %s)
                        """,
                        (
                            run_id,
                            snapshot_id,
                            f"dataset={args.dataset}, version={args.dataset_version}, batch={load_batch_id}",
                        ),
                    )

            try:
                with conn.transaction():
                    with conn.cursor() as cur:
                        stage_counts.features = _load_feature_stage(
                            cur,
                            path=args.features_csv,
                            load_batch_id=load_batch_id,
                            source_snapshot_id=snapshot_id,
                            default_authority=args.authority,
                        )
                        stage_counts.volumes = _load_volume_stage(
                            cur,
                            path=args.volumes_geojson,
                            load_batch_id=load_batch_id,
                            source_snapshot_id=snapshot_id,
                            default_authority=args.authority,
                        )
                        if args.schedules_csv:
                            stage_counts.schedules = _load_schedule_stage(
                                cur,
                                path=args.schedules_csv,
                                load_batch_id=load_batch_id,
                                source_snapshot_id=snapshot_id,
                                default_authority=args.authority,
                            )

                        cur.execute(
                            "SELECT faa_airspace.merge_stage(%s, %s)",
                            (load_batch_id, run_id),
                        )
                        merge_row = cur.fetchone()
                        if not merge_row:
                            raise IngestionError("merge_stage did not return metrics")
                        merge_metrics = _parse_metrics(merge_row[0])

                        if not args.keep_staging:
                            cur.execute("SELECT faa_airspace.purge_staging_batch(%s)", (load_batch_id,))

                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE faa_airspace.ingestion_run
                               SET metrics = COALESCE(metrics, '{}'::jsonb) || %s::jsonb
                             WHERE run_id = %s
                            """,
                            (
                                json.dumps(
                                    {
                                        "staged_features": stage_counts.features,
                                        "staged_volumes": stage_counts.volumes,
                                        "staged_schedules": stage_counts.schedules,
                                        "load_batch_id": str(load_batch_id),
                                    }
                                ),
                                run_id,
                            ),
                        )
            except Exception as exc:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE faa_airspace.ingestion_run
                               SET finished_at = now(),
                                   status = 'failed',
                                   message = %s
                             WHERE run_id = %s
                            """,
                            (str(exc)[:1000], run_id),
                        )
                raise
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    summary = {
        "run_id": str(run_id),
        "source_snapshot_id": snapshot_id,
        "load_batch_id": str(load_batch_id),
        "dataset": args.dataset,
        "dataset_version": args.dataset_version,
        "airac_cycle": args.airac_cycle,
        "staged": {
            "features": stage_counts.features,
            "volumes": stage_counts.volumes,
            "schedules": stage_counts.schedules,
        },
        "merged": merge_metrics,
        "keep_staging": bool(args.keep_staging),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
