#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import urllib.parse
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import shapefile  # type: ignore[import-not-found]
except ModuleNotFoundError:
    shapefile = None  # type: ignore[assignment]

OPEN_ENDED_DATE = date(9999, 12, 31)
INVALID_ALTITUDE_SENTINEL = -9000.0

DEFAULT_SHP_KEYWORDS = [
    "sua",
    "special",
    "airspace",
    "class",
    "restrict",
    "warning",
    "prohibit",
    "moa",
]

POLYGON_SHAPE_TYPES = {5, 15, 25}

FEATURE_COLUMNS = [
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
]

SCHEDULE_COLUMNS = [
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
]

FIELD_CANDIDATES: dict[str, list[str]] = {
    "published_id": [
        "PUBLISHED_ID",
        "DESIGNATOR",
        "DESIG",
        "SUA_ID",
        "AIRSPACE_ID",
        "AIRSPACEID",
        "ID",
        "IDENT",
    ],
    "feature_name": [
        "FEATURE_NAME",
        "NAME",
        "AIRSPACE_NAME",
        "AIRSPACE_NA",
        "AREA_NAME",
        "DESCRIPTION",
        "DESC",
    ],
    "airspace_type": [
        "AIRSPACE_TYPE",
        "TYPE",
        "SUA_TYPE",
        "CATEGORY",
        "CLASS",
    ],
    "class_code": ["CLASS", "CLASS_CODE", "AIRSPACE_CL"],
    "designator": ["DESIGNATOR", "DESIG", "IDENT"],
    "controlling_agency": ["CONTROLLING", "CTRL_AGENCY", "CONT_AGENCY", "ATC_FAC", "ARTCC"],
    "using_agency": ["USING_AGENC", "USING_AGCY", "USINGAGENCY", "OWNING_AGEN", "OWNER"],
    "status": ["STATUS", "ACTIVE", "ACT_STAT", "STATE"],
    "valid_from": ["VALID_FROM", "START_DATE", "EFF_DATE", "EFFECTIVE", "BEGIN_DATE", "FROM_DATE"],
    "valid_to": ["VALID_TO", "END_DATE", "EXP_DATE", "EXPIRES", "THRU_DATE", "TO_DATE"],
    "lower_value": ["LOWER_VAL", "LOWER", "FLOOR", "LOW_ALT", "ALT_LOW", "LOWERLIM"],
    "lower_uom": ["LOWER_UOM", "LOWERUNIT", "LOW_UOM", "L_UNIT"],
    "lower_ref": ["LOWER_REF", "LOWERREF", "LOW_REF", "L_REF"],
    "lower_text": ["LOWER_DESC", "LOWER_TXT", "LOW_DESC", "LOWERLIMIT"],
    "upper_value": ["UPPER_VAL", "UPPER", "CEILING", "HIGH_ALT", "ALT_HIGH", "UPPERLIM"],
    "upper_uom": ["UPPER_UOM", "UPPERUNIT", "UP_UOM", "U_UNIT"],
    "upper_ref": ["UPPER_REF", "UPPERREF", "UP_REF", "U_REF"],
    "upper_text": ["UPPER_DESC", "UPPER_TXT", "UP_DESC", "UPPERLIMIT"],
    "volume_ordinal": ["VOLUME", "ORDINAL", "PART", "SEGMENT"],
    "schedule_key": ["SCHEDULE", "SCHED_KEY", "SCHED_ID", "TIME_CODE"],
    "notam_id": ["NOTAM_ID", "NOTAM", "NOTAMNUM"],
    "rrule": ["RRULE", "RECURRENCE", "RECUR_RULE"],
    "timezone_name": ["TZ", "TIMEZONE", "TZ_NAME"],
}

STATUS_MAP = {
    "A": "active",
    "ACTIVE": "active",
    "Y": "active",
    "TRUE": "active",
    "I": "inactive",
    "INACTIVE": "inactive",
    "N": "inactive",
    "FALSE": "inactive",
    "CLOSED": "inactive",
    "DEPRECATED": "deprecated",
}

