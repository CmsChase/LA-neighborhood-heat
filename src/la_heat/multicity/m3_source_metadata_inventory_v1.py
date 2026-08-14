"""Authorized metadata-only expansion of the M3 source inventory.

The authorization is append-only and grants only an assets-excluded Landsat
STAC metadata query for Houston and Chicago.  The inventory builder retains the
authenticated Los Angeles and Phoenix overpasses, queries the complete fixed
Houston/Chicago window, and never opens an asset href, raster, QA value, target
table, predictor value, model, or blind-test-city source.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final, Protocol

import geopandas as gpd
import pandas as pd

from la_heat.multicity import portable_predictor_inventory as legacy_inventory
from la_heat.multicity import source_footprints as footprints
from la_heat.multicity.config import load_multicity_plan
from la_heat.multicity.m3_source_acquisition_amendment import (
    AMENDMENT_PATH,
    authenticate_m3_source_acquisition_amendment,
)
from la_heat.provenance import canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-source-metadata-inventory-v1"
MODULE_PATH: Final = Path(
    "src/la_heat/multicity/m3_source_metadata_inventory_v1.py"
)
SCRIPT_PATH: Final = Path("scripts/stage_m3_source_metadata_inventory_v1.py")
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_SOURCE_METADATA_INVENTORY_AUTHORIZATION.json"
)
INVENTORY_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_SOURCE_EXPANDED_INVENTORY.json"
)
RAW_ROOT: Final = Path("data/raw/multicity/m3_source_expanded_inventory")

SOURCE_CITY_IDS: Final = (
    "los_angeles_ca",
    "phoenix_az",
    "houston_tx",
    "chicago_il",
)
RETAINED_CITY_IDS: Final = ("los_angeles_ca", "phoenix_az")
QUERY_CITY_IDS: Final = ("houston_tx", "chicago_il")
BLIND_TEST_CITY_IDS: Final = (
    "seattle_wa",
    "denver_co",
    "atlanta_ga",
    "miami_fl",
)
QUERY_START: Final = date(2020, 5, 1)
QUERY_END: Final = date(2025, 10, 31)
WARM_MONTHS: Final = (5, 6, 7, 8, 9, 10)
MAXIMUM_OVERPASS_SPAN_MINUTES: Final = 15
MINIMUM_UNION_COVERAGE: Final = 0.98

QUERY_IMPLEMENTATION_PATHS: Final = (
    "configs/multicity/experiment.toml",
    "src/la_heat/inventory.py",
    "src/la_heat/multicity/config.py",
    "src/la_heat/multicity/portable_predictor_inventory.py",
    "src/la_heat/multicity/source_footprints.py",
    "src/la_heat/provenance.py",
)
AUTHORIZATION_PERMISSIONS: Final = {
    "read_authenticated_existing_la_phoenix_metadata": True,
    "read_houston_chicago_public_geography": True,
    "query_houston_chicago_public_landsat_stac_metadata": True,
    "persist_assets_excluded_public_metadata_pages": True,
    "write_expanded_source_inventory": True,
    "query_other_source_or_blind_test_cities": False,
    "request_or_read_landsat_item_assets": False,
    "read_or_persist_landsat_asset_hrefs": False,
    "sign_landsat_asset_hrefs": False,
    "read_landsat_thermal_or_target_qa_values": False,
    "read_raw_or_processed_target_tables": False,
    "build_predictors": False,
    "prefetch_landsat_assets": False,
    "rebuild_targets": False,
    "fit_select_predict_or_score": False,
    "create_values_opened_or_access_started_marker": False,
}
INVENTORY_ACCESS_AUDIT: Final = {
    "existing_la_phoenix_committed_metadata_read": True,
    "houston_chicago_public_landsat_metadata_read": True,
    "landsat_assets_excluded_from_queries": True,
    "landsat_item_assets_or_hrefs_read": False,
    "landsat_thermal_or_target_qa_values_read": False,
    "raw_or_processed_target_tables_read": False,
    "predictors_built_or_read": False,
    "model_fit_selection_prediction_or_scoring_performed": False,
}


class _MetadataClient(Protocol):
    def post(self, *args: Any, **kwargs: Any) -> Any: ...


QueryRunner = Callable[
    [Path, str, _MetadataClient | None],
    tuple[pd.DataFrame, list[dict[str, Any]], list[dict[str, Any]]],
]


class M3SourceMetadataInventoryError(RuntimeError):
    """Raised when authorization, query scope, or inventory evidence drifts."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise M3SourceMetadataInventoryError(message)


