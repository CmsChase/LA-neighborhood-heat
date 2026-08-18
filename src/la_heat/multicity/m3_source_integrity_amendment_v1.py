"""Outcome-blind integrity amendment and logical source overlay for M3.

This metadata-only transition never edits the locked source inventory, physical
scene plan, cache, queue, QA outputs, targets, predictors, or models.  It
applies one symmetric byte-integrity availability rule to the independently
committed repair incident, recomputes the one retained Houston scene footprint
from the already-bound assets-excluded metadata, and writes append-only
amendment and logical-overlay manifests.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any, Final

import geopandas as gpd

from la_heat.multicity import portable_predictor_inventory as legacy_inventory
from la_heat.multicity import source_footprints
from la_heat.multicity.config import load_multicity_plan
from la_heat.multicity.m3_development_protocol_lock import (
    authenticate_m3_development_protocol_lock,
)
from la_heat.multicity.m3_source_acquisition_amendment import (
    authenticate_m3_source_acquisition_amendment,
)
from la_heat.multicity.m3_source_asset_cache import PLAN_FILENAME, load_scene_plan
from la_heat.multicity.m3_source_asset_repair_v1 import (
    INCIDENT_PATH,
    authenticate_source_asset_repair_incident,
)
from la_heat.multicity.m3_source_development_runtime import (
    BLIND_CITY_IDS,
    SOURCE_CITY_IDS,
    RunnerSettings,
    authenticate_expanded_inventory,
    load_runner_settings,
)
from la_heat.provenance import canonical_sha256, sha256_file

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "m3-source-integrity-amendment-v1"
AMENDMENT_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_SOURCE_ASSET_INTEGRITY_AVAILABILITY_AMENDMENT_V1.json"
)
OVERLAY_PATH: Final = Path(
    "manifests/multicity/next_experiment/source_development/"
    "M3_SOURCE_INTEGRITY_LOGICAL_OVERLAY_V1.json"
)

HOUSTON_CITY: Final = "houston_tx"
HOUSTON_OVERPASS: Final = "landsat-9_20220514T165014Z"
HOUSTON_RETAINED_SCENE: Final = "LC09_L2SP_025039_20220514_02_T1"
HOUSTON_UNAVAILABLE_SCENE: Final = "LC09_L2SP_025040_20220514_02_T1"
CHICAGO_CITY: Final = "chicago_il"
CHICAGO_OVERPASS: Final = "landsat-8_20220727T163528Z"
CHICAGO_UNAVAILABLE_SCENE: Final = "LC08_L2SP_023031_20220727_02_T1"
MINIMUM_UNION_COVERAGE: Final = 0.98

EXPECTED_INCIDENT_ASSETS: Final = (
    (CHICAGO_CITY, CHICAGO_UNAVAILABLE_SCENE, "lwir11"),
    (CHICAGO_CITY, CHICAGO_UNAVAILABLE_SCENE, "qa_radsat"),
    (HOUSTON_CITY, HOUSTON_UNAVAILABLE_SCENE, "qa_radsat"),
)
EXPECTED_PARENT_COUNTS: Final = {
    "los_angeles_ca": {"overpasses": 90, "city_dates": 90, "scene_references": 177},
    "phoenix_az": {"overpasses": 22, "city_dates": 22, "scene_references": 44},
    "houston_tx": {"overpasses": 102, "city_dates": 102, "scene_references": 200},
    "chicago_il": {"overpasses": 104, "city_dates": 104, "scene_references": 104},
}
EXPECTED_LOGICAL_COUNTS: Final = {
    "los_angeles_ca": {"overpasses": 90, "city_dates": 90, "scene_references": 177},
    "phoenix_az": {"overpasses": 22, "city_dates": 22, "scene_references": 44},
    "houston_tx": {"overpasses": 102, "city_dates": 102, "scene_references": 199},
    "chicago_il": {"overpasses": 103, "city_dates": 103, "scene_references": 103},
}
CODE_PATHS: Final = (
    "configs/multicity/experiment.toml",
    "src/la_heat/multicity/m3_source_integrity_amendment_v1.py",
    "scripts/stage_m3_source_integrity_amendment_v1.py",
    "src/la_heat/multicity/config.py",
    "src/la_heat/multicity/m3_source_metadata_inventory_v1.py",
    "src/la_heat/multicity/portable_predictor_inventory.py",
    "src/la_heat/multicity/source_footprints.py",
)


class M3SourceIntegrityAmendmentError(RuntimeError):
    """Raised when the narrow integrity-only source transition drifts."""


def _inside(root: Path, value: str | Path, *, label: str) -> Path:
    raw = Path(value)
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not path.is_relative_to(root):
        raise M3SourceIntegrityAmendmentError(f"{label} must stay inside the project.")
    return path


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def _committed(payload: Mapping[str, Any]) -> bool:
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    return isinstance(recorded, str) and recorded == canonical_sha256(unsigned)


def _read_committed(path: Path, *, state: str, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise M3SourceIntegrityAmendmentError(f"Cannot read {label}: {path}") from error
    if not isinstance(payload, dict) or payload.get("state") != state or not _committed(payload):
        raise M3SourceIntegrityAmendmentError(f"{label} state or commit is invalid.")
    return payload


def _write_exclusive(payload: Mapping[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(dict(payload), indent=2, ensure_ascii=False) + "\n").encode()
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as error:
        raise M3SourceIntegrityAmendmentError(
            f"Append-only source-integrity manifest already exists: {destination}"
        ) from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _file_record(root: Path, path: Path, *, commit: object | None = None) -> dict[str, Any]:
    resolved = _inside(root, path, label="Bound input")
    if not resolved.is_file() or resolved.is_symlink():
        raise M3SourceIntegrityAmendmentError(f"Bound input is missing: {resolved}")
    record: dict[str, Any] = {
        "path": _relative(root, resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }
    if commit is not None:
        record["commit_sha256"] = str(commit)
    return record


def _record_path(root: Path, record: Mapping[str, Any], *, label: str) -> Path:
    path = _inside(root, str(record.get("path", "")), label=label)
    if (
        not path.is_file()
        or path.is_symlink()
        or path.stat().st_size != record.get("bytes")
        or sha256_file(path) != record.get("sha256")
    ):
        raise M3SourceIntegrityAmendmentError(f"{label} file record changed.")
    return path


def _authenticate_parents(
    project_root: str | Path,
    incident_path: str | Path,
) -> tuple[
    RunnerSettings,
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    Path,
]:
    root = Path(project_root).resolve()
    settings = load_runner_settings(root)
    protocol = authenticate_m3_development_protocol_lock(root, settings.protocol_lock)
    acquisition = authenticate_m3_source_acquisition_amendment(root, settings.amendment)
    inventory = authenticate_expanded_inventory(settings, acquisition)
    incident_file = _inside(root, incident_path, label="Repair incident")
    incident = authenticate_source_asset_repair_incident(root, incident_file)
    physical_plan = load_scene_plan(settings.cache_root)
    query = acquisition.get("amendment_contract", {}).get("query_contract", {})
    expected_bindings = {
        "m3_protocol_lock": protocol.get("commit_sha256"),
        "source_acquisition_amendment": acquisition.get("commit_sha256"),
        "expanded_source_inventory": inventory.get("commit_sha256"),
    }
    plan_bindings = physical_plan.get("bindings")
    if (
        acquisition.get("protocol_amendment_locked") is not True
        or query.get("include_every_qualifying_physical_overpass") is not True
        or query.get("date_or_scene_reselection_after_query") is not False
        or inventory.get("source_city_ids") != list(SOURCE_CITY_IDS)
        or incident.get("scene_plan_commit_sha256") != physical_plan.get("commit_sha256")
        or incident.get("expanded_source_inventory_commit_sha256")
        != inventory.get("commit_sha256")
        or not isinstance(plan_bindings, Mapping)
        or any(plan_bindings.get(key) != value for key, value in expected_bindings.items())
    ):
        raise M3SourceIntegrityAmendmentError("Integrity parents are detached or unlocked.")
    if any(city in json.dumps(incident) for city in BLIND_CITY_IDS):
        raise M3SourceIntegrityAmendmentError("Repair incident references a blind city.")
    return settings, protocol, acquisition, inventory, incident, physical_plan, incident_file


def _validate_incident(incident: Mapping[str, Any]) -> list[str]:
    assets = incident.get("affected_assets")
    if not isinstance(assets, list):
        raise M3SourceIntegrityAmendmentError("Repair incident has no affected assets.")
    observed: list[tuple[str, str, str]] = []
    for row in assets:
        if not isinstance(row, Mapping):
            raise M3SourceIntegrityAmendmentError("Repair incident asset is invalid.")
        fingerprint = row.get("bad_blob_safe_fingerprint")
        absence = row.get("cache_absence")
        if (
            not isinstance(fingerprint, Mapping)
            or fingerprint.get("classification") != "html_error_payload_not_tiff"
            or fingerprint.get("first_four_bytes_hex") != "3c21444f"
            or not isinstance(absence, Mapping)
            or absence.get("content_commit_present") is not False
            or absence.get("output_present") is not False
        ):
            raise M3SourceIntegrityAmendmentError("Incident lacks byte-integrity evidence.")
        observed.append((str(row.get("city_id")), str(row.get("scene_id")), str(row.get("asset"))))
    if tuple(observed) != EXPECTED_INCIDENT_ASSETS:
        raise M3SourceIntegrityAmendmentError("Incident affected-asset scope changed.")
    return [CHICAGO_UNAVAILABLE_SCENE, HOUSTON_UNAVAILABLE_SCENE]


def _find_overpass(
    inventory: Mapping[str, Any], city_id: str, overpass_id: str
) -> dict[str, Any]:
    matches = [
        dict(row)
        for row in inventory.get("overpasses", [])
        if isinstance(row, Mapping)
        and row.get("city_id") == city_id
        and row.get("overpass_id") == overpass_id
    ]
    if len(matches) != 1:
        raise M3SourceIntegrityAmendmentError(f"Expected one frozen overpass: {overpass_id}")
    return matches[0]


def _geography_boundary(
    root: Path, city_id: str
) -> tuple[gpd.GeoDataFrame, dict[str, Any], dict[str, Any]]:
    manifest_path = root / (
        f"manifests/multicity/cities/{city_id}/geography/GEOGRAPHY_CONTRACT_V1.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or not _committed(manifest)
        or manifest.get("city", {}).get("id") != city_id
        or manifest.get("city", {}).get("target_values_status") != "sealed"
        or manifest.get("access_contract", {}).get("external_target_or_qa_values_read")
        is not False
    ):
        raise M3SourceIntegrityAmendmentError("Public source-city geography changed.")
    boundary_record = manifest.get("output_tables", {}).get("city_boundary")
    if not isinstance(boundary_record, Mapping):
        raise M3SourceIntegrityAmendmentError("Geography has no city-boundary record.")
    boundary_path = _record_path(root, boundary_record, label="City boundary")
    boundary = gpd.read_parquet(boundary_path)
    if len(boundary) != 1 or boundary.crs is None or boundary.empty:
        raise M3SourceIntegrityAmendmentError("City boundary is invalid.")
    return (
        boundary,
        _file_record(root, manifest_path, commit=manifest["commit_sha256"]),
        dict(boundary_record),
    )


def _scene_feature(
    root: Path,
    inventory: Mapping[str, Any],
    *,
    city_id: str,
    year: int,
    scene_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for raw_record in inventory.get("raw_metadata_files", []):
        if (
            not isinstance(raw_record, Mapping)
            or raw_record.get("city_id") != city_id
            or raw_record.get("query_year") != year
        ):
            continue
        path = _record_path(root, raw_record, label="Assets-excluded raw metadata")
        page = json.loads(path.read_text(encoding="utf-8"))
        features = page.get("features") if isinstance(page, Mapping) else None
        if not isinstance(features, list):
            raise M3SourceIntegrityAmendmentError("Raw metadata page is invalid.")
        for feature in features:
            if isinstance(feature, dict) and feature.get("id") == scene_id:
                if "assets" in feature or "links" in feature:
                    raise M3SourceIntegrityAmendmentError(
                        "Source metadata unexpectedly contains asset locations."
                    )
                matches.append((feature, _file_record(root, path)))
    if len(matches) != 1:
        raise M3SourceIntegrityAmendmentError("Retained scene metadata is not unique.")
    return matches[0]


def _houston_coverage_evidence(
    root: Path,
    inventory: Mapping[str, Any],
) -> dict[str, Any]:
    feature, raw_page = _scene_feature(
        root,
        inventory,
        city_id=HOUSTON_CITY,
        year=2022,
        scene_id=HOUSTON_RETAINED_SCENE,
    )
    boundary, geography_manifest, boundary_record = _geography_boundary(root, HOUSTON_CITY)
    plan = load_multicity_plan(root / "configs/multicity/experiment.toml")
    city = next((item for item in plan.cities if item.id == HOUSTON_CITY), None)
    if city is None:
        raise M3SourceIntegrityAmendmentError("Houston city configuration is missing.")
    items = source_footprints.build_optical_item_table(
        [feature],
        source="landsat_wrs",
        collection=str(plan.raw["target"]["landsat_collection"]),
        expected_properties=source_footprints.LANDSAT_PROPERTIES,
        allowed_platforms=tuple(plan.raw["target"]["sensors"]),
        local_start_date=date(2022, 5, 1),
        local_end_date=date(2022, 10, 31),
        timezone=city.timezone,
        city_boundary=boundary,
        analysis_crs="EPSG:5070",
    )
    overpasses = legacy_inventory._overpasses(
        items,
        boundary,
        analysis_crs="EPSG:5070",
        maximum_gap_minutes=15,
        minimum_coverage=MINIMUM_UNION_COVERAGE,
    )
    if len(overpasses) != 1:
        raise M3SourceIntegrityAmendmentError("Retained scene did not form one overpass.")
    row = overpasses.iloc[0]
    coverage = float(row["union_city_coverage_fraction"])
    if (
        str(row["overpass_id"]) != HOUSTON_OVERPASS
        or str(row["scene_ids"]) != HOUSTON_RETAINED_SCENE
        or coverage < MINIMUM_UNION_COVERAGE
        or not bool(row["primary_eligible"])
    ):
        raise M3SourceIntegrityAmendmentError("Houston retained scene fails the frozen gate.")
    return {
        "retained_scene_id": HOUSTON_RETAINED_SCENE,
        "overpass_id": HOUSTON_OVERPASS,
        "platform": str(row["platform"]),
        "target_date": str(row["local_date"]),
        "wrs_path_rows": str(row["wrs_path_rows"]).split("|"),
        "acquired_utc_min": str(row["acquired_utc_min"]),
        "acquired_utc_max": str(row["acquired_utc_max"]),
        "union_city_coverage_fraction": coverage,
        "minimum_required_fraction": MINIMUM_UNION_COVERAGE,
        "passes_gate": True,
        "logical_source_lock_sha256": str(row["source_lock_sha256"]),
        "feature_geometry_sha256": canonical_sha256(feature.get("geometry")),
        "assets_excluded_raw_metadata_page": raw_page,
        "public_geography_manifest": geography_manifest,
        "public_city_boundary": boundary_record,
        "analysis_crs": "EPSG:5070",
        "grouping_implementation": (
            "source_footprints.build_optical_item_table_then_"
            "portable_predictor_inventory._overpasses"
        ),
    }


def _parent_counts(inventory: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    rows = inventory.get("overpasses")
    if not isinstance(rows, list):
        raise M3SourceIntegrityAmendmentError("Expanded inventory has no overpasses.")
    counts: dict[str, dict[str, int]] = {}
    for city_id in SOURCE_CITY_IDS:
        city_rows = [
            row
            for row in rows
            if isinstance(row, Mapping) and row.get("city_id") == city_id
        ]
        counts[city_id] = {
            "overpasses": len(city_rows),
            "city_dates": len({str(row.get("target_date")) for row in city_rows}),
            "scene_references": sum(len(row.get("scene_ids", [])) for row in city_rows),
        }
    if counts != EXPECTED_PARENT_COUNTS:
        raise M3SourceIntegrityAmendmentError("Parent source counts changed.")
    return counts


def _build_amendment(
    project_root: str | Path,
    *,
    incident_path: str | Path,
    overlay_path: str | Path,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    (
        settings,
        protocol,
        acquisition,
        inventory,
        incident,
        physical_plan,
        incident_file,
    ) = _authenticate_parents(root, incident_path)
    unavailable_scenes = _validate_incident(incident)
    parent_counts = _parent_counts(inventory)
    houston = _find_overpass(inventory, HOUSTON_CITY, HOUSTON_OVERPASS)
    chicago = _find_overpass(inventory, CHICAGO_CITY, CHICAGO_OVERPASS)
    if (
        houston.get("ordinal") != 133
        or houston.get("scene_ids")
        != [HOUSTON_RETAINED_SCENE, HOUSTON_UNAVAILABLE_SCENE]
        or chicago.get("ordinal") != 244
        or chicago.get("scene_ids") != [CHICAGO_UNAVAILABLE_SCENE]
    ):
        raise M3SourceIntegrityAmendmentError("Affected overpass membership changed.")
    coverage = _houston_coverage_evidence(root, inventory)
    destination = _inside(root, overlay_path, label="Logical overlay")
    code_identity = {relative: _file_record(root, root / relative) for relative in CODE_PATHS}
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "source_asset_integrity_availability_amendment_locked",
        "amendment_id": "m3-source-asset-integrity-availability-v1",
        "parent_commits": {
            "m3_development_protocol_lock": protocol["commit_sha256"],
            "source_acquisition_amendment": acquisition["commit_sha256"],
            "expanded_source_inventory": inventory["commit_sha256"],
            "source_asset_repair_incident": incident["commit_sha256"],
            "physical_scene_plan": physical_plan["commit_sha256"],
            "runtime_task_plan": incident["runtime_task_plan"]["task_plan_sha256"],
        },
        "integrity_availability_rule": {
            "scope": "all_scenes_in_the_frozen_four_source_city_inventory_symmetrically",
            "trigger": (
                "a_required_asset_has_an_authenticated_non_tiff_byte_integrity_"
                "failure_and_the_incident_freezes_no_valid_content_commit"
            ),
            "trigger_may_use_qa_target_predictor_or_model_outcomes": False,
            "scene_action": "exclude_only_the_integrity_unavailable_scene",
            "overpass_action": (
                "recompute_union_from_retained_assets_excluded_scene_geometry;_"
                "retain_only_if_at_least_one_scene_and_union_fraction_at_least_0_98"
            ),
            "replacement_or_backfill_date_allowed": False,
            "year_city_or_support_gate_change_allowed": False,
            "minimum_city_union_coverage_fraction": MINIMUM_UNION_COVERAGE,
        },
        "unavailable_scene_ids": unavailable_scenes,
        "adjudications": [
            {
                "city_id": HOUSTON_CITY,
                "overpass_id": HOUSTON_OVERPASS,
                "parent_ordinal": 133,
                "excluded_scene_ids": [HOUSTON_UNAVAILABLE_SCENE],
                "retained_scene_ids": [HOUSTON_RETAINED_SCENE],
                "result": "retain_overpass_and_city_date",
                "coverage_evidence": coverage,
            },
            {
                "city_id": CHICAGO_CITY,
                "overpass_id": CHICAGO_OVERPASS,
                "parent_ordinal": 244,
                "excluded_scene_ids": [CHICAGO_UNAVAILABLE_SCENE],
                "retained_scene_ids": [],
                "result": "exclude_overpass_and_city_date_because_no_scene_remains",
                "parent_union_city_coverage_fraction": chicago[
                    "union_city_coverage_fraction"
                ],
            },
        ],
        "parent_counts_by_city": parent_counts,
        "required_logical_counts_by_city": EXPECTED_LOGICAL_COUNTS,
        "required_logical_totals": {
            "overpasses": 317,
            "city_dates": 317,
            "scene_references": 523,
        },
        "physical_cache_contract": {
            "scene_plan_commit_sha256": physical_plan["commit_sha256"],
            "scene_plan_and_cache_remain_immutable": True,
            "rewrite_copy_or_rebind_existing_content_commits": False,
            "future_logical_execution_must_authenticate_retained_content_against_old_plan": True,
            "source_value_execution_authorized_by_this_amendment": False,
        },
        "inputs": {
            "m3_development_protocol_lock": _file_record(
                root, settings.protocol_lock, commit=protocol["commit_sha256"]
            ),
            "source_acquisition_amendment": _file_record(
                root, settings.amendment, commit=acquisition["commit_sha256"]
            ),
            "expanded_source_inventory": _file_record(
                root, settings.inventory, commit=inventory["commit_sha256"]
            ),
            "source_asset_repair_incident": _file_record(
                root, incident_file, commit=incident["commit_sha256"]
            ),
            "physical_scene_plan": _file_record(
                root,
                settings.cache_root / PLAN_FILENAME,
                commit=physical_plan["commit_sha256"],
            ),
        },
        "logical_overlay_path": _relative(root, destination),
        "permissions": {
            "read_bound_assets_excluded_source_metadata_and_public_boundary": True,
            "create_append_only_integrity_amendment_and_logical_overlay": True,
            "read_blind_city_metadata_assets_predictors_qa_or_targets": False,
            "read_source_qa_values": False,
            "read_source_target_values": False,
            "read_or_build_predictors": False,
            "read_landsat_asset_values": False,
            "mutate_inventory_scene_plan_cache_or_queue": False,
            "fit_select_predict_score_or_publish": False,
        },
        "access_audit": {
            "assets_excluded_source_metadata_read": True,
            "public_source_city_boundary_read": True,
            "landsat_asset_or_qa_values_read": False,
            "target_or_predictor_values_read": False,
            "blind_test_city_accessed": False,
            "model_operation_performed": False,
            "old_physical_cache_or_queue_modified": False,
        },
        "code_identity": code_identity,
        "next_safe_stage": "create_and_authenticate_derived_logical_overlay",
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def build_source_integrity_availability_amendment(
    project_root: str | Path,
    *,
    incident_path: str | Path = INCIDENT_PATH,
    overlay_path: str | Path = OVERLAY_PATH,
) -> dict[str, Any]:
    return _build_amendment(
        project_root,
        incident_path=incident_path,
        overlay_path=overlay_path,
    )


def create_source_integrity_availability_amendment(
    project_root: str | Path,
    amendment_path: str | Path = AMENDMENT_PATH,
    *,
    incident_path: str | Path = INCIDENT_PATH,
    overlay_path: str | Path = OVERLAY_PATH,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    destination = _inside(root, amendment_path, label="Integrity amendment")
    payload = build_source_integrity_availability_amendment(
        root,
        incident_path=incident_path,
        overlay_path=overlay_path,
    )
    _write_exclusive(payload, destination)
    return authenticate_source_integrity_availability_amendment(root, destination)


def authenticate_source_integrity_availability_amendment(
    project_root: str | Path,
    amendment_path: str | Path = AMENDMENT_PATH,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    path = _inside(root, amendment_path, label="Integrity amendment")
    observed = _read_committed(
        path,
        state="source_asset_integrity_availability_amendment_locked",
        label="Source integrity amendment",
    )
    incident_record = observed.get("inputs", {}).get("source_asset_repair_incident", {})
    expected = _build_amendment(
        root,
        incident_path=str(incident_record.get("path", "")),
        overlay_path=str(observed.get("logical_overlay_path", "")),
    )
    if observed != expected:
        raise M3SourceIntegrityAmendmentError("Source integrity amendment drifted.")
    return observed


def _logical_rows(
    inventory: Mapping[str, Any], amendment: Mapping[str, Any]
) -> list[dict[str, Any]]:
    raw_rows = inventory.get("overpasses")
    if not isinstance(raw_rows, list):
        raise M3SourceIntegrityAmendmentError("Expanded inventory rows are missing.")
    coverage = amendment["adjudications"][0]["coverage_evidence"]
    logical: list[dict[str, Any]] = []
    for raw in raw_rows:
        if not isinstance(raw, Mapping):
            raise M3SourceIntegrityAmendmentError("Expanded inventory row is invalid.")
        row = dict(raw)
        unsigned = {
            key: value
            for key, value in row.items()
            if key not in {"ordinal", "relationship_sha256"}
        }
        if row.get("relationship_sha256") != canonical_sha256(unsigned):
            raise M3SourceIntegrityAmendmentError("Parent overpass relationship changed.")
        if row.get("city_id") == CHICAGO_CITY and row.get("overpass_id") == CHICAGO_OVERPASS:
            continue
        if row.get("city_id") == HOUSTON_CITY and row.get("overpass_id") == HOUSTON_OVERPASS:
            row.update(
                {
                    "scene_ids": [HOUSTON_RETAINED_SCENE],
                    "wrs_path_rows": list(coverage["wrs_path_rows"]),
                    "acquired_utc_min": coverage["acquired_utc_min"],
                    "acquired_utc_max": coverage["acquired_utc_max"],
                    "union_city_coverage_fraction": coverage[
                        "union_city_coverage_fraction"
                    ],
                    "source_lock_sha256": coverage["logical_source_lock_sha256"],
                }
            )
            row["relationship_sha256"] = canonical_sha256(
                {
                    key: value
                    for key, value in row.items()
                    if key not in {"ordinal", "relationship_sha256"}
                }
            )
        logical.append(row)
    for ordinal, row in enumerate(logical, start=1):
        row["ordinal"] = ordinal
    return logical


def _logical_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for city_id in SOURCE_CITY_IDS:
        city_rows = [row for row in rows if row.get("city_id") == city_id]
        counts[city_id] = {
            "overpasses": len(city_rows),
            "city_dates": len({str(row.get("target_date")) for row in city_rows}),
            "scene_references": sum(len(row.get("scene_ids", [])) for row in city_rows),
        }
    if counts != EXPECTED_LOGICAL_COUNTS:
        raise M3SourceIntegrityAmendmentError("Logical source counts changed.")
    return counts


def _build_overlay(
    project_root: str | Path,
    *,
    amendment_path: str | Path,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    amendment_file = _inside(root, amendment_path, label="Integrity amendment")
    amendment = authenticate_source_integrity_availability_amendment(root, amendment_file)
    incident_path = amendment["inputs"]["source_asset_repair_incident"]["path"]
    (
        _settings,
        _protocol,
        _acquisition,
        inventory,
        incident,
        physical_plan,
        _incident_file,
    ) = _authenticate_parents(root, incident_path)
    rows = _logical_rows(inventory, amendment)
    counts = _logical_counts(rows)
    unique_scene_ids = {scene for row in rows for scene in row["scene_ids"]}
    totals = {
        "overpasses": len(rows),
        "city_dates": len({(row["city_id"], row["target_date"]) for row in rows}),
        "scene_references": sum(len(row["scene_ids"]) for row in rows),
        "unique_scenes": len(unique_scene_ids),
    }
    if totals != {
        "overpasses": 317,
        "city_dates": 317,
        "scene_references": 523,
        "unique_scenes": 523,
    }:
        raise M3SourceIntegrityAmendmentError("Logical source totals changed.")
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "source_integrity_logical_overlay_complete",
        "source_integrity_amendment_commit_sha256": amendment["commit_sha256"],
        "parent_expanded_source_inventory_commit_sha256": inventory["commit_sha256"],
        "source_asset_repair_incident_commit_sha256": incident["commit_sha256"],
        "physical_scene_plan_commit_sha256": physical_plan["commit_sha256"],
        "physical_scene_plan_role": "immutable_cache_namespace_only",
        "logical_source_city_ids": list(SOURCE_CITY_IDS),
        "logical_counts_by_city": counts,
        "logical_totals": totals,
        "excluded_scene_ids": [CHICAGO_UNAVAILABLE_SCENE, HOUSTON_UNAVAILABLE_SCENE],
        "excluded_overpass_ids": [CHICAGO_OVERPASS],
        "retained_modified_overpass_ids": [HOUSTON_OVERPASS],
        "logical_overpasses": rows,
        "logical_overpass_set_sha256": canonical_sha256(rows),
        "old_inventory_scene_plan_cache_or_queue_modified": False,
        "existing_content_commits_rewritten_copied_or_rebound": False,
        "source_raster_predictor_qa_target_or_model_execution_authorized": False,
        "blind_test_city_access_authorized": False,
        "permissions": {
            "read_landsat_asset_or_qa_values": False,
            "read_source_or_blind_target_values": False,
            "read_or_build_predictors": False,
            "read_blind_city_metadata_assets_qa_or_targets": False,
            "mutate_old_inventory_scene_plan_cache_or_queue": False,
            "fit_select_predict_score_or_publish": False,
        },
        "next_safe_stage": (
            "separately_authorize_new_logical_run_that_reuses_old_physical_cache_commits"
        ),
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def build_source_integrity_logical_overlay(
    project_root: str | Path,
    *,
    amendment_path: str | Path = AMENDMENT_PATH,
) -> dict[str, Any]:
    return _build_overlay(project_root, amendment_path=amendment_path)


def create_source_integrity_logical_overlay(
    project_root: str | Path,
    overlay_path: str | Path = OVERLAY_PATH,
    *,
    amendment_path: str | Path = AMENDMENT_PATH,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    amendment = authenticate_source_integrity_availability_amendment(root, amendment_path)
    expected_path = _inside(root, amendment["logical_overlay_path"], label="Logical overlay")
    destination = _inside(root, overlay_path, label="Logical overlay")
    if destination != expected_path:
        raise M3SourceIntegrityAmendmentError("Overlay path differs from the amendment.")
    payload = build_source_integrity_logical_overlay(root, amendment_path=amendment_path)
    _write_exclusive(payload, destination)
    return authenticate_source_integrity_logical_overlay(
        root,
        destination,
        amendment_path=amendment_path,
    )


def authenticate_source_integrity_logical_overlay(
    project_root: str | Path,
    overlay_path: str | Path = OVERLAY_PATH,
    *,
    amendment_path: str | Path = AMENDMENT_PATH,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    amendment = authenticate_source_integrity_availability_amendment(root, amendment_path)
    requested = _inside(root, overlay_path, label="Logical overlay")
    expected_path = _inside(root, amendment["logical_overlay_path"], label="Logical overlay")
    if requested != expected_path:
        raise M3SourceIntegrityAmendmentError("Overlay path differs from the amendment.")
    observed = _read_committed(
        requested,
        state="source_integrity_logical_overlay_complete",
        label="Source integrity logical overlay",
    )
    expected = _build_overlay(root, amendment_path=amendment_path)
    if observed != expected:
        raise M3SourceIntegrityAmendmentError("Source integrity logical overlay drifted.")
    return observed