AIRSPACE_TYPE_MAP = {
    "R": "RESTRICTED",
    "RESTRICTED": "RESTRICTED",
    "P": "PROHIBITED",
    "PROHIBITED": "PROHIBITED",
    "W": "WARNING",
    "WARNING": "WARNING",
    "MOA": "MOA",
    "ALERT": "ALERT",
    "TRA": "TRA",
    "TSA": "TSA",
    "CTA": "CTA",
    "TMA": "TMA",
    "CTR": "CTR",
    "CLASSB": "CLASS_B",
    "CLASSC": "CLASS_C",
    "CLASSD": "CLASS_D",
    "CLASSE": "CLASS_E",
    "CLASSA": "CLASS_A",
}


@dataclass
class ExtractStats:
    shapefiles_considered: int = 0
    shapefiles_selected: int = 0
    records_read: int = 0
    records_kept: int = 0
    records_skipped_non_polygon: int = 0
    records_skipped_empty_geometry: int = 0
    features_written: int = 0
    volumes_written: int = 0
    schedules_written: int = 0


class ExtractError(RuntimeError):
    pass


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace").strip()
        except Exception:
            return str(value).strip()
    return str(value).strip()


def _norm_key(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def _stringify_json(value: dict[str, Any]) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True)


def _safe_slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text.strip()).strip("-").upper()
    return slug or "UNSPECIFIED"


def _first_present(
    record_norm: dict[str, Any],
    candidates: list[str],
    *,
    track: set[str] | None = None,
) -> Any:
    for candidate in candidates:
        key = _norm_key(candidate)
        if key in record_norm and _clean(record_norm[key]):
            if track is not None:
                track.add(key)
            return record_norm[key]
    return None


def _parse_date_any(raw: Any) -> date | None:
    text = _clean(raw)
    if not text:
        return None

    attempts = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y%m%d",
        "%m/%d/%Y",
        "%d-%b-%Y",
        "%d-%b-%y",
    ]
    for fmt in attempts:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass

    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).date()
    except Exception:
        return None


def _parse_timestamp_any(raw: Any) -> str | None:
    text = _clean(raw)
    if not text:
        return None

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    for fmt in [
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
    ]:
        try:
            parsed = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return parsed.isoformat().replace("+00:00", "Z")
        except ValueError:
            pass

    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def _parse_float_any(raw: Any) -> float | None:
    text = _clean(raw)
    if not text:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _normalize_status(raw: Any) -> str:
    text = _clean(raw).upper()
    if not text:
        return "active"
    return STATUS_MAP.get(text, "active")


def _infer_type_from_published_id(published_id: str) -> str | None:
    up = published_id.upper()
    if up.startswith("R-"):
        return "RESTRICTED"
    if up.startswith("P-"):
        return "PROHIBITED"
    if up.startswith("W-"):
        return "WARNING"
    if up.endswith("MOA"):
        return "MOA"
    return None


def _normalize_airspace_type(raw: Any, *, published_id: str, default_type: str) -> str:
    text = _clean(raw).upper()
    if not text:
        inferred = _infer_type_from_published_id(published_id)
        return inferred or default_type

    normalized = AIRSPACE_TYPE_MAP.get(text)
    if normalized:
        return normalized

    compact = re.sub(r"[^A-Z0-9]", "", text)
    normalized = AIRSPACE_TYPE_MAP.get(compact)
    if normalized:
        return normalized

    return text


def _normalize_uom(raw: Any, *, fallback: str) -> str:
    text = _clean(raw).upper()
    if not text:
        return fallback
    if "SFC" in text:
        return "SFC"
    if "FL" in text:
        return "FL"
    if text in {"M", "METER", "METERS"}:
        return "M"
    return "FT"


def _normalize_ref(raw: Any, *, fallback: str) -> str:
    text = _clean(raw).upper()
    if not text:
        return fallback
    if "SFC" in text:
        return "SFC"
    if "AGL" in text:
        return "AGL"
    if "STD" in text or "STANDARD" in text or "FL" in text:
        return "STD"
    if "UNL" in text or "UNLIMITED" in text:
        return "UNL"
    return "MSL"