def _inside(root: Path, value: str | Path, *, label: str) -> Path:
    raw = Path(value)
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not path.is_relative_to(root):
        raise M3SourceMetadataInventoryError(f"{label} must stay inside the project")
    return path


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _file_record(root: Path, value: str | Path, *, label: str) -> dict[str, Any]:
    path = _inside(root, value, label=label)
    if not path.is_file():
        raise M3SourceMetadataInventoryError(f"{label} is unavailable: {path}")
    return {
        "path": _relative(root, path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise M3SourceMetadataInventoryError(f"Cannot read {label}: {path}") from error
    if not isinstance(payload, dict):
        raise M3SourceMetadataInventoryError(f"{label} must be a JSON object")
    return payload


def _authenticate_commit(payload: Mapping[str, Any], *, label: str) -> str:
    commit = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    if not isinstance(commit, str) or canonical_sha256(unsigned) != commit:
        raise M3SourceMetadataInventoryError(f"{label} commit is invalid")
    return commit


def _bound_json(
    root: Path,
    record: Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    observed_record = _file_record(root, str(record.get("path", "")), label=label)
    _require(
        observed_record["bytes"] == record.get("bytes")
        and observed_record["sha256"] == record.get("sha256"),
        f"{label} file changed",
    )
    payload = _read_json(root / observed_record["path"], label=label)
    commit = _authenticate_commit(payload, label=label)
    _require(commit == record.get("commit_sha256"), f"{label} commit changed")
    return payload


def _write_exclusive(payload: Mapping[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as error:
        raise M3SourceMetadataInventoryError(
            f"Append-only output already exists: {destination}"
        ) from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _write_raw_page(payload: Mapping[str, Any], destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    if destination.exists():
        _require(
            destination.read_bytes() == encoded,
            f"Raw metadata page changed: {destination}",
        )
    else:
        try:
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            _require(
                destination.read_bytes() == encoded,
                f"Raw metadata page changed: {destination}",
            )
        else:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
    return {
        "bytes": destination.stat().st_size,
        "sha256": sha256_file(destination),
    }


def _validate_assets_excluded_page(page: object) -> dict[str, Any]:
    _require(isinstance(page, dict), "Raw metadata page is not a JSON object")
    payload = dict(page)
    features = payload.get("features")
    _require(isinstance(features, list), "Raw metadata page has no feature list")
    for feature in features:
        _require(isinstance(feature, dict), "Raw metadata feature is invalid")
        _require(
            "assets" not in feature and "links" not in feature,
            "Raw metadata feature exposed an asset or item link",
        )
    return payload


def _validate_amendment(amendment: Mapping[str, Any]) -> None:
    contract = amendment.get("amendment_contract", {})
    _require(
        amendment.get("state")
        == "source_acquisition_amendment_locked_before_new_metadata_or_value_access"
        and amendment.get("protocol_amendment_locked") is True
        and amendment.get("execution_authorized") is False
        and amendment.get("next_safe_stage")
        == "separately_authorize_target_blind_source_metadata_inventory_extension",
        "Source acquisition amendment is not ready for separate authorization",
    )
    retained = contract.get("retained_inventory", {})
    expanded = contract.get("expanded_inventory", {})
    query = contract.get("query_contract", {})
    _require(
        tuple(retained) == RETAINED_CITY_IDS
        and tuple(expanded) == QUERY_CITY_IDS
        and all(
            row.get("start_date") == QUERY_START.isoformat()
            and row.get("end_date") == QUERY_END.isoformat()
            and row.get("include_all_qualifying_overpasses") is True
            for row in expanded.values()
        )
        and query.get("warm_season_months") == list(WARM_MONTHS)
        and query.get("maximum_physical_overpass_span_minutes")
        == MAXIMUM_OVERPASS_SPAN_MINUTES
        and float(query.get("minimum_city_union_coverage_fraction"))
        == MINIMUM_UNION_COVERAGE
        and query.get("include_every_qualifying_physical_overpass") is True
        and query.get("exclude_assets_from_metadata_query") is True
        and query.get("persist_asset_hrefs") is False
        and query.get("qa_dependent_date_or_scene_selection") is False,
        "Source acquisition amendment query contract changed",
    )


def build_source_metadata_inventory_authorization(
    project_root: str | Path,
    amendment_path: str | Path = AMENDMENT_PATH,
) -> dict[str, Any]:
    """Build the narrow metadata-query permit without performing the query."""

    root = Path(project_root).resolve()
    amendment_file = _inside(root, amendment_path, label="Source acquisition amendment")
    amendment = authenticate_m3_source_acquisition_amendment(root, amendment_file)
    _validate_amendment(amendment)
    code_identity = {
        "metadata_inventory_module": _file_record(
            root, MODULE_PATH, label="Metadata inventory module"
        ),
        "metadata_inventory_script": _file_record(
            root, SCRIPT_PATH, label="Metadata inventory script"
        ),
    }
    code_identity.update(
        {
            path: _file_record(root, path, label=f"Query implementation {path}")
            for path in QUERY_IMPLEMENTATION_PATHS
        }
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "source_metadata_inventory_authorized",
        "source_acquisition_amendment": {
            **_file_record(root, amendment_file, label="Source acquisition amendment"),
            "commit_sha256": amendment["commit_sha256"],
        },
        "source_city_ids": list(SOURCE_CITY_IDS),
        "retained_city_ids": list(RETAINED_CITY_IDS),
        "metadata_query_city_ids": list(QUERY_CITY_IDS),
        "query_contract": amendment["amendment_contract"]["query_contract"],
        "expanded_inventory": amendment["amendment_contract"]["expanded_inventory"],
        "permissions": dict(AUTHORIZATION_PERMISSIONS),
        "code_identity": code_identity,
        "access_audit": {
            "network_requests_performed_by_authorization": False,
            "landsat_metadata_read_by_authorization": False,
            "landsat_item_assets_or_hrefs_read_by_authorization": False,
            "thermal_qa_predictor_or_target_values_read_by_authorization": False,
            "model_fit_selection_prediction_or_scoring_performed": False,
        },
        "next_safe_stage": "run_exact_houston_chicago_assets_excluded_metadata_query",
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def create_source_metadata_inventory_authorization(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    """Create the append-only metadata authorization once."""

    root = Path(project_root).resolve()
    destination = _inside(root, authorization_path, label="Metadata authorization")
    payload = build_source_metadata_inventory_authorization(root)
    _write_exclusive(payload, destination)
    return authenticate_source_metadata_inventory_authorization(root, destination)


def authenticate_source_metadata_inventory_authorization(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    """Rebuild the permit and require exact equality before any query."""

    root = Path(project_root).resolve()
    path = _inside(root, authorization_path, label="Metadata authorization")
    observed = _read_json(path, label="Metadata authorization")
    _authenticate_commit(observed, label="Metadata authorization")
    expected = build_source_metadata_inventory_authorization(
        root,
        observed.get("source_acquisition_amendment", {}).get("path", ""),
    )
    if observed != expected:
        raise M3SourceMetadataInventoryError(
            "Metadata authorization no longer reproduces exactly"
        )
    return observed


def _query_city_overpasses(
    root: Path,
    city_id: str,
    client: _MetadataClient | None,
) -> tuple[pd.DataFrame, list[dict[str, Any]], list[dict[str, Any]]]:
    _require(city_id in QUERY_CITY_IDS, "Metadata query attempted an unauthorized city")
    plan = load_multicity_plan(root / "configs/multicity/experiment.toml")
    city = next((candidate for candidate in plan.cities if candidate.id == city_id), None)
    _require(city is not None, f"Missing city configuration: {city_id}")
    _geography, boundary, _tracts, _manifest = legacy_inventory._geography(root, city_id)
    active_client = footprints._retrying_session() if client is None else client
    bbox = tuple(float(value) for value in boundary.to_crs("EPSG:4326").total_bounds)
    tables: list[gpd.GeoDataFrame] = []
    pages: list[dict[str, Any]] = []
    query_records: list[dict[str, Any]] = []
    for year in range(QUERY_START.year, QUERY_END.year + 1):
        local_start = date(year, WARM_MONTHS[0], 1)
        local_end = date(year, WARM_MONTHS[-1], 31)
        features, year_pages, query = footprints.fetch_public_stac_metadata(
            active_client,
            api=str(plan.raw["sources"]["landsat_stac_api"]),
            collection=str(plan.raw["target"]["landsat_collection"]),
            bbox_wgs84=bbox,
            datetime_interval=footprints.local_date_interval_to_utc(
                local_start, local_end, city.timezone
            ),
            fields=footprints.LANDSAT_FIELDS,
            properties=footprints.LANDSAT_PROPERTIES,
            page_limit=100,
            query={
                "platform": {"in": list(plan.raw["target"]["sensors"])},
                "landsat:collection_category": {"eq": "T1"},
                "landsat:correction": {"eq": "L2SP"},
            },
        )
        tables.append(
            footprints.build_optical_item_table(
                features,
                source="landsat_wrs",
                collection=str(plan.raw["target"]["landsat_collection"]),
                expected_properties=footprints.LANDSAT_PROPERTIES,
                allowed_platforms=tuple(plan.raw["target"]["sensors"]),
                local_start_date=local_start,
                local_end_date=local_end,
                timezone=city.timezone,
                city_boundary=boundary,
                analysis_crs="EPSG:5070",
            )
        )
        pages.extend({"query_year": year, "page": page} for page in year_pages)
        query_records.append({"city_id": city_id, "year": year, **query})
    _require(bool(tables), f"No query tables produced for {city_id}")
    items = gpd.GeoDataFrame(
        pd.concat(tables, ignore_index=True),
        geometry="geometry",
        crs=tables[0].crs,
    ).sort_values("item_id", kind="stable").reset_index(drop=True)
    _require(
        not items.empty and not items["item_id"].duplicated().any(),
        f"Expanded metadata is empty or duplicated for {city_id}",
    )
    overpasses = legacy_inventory._overpasses(
        items,
        boundary,
        analysis_crs="EPSG:5070",
        maximum_gap_minutes=MAXIMUM_OVERPASS_SPAN_MINUTES,
        minimum_coverage=MINIMUM_UNION_COVERAGE,
    )
    return overpasses, pages, query_records


def _validate_query_records(
    root: Path, records: object
) -> tuple[dict[str, Any], ...]:
    _require(isinstance(records, list), "Metadata query records must be a list")
    plan = load_multicity_plan(root / "configs/multicity/experiment.toml")
    observed: list[dict[str, Any]] = []
    expected_pairs = [
        (city_id, year)
        for city_id in QUERY_CITY_IDS
        for year in range(QUERY_START.year, QUERY_END.year + 1)
    ]
    _require(len(records) == len(expected_pairs), "Metadata query count changed")
    for raw, (city_id, year) in zip(records, expected_pairs, strict=True):
        _require(isinstance(raw, dict), "Metadata query record is invalid")
        record = dict(raw)
        query = record.get("query")
        _require(isinstance(query, dict), "Metadata query body is missing")
        fields = query.get("fields")
        filters = query.get("query")
        city = next(candidate for candidate in plan.cities if candidate.id == city_id)
        expected_interval = footprints.local_date_interval_to_utc(
            date(year, WARM_MONTHS[0], 1),
            date(year, WARM_MONTHS[-1], 31),
            city.timezone,
        )
        bbox = query.get("bbox")
        _require(
            record.get("city_id") == city_id
            and record.get("year") == year
            and record.get("assets_excluded") is True
            and record.get("pagination_exhausted") is True
            and isinstance(record.get("page_count"), int)
            and int(record["page_count"]) >= 1
            and query.get("collections") == ["landsat-c2-l2"]
            and query.get("datetime") == expected_interval
            and query.get("limit") == 100
            and isinstance(bbox, list)
            and len(bbox) == 4
            and all(isinstance(value, (int, float)) for value in bbox)
            and isinstance(fields, dict)
            and fields.get("include") == list(footprints.LANDSAT_FIELDS)
            and fields.get("exclude") == ["assets", "links"]
            and filters
            == {
                "platform": {"in": ["landsat-8", "landsat-9"]},
                "landsat:collection_category": {"eq": "T1"},
                "landsat:correction": {"eq": "L2SP"},
            }
            and record.get("query_sha256") == canonical_sha256(query),
            "Metadata query escaped the assets-excluded Landsat contract",
        )
        observed.append(record)
    return tuple(observed)


def _validate_queried_overpasses(city_id: str, frame: object) -> pd.DataFrame:
    _require(city_id in QUERY_CITY_IDS, "Queried overpass city is unauthorized")
    _require(isinstance(frame, pd.DataFrame) and not frame.empty, "Empty query result")
    required = {
        "overpass_id",
        "platform",
        "local_date",
        "acquired_utc_min",
        "acquired_utc_max",
        "scene_ids",
        "wrs_path_rows",
        "scene_count",
        "union_city_coverage_fraction",
        "ambiguous_local_date",
        "primary_eligible",
        "source_lock_sha256",
    }
    _require(required.issubset(frame.columns), "Queried overpass schema changed")
    normalized = frame.copy()
    _require(
        not normalized["overpass_id"].astype("string").duplicated().any(),
        "Queried overpass IDs are duplicated",
    )
    dates = pd.to_datetime(normalized["local_date"], errors="raise").dt.date
    expected_primary = (
        pd.to_numeric(normalized["union_city_coverage_fraction"], errors="raise")
        >= MINIMUM_UNION_COVERAGE
    ) & ~normalized["ambiguous_local_date"].astype(bool)
    _require(
        normalized["primary_eligible"].astype(bool).equals(expected_primary)
        and all(QUERY_START <= value <= QUERY_END for value in dates)
        and all(value.month in WARM_MONTHS for value in dates)
        and normalized.loc[expected_primary, "local_date"].duplicated().sum() == 0,
        "Queried overpasses violate the fixed coverage/date/grouping contract",
    )
    source_locks = normalized["source_lock_sha256"].astype("string")
    _require(
        source_locks.str.fullmatch(r"[0-9a-f]{64}", na=False).all(),
        "Queried overpass source lock is invalid",
    )
    return normalized


def _record_path_is_current(root: Path, record: Mapping[str, Any], *, label: str) -> Path:
    path = _inside(root, str(record.get("path", "")), label=label)
    _require(
        path.is_file()
        and path.stat().st_size == record.get("bytes")
        and sha256_file(path) == record.get("sha256"),
        f"{label} changed",
    )
    return path


def _retained_overpasses(
    root: Path,
    authorization: Mapping[str, Any],
) -> tuple[dict[str, pd.DataFrame], dict[str, Any], dict[str, Any]]:
    amendment = authenticate_m3_source_acquisition_amendment(
        root,
        authorization.get("source_acquisition_amendment", {}).get("path", ""),
    )
    anchors = amendment.get("input_anchors", {})
    inventory = _bound_json(
        root,
        anchors.get("previous_predictor_inventory", {}),
        label="Previous predictor inventory",
    )
    plan = _bound_json(
        root,
        anchors.get("previous_target_plan", {}),
        label="Previous target plan",
    )
    output_tables = inventory.get("output_tables", {})
    frames: dict[str, pd.DataFrame] = {}
    expected_retained = {
        "los_angeles_ca": (90, 177),
        "phoenix_az": (22, 44),
    }
    for city_id, (expected_count, expected_scene_count) in expected_retained.items():
        record = output_tables.get(f"{city_id}/overpasses", {})
        path = _record_path_is_current(root, record, label=f"{city_id} retained overpasses")
        frame = pd.read_parquet(path)
        primary = frame.loc[frame["primary_eligible"].eq(True)].copy()
        observed_scene_count = sum(
            len(str(value).split("|")) for value in primary["scene_ids"]
        )
        _require(
            len(primary) == expected_count
            and observed_scene_count == expected_scene_count
            and not primary["local_date"].duplicated().any(),
            f"{city_id} retained overpasses changed",
        )
        frames[city_id] = primary
    return frames, inventory, plan


def _normalize_overpass_rows(
    frames: Mapping[str, pd.DataFrame],
    target_plan: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for city_id in SOURCE_CITY_IDS:
        frame = frames.get(city_id)
        _require(frame is not None and not frame.empty, f"Missing overpasses for {city_id}")
        city_plan = target_plan.get("cities", {}).get(city_id, {})
        grid_sha256 = city_plan.get("target_grid_sha256")
        context_locks = city_plan.get("context_locks")
        _require(
            isinstance(grid_sha256, str) and isinstance(context_locks, dict),
            f"Missing target context for {city_id}",
        )
        context_commit = canonical_sha256(context_locks)
        for raw in frame.sort_values(["local_date", "overpass_id"], kind="stable").to_dict(
            "records"
        ):
            scene_ids = str(raw["scene_ids"]).split("|")
            wrs_path_rows = str(raw["wrs_path_rows"]).split("|")
            _require(
                bool(scene_ids)
                and len(scene_ids) == len(set(scene_ids))
                and bool(raw.get("primary_eligible"))
                and not bool(raw.get("ambiguous_local_date"))
                and float(raw["union_city_coverage_fraction"])
                >= MINIMUM_UNION_COVERAGE,
                f"Non-qualifying overpass entered expanded inventory: {city_id}",
            )
            row: dict[str, Any] = {
                "city_id": city_id,
                "target_date": str(raw["local_date"]),
                "overpass_id": str(raw["overpass_id"]),
                "platform": str(raw["platform"]),
                "scene_ids": scene_ids,
                "wrs_path_rows": wrs_path_rows,
                "acquired_utc_min": str(raw["acquired_utc_min"]),
                "acquired_utc_max": str(raw["acquired_utc_max"]),
                "union_city_coverage_fraction": float(
                    raw["union_city_coverage_fraction"]
                ),
                "source_lock_sha256": str(raw["source_lock_sha256"]),
                "grid_sha256": grid_sha256,
                "target_context_commit_sha256": context_commit,
                "inventory_mode": (
                    "retained_authenticated_existing"
                    if city_id in RETAINED_CITY_IDS
                    else "expanded_complete_fixed_window_query"
                ),
            }
            row["relationship_sha256"] = canonical_sha256(row)
            rows.append(row)
    rows.sort(
        key=lambda row: (
            SOURCE_CITY_IDS.index(str(row["city_id"])),
            str(row["target_date"]),
            str(row["overpass_id"]),
        )
    )
    for ordinal, row in enumerate(rows, start=1):
        row["ordinal"] = ordinal
    return rows


def _validate_inventory_overpasses(rows: object) -> tuple[dict[str, Any], ...]:
    _require(isinstance(rows, list) and bool(rows), "Expanded inventory has no overpasses")
    observed: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for ordinal, raw in enumerate(rows, start=1):
        _require(isinstance(raw, dict), "Expanded inventory overpass is invalid")
        row = dict(raw)
        _require(
            set(row)
            == {
                "city_id",
                "target_date",
                "overpass_id",
                "platform",
                "scene_ids",
                "wrs_path_rows",
                "acquired_utc_min",
                "acquired_utc_max",
                "union_city_coverage_fraction",
                "source_lock_sha256",
                "grid_sha256",
                "target_context_commit_sha256",
                "inventory_mode",
                "relationship_sha256",
                "ordinal",
            },
            "Expanded inventory overpass fields changed",
        )
        city_id = row.get("city_id")
        key = (str(city_id), str(row.get("overpass_id")))
        acquired_min = datetime.fromisoformat(
            str(row.get("acquired_utc_min")).replace("Z", "+00:00")
        )
        acquired_max = datetime.fromisoformat(
            str(row.get("acquired_utc_max")).replace("Z", "+00:00")
        )
        _require(
            row.get("ordinal") == ordinal
            and city_id in SOURCE_CITY_IDS
            and city_id not in BLIND_TEST_CITY_IDS
            and key not in seen
            and isinstance(row.get("scene_ids"), list)
            and bool(row["scene_ids"])
            and len(row["scene_ids"]) == len(set(row["scene_ids"]))
            and isinstance(row.get("wrs_path_rows"), list)
            and bool(row["wrs_path_rows"])
            and row.get("platform") in {"landsat-8", "landsat-9"}
            and 0
            <= (acquired_max - acquired_min).total_seconds()
            <= MAXIMUM_OVERPASS_SPAN_MINUTES * 60
            and float(row.get("union_city_coverage_fraction", 0.0))
            >= MINIMUM_UNION_COVERAGE
            and isinstance(row.get("source_lock_sha256"), str)
            and len(row["source_lock_sha256"]) == 64
            and isinstance(row.get("grid_sha256"), str)
            and len(row["grid_sha256"]) == 64
            and isinstance(row.get("target_context_commit_sha256"), str)
            and len(row["target_context_commit_sha256"]) == 64
            and row.get("inventory_mode")
            == (
                "retained_authenticated_existing"
                if city_id in RETAINED_CITY_IDS
                else "expanded_complete_fixed_window_query"
            )
            and row.get("relationship_sha256")
            == canonical_sha256(
                {
                    key: value
                    for key, value in row.items()
                    if key not in {"ordinal", "relationship_sha256"}
                }
            ),
            "Expanded inventory overpass contract changed",
        )
        target_date = date.fromisoformat(str(row.get("target_date")))
        if city_id == "los_angeles_ca":
            _require(date(2020, 5, 1) <= target_date <= date(2024, 10, 31), "LA date changed")
        elif city_id == "phoenix_az":
            _require(date(2025, 5, 1) <= target_date <= date(2025, 10, 31), "Phoenix date changed")
        else:
            _require(
                QUERY_START <= target_date <= QUERY_END
                and target_date.month in WARM_MONTHS,
                "Expanded Houston/Chicago date left its fixed window",
            )
        seen.add(key)
        observed.append(row)
    expected_order = sorted(
        observed,
        key=lambda row: (
            SOURCE_CITY_IDS.index(str(row["city_id"])),
            str(row["target_date"]),
            str(row["overpass_id"]),
        ),
    )
    _require(observed == expected_order, "Expanded inventory overpass order changed")
    return tuple(observed)


def build_expanded_source_inventory(
    project_root: str | Path,
    *,
    authorization_path: str | Path = AUTHORIZATION_PATH,
    inventory_path: str | Path = INVENTORY_PATH,
    raw_root: str | Path = RAW_ROOT,
    client: _MetadataClient | None = None,
    query_runner: QueryRunner | None = None,
) -> dict[str, Any]:
    """Run the exact authorized metadata query and write one append-only inventory."""

    root = Path(project_root).resolve()
    authorization = authenticate_source_metadata_inventory_authorization(
        root, authorization_path
    )
    _require(
        authorization.get("permissions") == AUTHORIZATION_PERMISSIONS,
        "Metadata authorization permissions changed",
    )
    retained, _previous_inventory, target_plan = _retained_overpasses(root, authorization)
    runner = _query_city_overpasses if query_runner is None else query_runner
    frames = dict(retained)
    raw_base = _inside(root, raw_root, label="Raw metadata root")
    raw_files: list[dict[str, Any]] = []
    pending_pages: list[tuple[str, int, int, Path, dict[str, Any]]] = []
    queries: list[dict[str, Any]] = []
    for city_id in QUERY_CITY_IDS:
        frame, pages, city_queries = runner(root, city_id, client)
        frame = _validate_queried_overpasses(city_id, frame)
        frames[city_id] = frame.loc[frame["primary_eligible"].eq(True)].copy()
        _require(not frames[city_id].empty, f"No qualifying overpasses for {city_id}")
        queries.extend(city_queries)
        page_numbers: dict[int, int] = {}
        for wrapper in pages:
            _require(
                isinstance(wrapper, dict)
                and set(wrapper) == {"query_year", "page"}
                and isinstance(wrapper.get("query_year"), int)
                and QUERY_START.year
                <= int(wrapper["query_year"])
                <= QUERY_END.year,
                "Raw metadata page query-year wrapper changed",
            )
            year = int(wrapper["query_year"])
            page_number = page_numbers.get(year, 0) + 1
            page_numbers[year] = page_number
            page = _validate_assets_excluded_page(wrapper["page"])
            page_path = (
                raw_base
                / city_id
                / str(year)
                / f"stac_page_{page_number:03d}.json"
            )
            pending_pages.append((city_id, year, page_number, page_path, page))
    validated_queries = _validate_query_records(root, queries)
    observed_page_counts = {
        (city_id, year): sum(
            page_city == city_id and page_year == year
            for page_city, page_year, _number, _path, _page in pending_pages
        )
        for city_id in QUERY_CITY_IDS
        for year in range(QUERY_START.year, QUERY_END.year + 1)
    }
    _require(
        all(
            observed_page_counts[(str(record["city_id"]), int(record["year"]))]
            == record["page_count"]
            for record in validated_queries
        ),
        "Raw metadata page counts do not match exhausted query records",
    )
    for city_id, year, page_number, page_path, page in pending_pages:
        record = _write_raw_page(page, page_path)
        record.update(
            {
                "city_id": city_id,
                "query_year": year,
                "page_number": page_number,
                "path": _relative(root, page_path),
            }
        )
        raw_files.append(record)
    rows = _normalize_overpass_rows(frames, target_plan)
    counts = {
        city_id: sum(row["city_id"] == city_id for row in rows)
        for city_id in SOURCE_CITY_IDS
    }
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "expanded_source_inventory_complete",
        "source_acquisition_amendment_commit_sha256": authorization[
            "source_acquisition_amendment"
        ]["commit_sha256"],
        "metadata_inventory_authorization": {
            **_file_record(root, authorization_path, label="Metadata authorization"),
            "commit_sha256": authorization["commit_sha256"],
        },
        "source_city_ids": list(SOURCE_CITY_IDS),
        "retained_city_ids": list(RETAINED_CITY_IDS),
        "queried_city_ids": list(QUERY_CITY_IDS),
        "query_contract": authorization["query_contract"],
        "query_records": list(validated_queries),
        "raw_metadata_files": raw_files,
        "overpass_count": len(rows),
        "overpass_count_by_city": counts,
        "overpasses": rows,
        "access_audit": dict(INVENTORY_ACCESS_AUDIT),
        "blind_test_asset_or_value_accessed": False,
        "next_safe_stage": "separately_authorize_source_only_cache_prefetch_and_offline_qa_rebuild",
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    destination = _inside(root, inventory_path, label="Expanded source inventory")
    _write_exclusive(payload, destination)
    return authenticate_expanded_source_inventory(root, destination)


def authenticate_expanded_source_inventory(
    project_root: str | Path,
    inventory_path: str | Path = INVENTORY_PATH,
) -> dict[str, Any]:
    """Authenticate a completed inventory without repeating its network query."""

    root = Path(project_root).resolve()
    path = _inside(root, inventory_path, label="Expanded source inventory")
    payload = _read_json(path, label="Expanded source inventory")
    _authenticate_commit(payload, label="Expanded source inventory")
    _require(
        payload.get("state") == "expanded_source_inventory_complete"
        and tuple(payload.get("source_city_ids", ())) == SOURCE_CITY_IDS
        and tuple(payload.get("retained_city_ids", ())) == RETAINED_CITY_IDS
        and tuple(payload.get("queried_city_ids", ())) == QUERY_CITY_IDS
        and payload.get("blind_test_asset_or_value_accessed") is False,
        "Expanded source inventory identity changed",
    )
    authorization_record = payload.get("metadata_inventory_authorization", {})
    authorization = authenticate_source_metadata_inventory_authorization(
        root, str(authorization_record.get("path", ""))
    )
    observed_authorization = _file_record(
        root,
        str(authorization_record.get("path", "")),
        label="Metadata authorization",
    )
    _require(
        observed_authorization["sha256"] == authorization_record.get("sha256")
        and observed_authorization["bytes"] == authorization_record.get("bytes")
        and authorization["commit_sha256"] == authorization_record.get("commit_sha256")
        and payload.get("source_acquisition_amendment_commit_sha256")
        == authorization["source_acquisition_amendment"]["commit_sha256"],
        "Expanded inventory authorization binding changed",
    )
    _require(
        payload.get("query_contract") == authorization["query_contract"],
        "Expanded inventory query contract changed",
    )
    query_records = _validate_query_records(root, payload.get("query_records"))
    raw_records = payload.get("raw_metadata_files")
    _require(isinstance(raw_records, list), "Raw metadata file records changed")
    expected_pages = [
        (str(record["city_id"]), int(record["year"]), page_number)
        for record in query_records
        for page_number in range(1, int(record["page_count"]) + 1)
    ]
    _require(
        len(raw_records) == len(expected_pages),
        "Raw metadata page count changed",
    )
    for raw, (city_id, year, page_number) in zip(
        raw_records, expected_pages, strict=True
    ):
        _require(
            isinstance(raw, dict)
            and raw.get("city_id") == city_id
            and raw.get("query_year") == year
            and raw.get("page_number") == page_number,
            "Raw metadata page ordering changed",
        )
        raw_path = _record_path_is_current(
            root, raw, label="Raw assets-excluded metadata page"
        )
        _validate_assets_excluded_page(
            _read_json(raw_path, label="Raw assets-excluded metadata page")
        )
    rows = _validate_inventory_overpasses(payload.get("overpasses"))
    _require(payload.get("overpass_count") == len(rows), "Overpass count changed")
    observed_counts = {
        city_id: sum(row["city_id"] == city_id for row in rows)
        for city_id in SOURCE_CITY_IDS
    }
    _require(
        payload.get("overpass_count_by_city") == observed_counts
        and observed_counts["los_angeles_ca"] == 90
        and observed_counts["phoenix_az"] == 22,
        "Expanded inventory city counts changed",
    )
    _require(
        payload.get("access_audit") == INVENTORY_ACCESS_AUDIT,
        "Expanded inventory access boundary changed",
    )
    return payload