def _infer_altitude(value_raw: Any, text_raw: Any, *, default_uom: str, default_ref: str) -> tuple[float | None, str, str]:
    text = _clean(text_raw)
    value = _parse_float_any(value_raw)

    merged = f"{_clean(value_raw)} {_clean(text_raw)}".upper()
    uom = _normalize_uom(text_raw or value_raw, fallback=default_uom)
    ref = _normalize_ref(text_raw or value_raw, fallback=default_ref)

    if value is None and "FL" in merged:
        match = re.search(r"FL\s*(\d+)", merged)
        if match:
            value = float(match.group(1))
            uom = "FL"
            ref = "STD"

    if "UNL" in merged:
        return None, uom, "UNL"

    if "SFC" in merged and value is None:
        value = 0.0
        uom = "SFC"
        ref = "SFC"

    return value, uom, ref


def _sanitize_altitude_value(value: float | None) -> float | None:
    if value is None:
        return None
    if value <= INVALID_ALTITUDE_SENTINEL:
        return None
    return value


def _shape_to_geojson_multipolygon(shape_obj: Any) -> dict[str, Any] | None:
    points = getattr(shape_obj, "points", None)
    parts = getattr(shape_obj, "parts", None)
    if not points or parts is None:
        return None

    part_indexes = list(parts) + [len(points)]
    polygons: list[list[list[list[float]]]] = []

    for idx in range(len(part_indexes) - 1):
        start = part_indexes[idx]
        end = part_indexes[idx + 1]
        raw_ring = points[start:end]
        if len(raw_ring) < 3:
            continue

        ring = [[float(pt[0]), float(pt[1])] for pt in raw_ring]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        if len(ring) < 4:
            continue

        # Treat each part as independent polygon to avoid fragile hole inference.
        polygons.append([ring])

    if not polygons:
        return None

    return {
        "type": "MultiPolygon",
        "coordinates": polygons,
    }


def _download_file(url: str, download_dir: Path) -> Path:
    download_dir.mkdir(parents=True, exist_ok=True)
    parsed = urllib.parse.urlparse(url)
    filename = Path(parsed.path).name or "nasr_cycle.zip"
    destination = download_dir / filename

    with urllib.request.urlopen(url, timeout=120) as response, destination.open("wb") as out:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    return destination


def _extract_zip_recursive(archive_path: Path, extract_root: Path) -> Path:
    if not archive_path.exists():
        raise ExtractError(f"archive not found: {archive_path}")

    extract_root.mkdir(parents=True, exist_ok=True)

    first_dir = extract_root / archive_path.stem
    first_dir.mkdir(parents=True, exist_ok=True)

    processed: set[Path] = set()
    queue: list[tuple[Path, Path]] = [(archive_path, first_dir)]

    while queue:
        zip_path, dest_dir = queue.pop(0)
        zip_real = zip_path.resolve()
        if zip_real in processed:
            continue
        processed.add(zip_real)

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(dest_dir)

        nested = sorted(dest_dir.rglob("*.zip"))
        for nested_zip in nested:
            nested_dest = nested_zip.parent / nested_zip.stem
            nested_dest.mkdir(parents=True, exist_ok=True)
            queue.append((nested_zip, nested_dest))

    return first_dir


def _discover_shapefiles(extracted_root: Path, keywords: list[str]) -> tuple[list[Path], list[Path]]:
    all_shps = sorted(path for path in extracted_root.rglob("*.shp") if path.is_file())
    if not all_shps:
        raise ExtractError(f"no .shp files found under {extracted_root}")

    lowered = [kw.lower() for kw in keywords if kw.strip()]
    selected = [
        shp
        for shp in all_shps
        if any(kw in shp.name.lower() or kw in str(shp.parent).lower() for kw in lowered)
    ]

    if selected:
        return all_shps, selected
    return all_shps, all_shps


def _record_to_dict(record_obj: Any, field_names: list[str]) -> dict[str, Any]:
    if hasattr(record_obj, "as_dict"):
        raw = record_obj.as_dict()
        return {str(k): v for k, v in raw.items()}

    values = list(record_obj)
    out: dict[str, Any] = {}
    for idx, name in enumerate(field_names):
        out[name] = values[idx] if idx < len(values) else None
    return out


def _empty_schedule_row() -> dict[str, str]:
    return {column: "" for column in SCHEDULE_COLUMNS}


def _build_schedule_row(
    authority: str,
    published_id: str,
    airspace_type: str,
    valid_from: date,
    *,
    schedule_key: str,
    timezone_name: str,
    active_from: str | None,
    active_to: str | None,
    recurrence_rule: str | None,
    notam_id: str | None,
) -> dict[str, str]:
    row = _empty_schedule_row()
    row.update(
        {
            "authority": authority,
            "published_id": published_id,
            "airspace_type": airspace_type,
            "valid_from": valid_from.isoformat(),
            "schedule_key": schedule_key,
            "timezone_name": timezone_name or "UTC",
            "active_from": active_from or "",
            "active_to": active_to or "",
            "recurrence_rule": recurrence_rule or "",
            "notam_id": notam_id or "",
            "status": "scheduled",
            "notes": "Derived from NASR source fields",
            "attrs_json": _stringify_json({"source": "nasr_extractor"}),
        }
    )
    return row


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download/extract FAA NASR raw ZIPs, discover airspace shapefiles, and write "
            "normalized features.csv / volumes.geojson for ingest_faa_airspace.py"
        )
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--nasr-zip-path", type=Path, help="Path to a NASR ZIP archive")
    source_group.add_argument("--nasr-zip-url", help="HTTP URL to a NASR ZIP archive")
    source_group.add_argument("--nasr-dir", type=Path, help="Path to an already-extracted NASR directory")

    parser.add_argument("--work-dir", type=Path, default=Path("./work"), help="Working directory for downloads/extract")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./work/normalized"),
        help="Output directory for normalized files",
    )
    parser.add_argument("--authority", default="FAA", help="Authority field value (default: FAA)")
    parser.add_argument(
        "--default-airspace-type",
        default="SPECIAL_USE",
        help="Fallback airspace type when source record has no recognizable type",
    )
    parser.add_argument(
        "--effective-from",
        required=True,
        help="Default valid_from date used when source records have no effective date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--effective-to",
        default=str(OPEN_ENDED_DATE),
        help=f"Default valid_to date used when source records have no end date (YYYY-MM-DD, default {OPEN_ENDED_DATE})",
    )
    parser.add_argument(
        "--shp-name-contains",
        default=",".join(DEFAULT_SHP_KEYWORDS),
        help="Comma-separated keywords for selecting relevant shapefiles",
    )
    parser.add_argument(
        "--emit-empty-schedules",
        action="store_true",
        help="Always write schedules.csv header even if no schedules are inferred",
    )
    parser.add_argument("--verbose", action="store_true", help="Print selected shapefiles and field diagnostics")

    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    if shapefile is None:
        print(
            "error: pyshp is not installed. Install dependencies with: pip install -r backend/requirements.txt",
            file=sys.stderr,
        )
        return 2

    try:
        default_valid_from = date.fromisoformat(args.effective_from)
        default_valid_to = date.fromisoformat(args.effective_to)
    except ValueError:
        print("error: --effective-from/--effective-to must use YYYY-MM-DD", file=sys.stderr)
        return 2

    if default_valid_to < default_valid_from:
        print("error: --effective-to must be on/after --effective-from", file=sys.stderr)
        return 2

    work_dir: Path = args.work_dir
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    stats = ExtractStats()
    selected_shapefiles: list[Path] = []

    try:
        if args.nasr_dir:
            extracted_root = args.nasr_dir.resolve()
            if not extracted_root.exists() or not extracted_root.is_dir():
                raise ExtractError(f"--nasr-dir does not exist or is not a directory: {extracted_root}")
            archive_source = None
        else:
            if args.nasr_zip_url:
                archive_source = _download_file(args.nasr_zip_url, work_dir / "downloads")
            else:
                archive_source = args.nasr_zip_path.resolve()

            extracted_root = _extract_zip_recursive(archive_source, work_dir / "extracted")

        keywords = [item.strip() for item in args.shp_name_contains.split(",") if item.strip()]
        all_shps, selected_shapefiles = _discover_shapefiles(extracted_root, keywords)
        stats.shapefiles_considered = len(all_shps)
        stats.shapefiles_selected = len(selected_shapefiles)

        if args.verbose:
            print("Selected shapefiles:")
            for shp in selected_shapefiles:
                print(f"- {shp}")

        features_index: dict[tuple[str, str, str, date], dict[str, Any]] = {}
        feature_volume_ordinals_used: dict[tuple[str, str, str, date], set[int]] = {}
        feature_volume_next_ordinal: dict[tuple[str, str, str, date], int] = {}
        volumes_geojson: list[dict[str, Any]] = []
        schedules: list[dict[str, str]] = []

        for shp_path in selected_shapefiles:
            reader = shapefile.Reader(str(shp_path), encoding="latin1")
            field_names = [field[0] for field in reader.fields[1:]]

            if args.verbose:
                print(f"\nFields for {shp_path.name}: {', '.join(field_names)}")

            for idx, shape_record in enumerate(reader.iterShapeRecords(), start=1):
                stats.records_read += 1

                shape_obj = shape_record.shape
                if getattr(shape_obj, "shapeType", None) not in POLYGON_SHAPE_TYPES:
                    stats.records_skipped_non_polygon += 1
                    continue

                geometry = _shape_to_geojson_multipolygon(shape_obj)
                if geometry is None:
                    stats.records_skipped_empty_geometry += 1
                    continue

                raw_record = _record_to_dict(shape_record.record, field_names)
                record_norm = {_norm_key(key): value for key, value in raw_record.items()}
                consumed: set[str] = set()

                published_raw = _first_present(record_norm, FIELD_CANDIDATES["published_id"], track=consumed)
                designator_raw = _first_present(record_norm, FIELD_CANDIDATES["designator"], track=consumed)
                feature_name_raw = _first_present(record_norm, FIELD_CANDIDATES["feature_name"], track=consumed)

                published_id = _clean(published_raw)
                designator = _clean(designator_raw)
                feature_name = _clean(feature_name_raw)

                if not published_id:
                    published_id = designator or feature_name
                if not published_id:
                    published_id = f"{_safe_slug(shp_path.stem)}-{idx}"
                published_id = _safe_slug(published_id)

                airspace_type_raw = _first_present(record_norm, FIELD_CANDIDATES["airspace_type"], track=consumed)
                airspace_type = _normalize_airspace_type(
                    airspace_type_raw,
                    published_id=published_id,
                    default_type=args.default_airspace_type.upper(),
                )

                class_code = _clean(_first_present(record_norm, FIELD_CANDIDATES["class_code"], track=consumed))
                controlling_agency = _clean(
                    _first_present(record_norm, FIELD_CANDIDATES["controlling_agency"], track=consumed)
                )
                using_agency = _clean(_first_present(record_norm, FIELD_CANDIDATES["using_agency"], track=consumed))
                status = _normalize_status(_first_present(record_norm, FIELD_CANDIDATES["status"], track=consumed))

                valid_from_raw = _first_present(record_norm, FIELD_CANDIDATES["valid_from"], track=consumed)
                valid_to_raw = _first_present(record_norm, FIELD_CANDIDATES["valid_to"], track=consumed)
                valid_from = _parse_date_any(valid_from_raw) or default_valid_from
                valid_to = _parse_date_any(valid_to_raw) or default_valid_to
                if valid_to < valid_from:
                    valid_to = valid_from

                lower_value_raw = _first_present(record_norm, FIELD_CANDIDATES["lower_value"], track=consumed)
                lower_uom_raw = _first_present(record_norm, FIELD_CANDIDATES["lower_uom"], track=consumed)
                lower_ref_raw = _first_present(record_norm, FIELD_CANDIDATES["lower_ref"], track=consumed)
                lower_text_raw = _first_present(record_norm, FIELD_CANDIDATES["lower_text"], track=consumed)

                upper_value_raw = _first_present(record_norm, FIELD_CANDIDATES["upper_value"], track=consumed)
                upper_uom_raw = _first_present(record_norm, FIELD_CANDIDATES["upper_uom"], track=consumed)
                upper_ref_raw = _first_present(record_norm, FIELD_CANDIDATES["upper_ref"], track=consumed)
                upper_text_raw = _first_present(record_norm, FIELD_CANDIDATES["upper_text"], track=consumed)

                lower_value, lower_uom_inferred, lower_ref_inferred = _infer_altitude(
                    lower_value_raw,
                    lower_text_raw,
                    default_uom="FT",
                    default_ref="MSL",
                )
                upper_value, upper_uom_inferred, upper_ref_inferred = _infer_altitude(
                    upper_value_raw,
                    upper_text_raw,
                    default_uom="FT",
                    default_ref="MSL",
                )

                lower_uom = _normalize_uom(lower_uom_raw, fallback=lower_uom_inferred)
                lower_ref = _normalize_ref(lower_ref_raw, fallback=lower_ref_inferred)
                upper_uom = _normalize_uom(upper_uom_raw, fallback=upper_uom_inferred)
                upper_ref = _normalize_ref(upper_ref_raw, fallback=upper_ref_inferred)

                if lower_ref == "SFC" and lower_value is None:
                    lower_value = 0.0

                lower_value = _sanitize_altitude_value(lower_value)
                upper_value = _sanitize_altitude_value(upper_value)
                if lower_value is not None and upper_value is not None and upper_value < lower_value:
                    # Preserve the floor and drop invalid ceiling values to satisfy DB constraints.
                    upper_value = None

                volume_ordinal_raw = _first_present(record_norm, FIELD_CANDIDATES["volume_ordinal"], track=consumed)
                try:
                    volume_ordinal_candidate = (
                        int(float(_clean(volume_ordinal_raw))) if _clean(volume_ordinal_raw) else None
                    )
                except ValueError:
                    volume_ordinal_candidate = None
                if volume_ordinal_candidate is not None and volume_ordinal_candidate <= 0:
                    volume_ordinal_candidate = None

                source_attrs = {
                    "source_shapefile": str(shp_path.relative_to(extracted_root)),
                    "source_record_index": idx,
                }
                for norm_key, value in record_norm.items():
                    if norm_key in consumed:
                        continue
                    text = _clean(value)
                    if text:
                        source_attrs[norm_key] = text

                feature_key = (args.authority, published_id, airspace_type, valid_from)
                feature_row = features_index.get(feature_key)
                if feature_row is None:
                    feature_row = {
                        "authority": args.authority,
                        "published_id": published_id,
                        "feature_name": feature_name,
                        "airspace_type": airspace_type,
                        "class_code": class_code,
                        "designator": designator or published_id,
                        "controlling_agency": controlling_agency,
                        "using_agency": using_agency,
                        "status": status,
                        "valid_from": valid_from.isoformat(),
                        "valid_to": valid_to.isoformat(),
                        "attrs_json": _stringify_json(
                            {
                                "extracted_by": "extract_nasr_to_normalized.py",
                                "source": source_attrs,
                            }
                        ),
                    }
                    features_index[feature_key] = feature_row
                else:
                    if not feature_row["feature_name"] and feature_name:
                        feature_row["feature_name"] = feature_name
                    if not feature_row["class_code"] and class_code:
                        feature_row["class_code"] = class_code
                    if not feature_row["controlling_agency"] and controlling_agency:
                        feature_row["controlling_agency"] = controlling_agency
                    if not feature_row["using_agency"] and using_agency:
                        feature_row["using_agency"] = using_agency
                    prev_valid_to = _parse_date_any(feature_row["valid_to"]) or valid_to
                    if valid_to > prev_valid_to:
                        feature_row["valid_to"] = valid_to.isoformat()

                # Normalize to a unique, positive ordinal per (authority, published_id, airspace_type, valid_from).
                used_ordinals = feature_volume_ordinals_used.setdefault(feature_key, set())
                next_ordinal = feature_volume_next_ordinal.get(feature_key, 1)
                if volume_ordinal_candidate is None or volume_ordinal_candidate in used_ordinals:
                    while next_ordinal in used_ordinals:
                        next_ordinal += 1
                    volume_ordinal = next_ordinal
                    feature_volume_next_ordinal[feature_key] = next_ordinal + 1
                else:
                    volume_ordinal = volume_ordinal_candidate
                    if volume_ordinal >= next_ordinal:
                        feature_volume_next_ordinal[feature_key] = volume_ordinal + 1
                used_ordinals.add(volume_ordinal)

                volume_feature = {
                    "type": "Feature",
                    "properties": {
                        "authority": args.authority,
                        "published_id": published_id,
                        "airspace_type": airspace_type,
                        "valid_from": valid_from.isoformat(),
                        "volume_ordinal": volume_ordinal,
                        "lower_limit_value": lower_value,
                        "lower_limit_uom": lower_uom,
                        "lower_limit_ref": lower_ref,
                        "upper_limit_value": upper_value,
                        "upper_limit_uom": upper_uom,
                        "upper_limit_ref": upper_ref,
                        "attrs_json": _stringify_json(
                            {
                                "source_shapefile": str(shp_path.relative_to(extracted_root)),
                                "source_record_index": idx,
                            }
                        ),
                    },
                    "geometry": geometry,
                }
                volumes_geojson.append(volume_feature)

                schedule_key = _clean(_first_present(record_norm, FIELD_CANDIDATES["schedule_key"], track=consumed))
                if schedule_key:
                    timezone_name = _clean(
                        _first_present(record_norm, FIELD_CANDIDATES["timezone_name"], track=consumed)
                    ) or "UTC"
                    recurrence_rule = _clean(_first_present(record_norm, FIELD_CANDIDATES["rrule"], track=consumed))
                    notam_id = _clean(_first_present(record_norm, FIELD_CANDIDATES["notam_id"], track=consumed))
                    schedules.append(
                        _build_schedule_row(
                            args.authority,
                            published_id,
                            airspace_type,
                            valid_from,
                            schedule_key=schedule_key,
                            timezone_name=timezone_name,
                            active_from=_parse_timestamp_any(valid_from_raw),
                            active_to=_parse_timestamp_any(valid_to_raw),
                            recurrence_rule=recurrence_rule or None,
                            notam_id=notam_id or None,
                        )
                    )

                stats.records_kept += 1

        features_out = output_dir / "features.csv"
        volumes_out = output_dir / "volumes.geojson"
        schedules_out = output_dir / "schedules.csv"
        metadata_out = output_dir / "extract_metadata.json"

        features_rows = sorted(
            features_index.values(),
            key=lambda row: (row["published_id"], row["airspace_type"], row["valid_from"]),
        )

        with features_out.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FEATURE_COLUMNS)
            writer.writeheader()
            for row in features_rows:
                writer.writerow(row)

        with volumes_out.open("w", encoding="utf-8") as f:
            json.dump({"type": "FeatureCollection", "features": volumes_geojson}, f, indent=2)

        with schedules_out.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=SCHEDULE_COLUMNS)
            writer.writeheader()
            if schedules:
                for row in schedules:
                    writer.writerow(row)
            elif args.emit_empty_schedules:
                pass

        stats.features_written = len(features_rows)
        stats.volumes_written = len(volumes_geojson)
        stats.schedules_written = len(schedules)

        metadata = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "authority": args.authority,
            "default_airspace_type": args.default_airspace_type,
            "effective_from_default": default_valid_from.isoformat(),
            "effective_to_default": default_valid_to.isoformat(),
            "source": {
                "nasr_zip_url": args.nasr_zip_url,
                "nasr_zip_path": str(args.nasr_zip_path.resolve()) if args.nasr_zip_path else None,
                "nasr_dir": str(args.nasr_dir.resolve()) if args.nasr_dir else None,
            },
            "extracted_root": str(extracted_root),
            "selected_shapefiles": [str(path) for path in selected_shapefiles],
            "stats": asdict(stats),
            "outputs": {
                "features_csv": str(features_out),
                "volumes_geojson": str(volumes_out),
                "schedules_csv": str(schedules_out),
            },
        }
        with metadata_out.open("w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

        print(json.dumps(metadata, indent=2))
        return 0

    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
