"""Append-only authorization and completion checks for M3 source predictors.

This module freezes the exact source-only key universe before any predictor
value is opened.  Authorization reads committed JSON metadata only.  The
runtime permit is deliberately lighter: it authenticates the immutable permit
and code identity once, before a worker may construct a value-reading adapter.
"""

from __future__ import annotations

import json
import os
import tomllib
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

from la_heat.provenance import canonical_sha256, sha256_file

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "m3-source-predictor-extension-v1"
DEFAULT_CONFIG: Final = Path("configs/multicity/m3_source_predictor_extension_v1.toml")
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_SOURCE_PREDICTOR_EXTENSION_V1_AUTHORIZATION.json"
)
ACQUISITION_COMPLETION_NAME: Final = "SOURCE_PREDICTOR_ACQUISITION_COMPLETE.json"
PREDICTOR_COMPLETION_NAME: Final = "SOURCE_PREDICTORS_46_COMPLETE.json"

SOURCE_CITY_IDS: Final = (
    "los_angeles_ca",
    "phoenix_az",
    "houston_tx",
    "chicago_il",
)
EXTENSION_CITY_IDS: Final = ("houston_tx", "chicago_il")
BLIND_CITY_IDS: Final = (
    "seattle_wa",
    "denver_co",
    "atlanta_ga",
    "miami_fl",
)
EXTENSION_YEARS: Final = (2020, 2021, 2022, 2023, 2024)
EXPECTED_OVERPASS_COUNT: Final = 317
EXPECTED_QA_CANDIDATES: Final = ("none", "3k", "4k", "6k")
EXPECTED_CITY_COUNTS: Final = {
    "los_angeles_ca": {"all_dates": 90, "tracts": 1096, "all_rows": 98_640},
    "phoenix_az": {"all_dates": 22, "tracts": 375, "all_rows": 8_250},
    "houston_tx": {
        "extension_dates": 81,
        "all_dates": 102,
        "tracts": 651,
        "extension_rows": 52_731,
        "all_rows": 66_402,
    },
    "chicago_il": {
        "extension_dates": 82,
        "all_dates": 103,
        "tracts": 780,
        "extension_rows": 63_960,
        "all_rows": 80_340,
    },
}

STATIC_FEATURES: Final = (
    "nlcd_open_water_fraction",
    "nlcd_developed_open_fraction",
    "nlcd_developed_low_fraction",
    "nlcd_developed_high_fraction",
    "nlcd_barren_fraction",
    "nlcd_forest_fraction",
    "nlcd_shrub_grass_fraction",
    "nlcd_agriculture_fraction",
    "nlcd_wetland_fraction",
    "impervious_mean_fraction",
    "impervious_p90_fraction",
    "impervious_at_least_50_fraction",
    "elevation_mean_m",
    "elevation_std_m",
    "slope_mean_degrees",
    "slope_p90_degrees",
    "gshhg_ocean_great_lakes_shore_distance_mean_km",
    "gshhg_ocean_great_lakes_shore_distance_p10_km",
)
CALENDAR_FEATURES: Final = ("calendar_doy_sin", "calendar_doy_cos")
DAYMET_FEATURES: Final = tuple(
    f"daymet_{variable}_{summary}_prev_{window}d"
    for window in (1, 3, 7)
    for variable, summary in (
        ("dayl_s", "mean"),
        ("prcp_mm", "sum"),
        ("srad_w_m2", "mean"),
        ("tmax_c", "mean"),
        ("tmin_c", "mean"),
        ("vp_pa", "mean"),
        ("srad_energy_mj_m2", "sum"),
    )
)
SENTINEL_FEATURES: Final = (
    "sentinel_ndvi_lag60",
    "sentinel_evi_lag60",
    "sentinel_ndwi_lag60",
    "sentinel_ndbi_lag60",
    "sentinel_albedo_proxy_lag60",
)
FEATURE_NAMES: Final = (
    *STATIC_FEATURES,
    *CALENDAR_FEATURES,
    *DAYMET_FEATURES,
    *SENTINEL_FEATURES,
)
CONTEXT_FEATURES: Final = ("city_centroid_latitude_deg",)
REQUIRED_COLUMNS: Final = (
    "city_id",
    "tract_geoid",
    "target_date",
    *FEATURE_NAMES,
)
CITY_CENTROID_ALGORITHM: Final = (
    "authenticate_census_place_city_boundary;project_to_locked_target_grid_crs;"
    "unary_union;centroid;transform_centroid_to_epsg4326;take_latitude"
)

CODE_PATHS: Final = (
    "src/la_heat/multicity/m3_source_predictor_extension_authorization_v1.py",
    "src/la_heat/multicity/m3_source_predictor_extension_runtime_v1.py",
    "src/la_heat/multicity/m3_source_predictor_extension_worker_v1.py",
    "scripts/authorize_m3_source_predictor_extension_v1.py",
    "scripts/run_m3_source_predictor_extension_v1.py",
)
REUSED_BUILDER_CODE_PATHS: Final = (
    "src/la_heat/calendar_features.py",
    "src/la_heat/daymet_feature_stage.py",
    "src/la_heat/daymet_grid.py",
    "src/la_heat/feature_registry.py",
    "src/la_heat/grid.py",
    "src/la_heat/model_run_queue.py",
    "src/la_heat/multicity/m3_source_development_worker.py",
    "src/la_heat/multicity/portable_predictor_components.py",
    "src/la_heat/multicity/portable_predictor_daymet.py",
    "src/la_heat/multicity/portable_sentinel_build.py",
    "src/la_heat/multicity/portable_sentinel_inventory.py",
    "src/la_heat/multicity/source_footprints.py",
    "src/la_heat/provenance.py",
    "src/la_heat/sentinel_feature_builder.py",
    "src/la_heat/sentinel_features.py",
    "src/la_heat/sentinel_inventory.py",
    "src/la_heat/static_features.py",
    "src/la_heat/weather_daymet.py",
)


class M3SourcePredictorExtensionError(RuntimeError):
    """Raised when the source-only extension leaves its frozen authorization."""


@dataclass(frozen=True, slots=True)
class PredictorExtensionSettings:
    root: Path
    config_path: Path
    anchors: Mapping[str, Any]
    scope: Mapping[str, Any]
    authorization: Path
    database: Path
    control: Path
    status: Path
    log: Path
    worker_lock: Path
    acquisition_root: Path
    component_root: Path
    output_root: Path
    completion_root: Path
    acquisition_completion: Path
    predictor_completion: Path
    official_sentinel_hosts: tuple[str, ...]
    lease_seconds: int
    heartbeat_seconds: int
    retry_base_seconds: int
    retry_max_seconds: int


def _with_commit(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(result)
    return result


def _is_committed(payload: Mapping[str, Any]) -> bool:
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    return isinstance(recorded, str) and recorded == canonical_sha256(unsigned)


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _inside(root: Path, value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise M3SourcePredictorExtensionError(f"{label} path is missing.")
    raw = Path(value)
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not path.is_relative_to(root):
        raise M3SourcePredictorExtensionError(f"{label} must stay inside the project.")
    return path


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _validated_host_patterns(value: object, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise M3SourcePredictorExtensionError(f"{label} whitelist is missing.")
    patterns: list[str] = []
    for raw in value:
        if not isinstance(raw, str) or raw != raw.strip().lower():
            raise M3SourcePredictorExtensionError(f"{label} whitelist entry is invalid.")
        hostname = raw[2:] if raw.startswith("*.") else raw
        labels = hostname.split(".")
        if (
            not hostname
            or "*" in hostname
            or len(labels) < 2
            or any(
                not part
                or len(part) > 63
                or not part[0].isalnum()
                or not part[-1].isalnum()
                or any(not (character.isalnum() or character == "-") for character in part)
                for part in labels
            )
        ):
            raise M3SourcePredictorExtensionError(f"{label} whitelist entry is invalid.")
        patterns.append(raw)
    if len(patterns) != len(set(patterns)):
        raise M3SourcePredictorExtensionError(f"{label} whitelist contains duplicates.")
    return tuple(patterns)


def _read_committed(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise M3SourcePredictorExtensionError(f"Cannot read {label}: {path}") from error
    if not isinstance(payload, dict) or not _is_committed(payload):
        raise M3SourcePredictorExtensionError(f"{label} commit is invalid.")
    return payload


def _file_record(
    root: Path,
    path: Path,
    *,
    commit_sha256: str | None = None,
) -> dict[str, Any]:
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file() or resolved.is_symlink():
        raise M3SourcePredictorExtensionError(f"Bound file is invalid: {path}")
    result: dict[str, Any] = {
        "path": _relative(root, resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }
    if commit_sha256 is not None:
        result["commit_sha256"] = commit_sha256
    return result


def _record_path(root: Path, record: Mapping[str, Any], *, label: str) -> Path:
    pure = PurePosixPath(str(record.get("path", "")))
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise M3SourcePredictorExtensionError(f"{label} path is unsafe.")
    path = (root / Path(*pure.parts)).resolve()
    if (
        not path.is_relative_to(root)
        or not path.is_file()
        or path.is_symlink()
        or path.stat().st_size != record.get("bytes")
        or sha256_file(path) != record.get("sha256")
    ):
        raise M3SourcePredictorExtensionError(f"{label} file record changed.")
    return path


def _write_exclusive(payload: Mapping[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(dict(payload), indent=2, ensure_ascii=False) + "\n").encode()
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as error:
        raise M3SourcePredictorExtensionError(
            f"Append-only artifact already exists: {destination}"
        ) from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def load_predictor_extension_settings(
    project_root: str | Path,
    config_path: str | Path = DEFAULT_CONFIG,
) -> PredictorExtensionSettings:
    """Load the rigid source-only configuration and reject path aliasing."""

    root = Path(project_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    config = _inside(root, str(config_path), label="Predictor extension config")
    with config.open("rb") as handle:
        raw = tomllib.load(handle)
    if raw.get("schema_version") != 1 or raw.get("algorithm_version") != ALGORITHM_VERSION:
        raise M3SourcePredictorExtensionError("Predictor extension config changed.")
    anchors = raw.get("anchors")
    scope = raw.get("scope")
    runtime = raw.get("runtime")
    execution = raw.get("execution")
    limits = raw.get("limits")
    if not all(
        isinstance(value, Mapping) for value in (anchors, scope, runtime, execution, limits)
    ):
        raise M3SourcePredictorExtensionError("Predictor extension config is incomplete.")
    assert isinstance(anchors, Mapping)
    assert isinstance(scope, Mapping)
    assert isinstance(runtime, Mapping)
    assert isinstance(execution, Mapping)
    assert isinstance(limits, Mapping)
    if (
        tuple(scope.get("source_city_ids", ())) != SOURCE_CITY_IDS
        or tuple(scope.get("extension_city_ids", ())) != EXTENSION_CITY_IDS
        or tuple(scope.get("blind_test_city_ids", ())) != BLIND_CITY_IDS
        or tuple(scope.get("extension_years", ())) != EXTENSION_YEARS
        or scope.get("logical_overpass_count") != EXPECTED_OVERPASS_COUNT
        or scope.get("feature_count") != len(FEATURE_NAMES)
        or scope.get("extension_row_count") != 116_691
    ):
        raise M3SourcePredictorExtensionError("Source key or feature scope changed.")
    for city_id in EXTENSION_CITY_IDS:
        city = scope.get(city_id)
        expected = EXPECTED_CITY_COUNTS[city_id]
        if not isinstance(city, Mapping) or any(
            city.get(name) != expected[key]
            for name, key in (
                ("extension_date_count", "extension_dates"),
                ("all_date_count", "all_dates"),
                ("tract_count", "tracts"),
                ("extension_row_count", "extension_rows"),
                ("all_row_count", "all_rows"),
            )
        ):
            raise M3SourcePredictorExtensionError(f"{city_id} scope changed.")
    online = limits.get("online_acquisition")
    offline = limits.get("offline_assembly")
    global_limits = limits.get("global")
    if (
        not isinstance(online, Mapping)
        or not isinstance(offline, Mapping)
        or not isinstance(global_limits, Mapping)
        or online.get("network_requests_allowed") is not True
        or online.get("href_reads_allowed") is not True
        or offline.get("network_requests_allowed") is not False
        or offline.get("href_reads_allowed") is not False
        or any(global_limits.get(key) is not False for key in global_limits)
        or execution.get("compute_workers") != 1
        or execution.get("download_workers") != 1
        or execution.get("maximum_active_tasks") != 1
        or execution.get("anonymous_daymet_dap_first") is not True
        or execution.get("credential_persistence_allowed") is not False
        or execution.get("planetary_computer_signed_url_persistence_allowed") is not False
    ):
        raise M3SourcePredictorExtensionError("Runtime safety limits changed.")
    official_sentinel_hosts = _validated_host_patterns(
        online.get("official_sentinel_hosts"), label="Official Sentinel host"
    )
    paths = {
        key: _inside(root, runtime.get(key), label=key)
        for key in (
            "authorization",
            "database",
            "control",
            "status",
            "log",
            "worker_lock",
            "acquisition_root",
            "component_root",
            "output_root",
            "completion_root",
            "acquisition_completion",
            "predictor_completion",
        )
    }
    old_roots = (
        _inside(root, anchors.get("qa_output_root"), label="QA output root"),
        _inside(root, anchors.get("existing_predictor_root"), label="Existing predictor root"),
        root / "data/interim/multicity/m3_source_development",
        root / "data/interim/multicity/m3_source_development_v2",
        root / "manifests/multicity/next_experiment/source_development_v2",
    )
    for label, target in paths.items():
        if any(_overlaps(target, old.resolve()) for old in old_roots):
            raise M3SourcePredictorExtensionError(
                f"{label} must be isolated from every prior runtime/cache/output."
            )
    if (
        paths["acquisition_completion"].parent != paths["completion_root"]
        or paths["predictor_completion"].parent != paths["completion_root"]
    ):
        raise M3SourcePredictorExtensionError("Completion paths escaped their root.")
    write_roots = (
        paths["database"].parent,
        paths["acquisition_root"],
        paths["component_root"],
        paths["output_root"],
        paths["completion_root"],
    )
    if any(
        _overlaps(left, right)
        for index, left in enumerate(write_roots)
        for right in write_roots[index + 1 :]
    ):
        raise M3SourcePredictorExtensionError("New write roots overlap each other.")
    return PredictorExtensionSettings(
        root=root,
        config_path=config,
        anchors=anchors,
        scope=scope,
        authorization=paths["authorization"],
        database=paths["database"],
        control=paths["control"],
        status=paths["status"],
        log=paths["log"],
        worker_lock=paths["worker_lock"],
        acquisition_root=paths["acquisition_root"],
        component_root=paths["component_root"],
        output_root=paths["output_root"],
        completion_root=paths["completion_root"],
        acquisition_completion=paths["acquisition_completion"],
        predictor_completion=paths["predictor_completion"],
        official_sentinel_hosts=official_sentinel_hosts,
        lease_seconds=int(execution["lease_seconds"]),
        heartbeat_seconds=int(execution["heartbeat_seconds"]),
        retry_base_seconds=int(execution["retry_base_seconds"]),
        retry_max_seconds=int(execution["retry_max_seconds"]),
    )


def _anchor(
    settings: PredictorExtensionSettings,
    path_key: str,
    commit_key: str,
    *,
    label: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    path = _inside(settings.root, settings.anchors.get(path_key), label=label)
    payload = _read_committed(path, label=label)
    expected = settings.anchors.get(commit_key)
    if payload.get("commit_sha256") != expected:
        raise M3SourcePredictorExtensionError(f"{label} commit changed.")
    return payload, _file_record(settings.root, path, commit_sha256=str(payload["commit_sha256"]))


def _qa_commit_evidence(
    settings: PredictorExtensionSettings,
    overpasses: Sequence[Mapping[str, Any]],
    qa_completion: Mapping[str, Any],
) -> tuple[str, str]:
    overpass_refs: list[dict[str, str]] = []
    by_city_commits: dict[str, list[str]] = {city_id: [] for city_id in SOURCE_CITY_IDS}
    for row in overpasses:
        city_id = str(row.get("city_id", ""))
        overpass_id = str(row.get("overpass_id", ""))
        if city_id not in SOURCE_CITY_IDS or not overpass_id:
            raise M3SourcePredictorExtensionError("Logical overpass identity changed.")
        path = (
            settings.root
            / str(settings.anchors["qa_output_root"])
            / "by_overpass"
            / city_id
            / overpass_id
            / "QA_CANDIDATES_COMPLETE.json"
        )
        payload = _read_committed(path, label=f"{city_id}/{overpass_id} QA completion")
        lock = payload.get("cache_lock")
        if (
            payload.get("state") != "qa_overpass_complete"
            or not isinstance(lock, Mapping)
            or lock.get("city_id") != city_id
            or lock.get("overpass_id") != overpass_id
            or tuple(payload.get("candidate_ids", ())) != EXPECTED_QA_CANDIDATES
            or payload.get("network_requests_performed") != 0
            or payload.get("href_reads_performed") != 0
            or payload.get("blind_test_city_accessed") is not False
            or payload.get("model_fit_or_selection_performed") is not False
        ):
            raise M3SourcePredictorExtensionError("QA overpass audit changed.")
        overpass_refs.append(
            {
                "city_id": city_id,
                "overpass_id": overpass_id,
                "commit_sha256": str(payload["commit_sha256"]),
            }
        )
        by_city_commits[city_id].append(str(payload["commit_sha256"]))
    final_city_refs = qa_completion.get("city_commits")
    if not isinstance(final_city_refs, list):
        raise M3SourcePredictorExtensionError("QA final city commit chain changed.")
    expected_final = {
        str(record.get("city_id")): str(record.get("commit_sha256"))
        for record in final_city_refs
        if isinstance(record, Mapping)
    }
    if tuple(expected_final) != SOURCE_CITY_IDS:
        raise M3SourcePredictorExtensionError("QA final city commit order changed.")
    city_refs: list[dict[str, str]] = []
    for city_id in SOURCE_CITY_IDS:
        path = (
            settings.root
            / str(settings.anchors["qa_output_root"])
            / "cities"
            / city_id
            / "CITY_QA_CANDIDATES_COMPLETE.json"
        )
        payload = _read_committed(path, label=f"{city_id} QA city completion")
        lock = payload.get("cache_lock")
        if (
            payload.get("state") != "city_qa_candidates_complete"
            or not isinstance(lock, Mapping)
            or lock.get("city_id") != city_id
            or tuple(payload.get("candidate_ids", ())) != EXPECTED_QA_CANDIDATES
            or payload.get("network_requests_performed") != 0
            or payload.get("href_reads_performed") != 0
            or payload.get("blind_test_city_accessed") is not False
            or payload.get("model_fit_or_selection_performed") is not False
            or lock.get("overpass_commits_sha256") != canonical_sha256(by_city_commits[city_id])
            or lock.get("overpass_count") != len(by_city_commits[city_id])
            or payload.get("commit_sha256") != expected_final.get(city_id)
        ):
            raise M3SourcePredictorExtensionError("QA city audit changed.")
        city_refs.append({"city_id": city_id, "commit_sha256": str(payload["commit_sha256"])})
    return canonical_sha256(overpass_refs), canonical_sha256(city_refs)


def _key_universe(
    settings: PredictorExtensionSettings,
    integrity: Mapping[str, Any],
    qa_completion: Mapping[str, Any],
) -> dict[str, Any]:
    overlay = integrity.get("logical_overlay")
    if not isinstance(overlay, Mapping):
        raise M3SourcePredictorExtensionError("Integrity authorization lacks overlay.")
    raw = overlay.get("overpasses")
    if not isinstance(raw, list) or len(raw) != EXPECTED_OVERPASS_COUNT:
        raise M3SourcePredictorExtensionError("Logical overpass count changed.")
    overpasses: list[Mapping[str, Any]] = []
    for row in raw:
        if not isinstance(row, Mapping):
            raise M3SourcePredictorExtensionError("Logical overpass row changed.")
        overpasses.append(row)
    counts = Counter(str(row.get("city_id", "")) for row in overpasses)
    if counts != Counter(
        {city_id: values["all_dates"] for city_id, values in EXPECTED_CITY_COUNTS.items()}
    ):
        raise M3SourcePredictorExtensionError("Logical city/date counts changed.")
    all_cities: list[dict[str, Any]] = []
    extension_cities: list[dict[str, Any]] = []
    for city_id in SOURCE_CITY_IDS:
        dates = tuple(
            sorted(
                str(row.get("target_date", "")) for row in overpasses if row["city_id"] == city_id
            )
        )
        if len(dates) != len(set(dates)) or any(len(value) != 10 for value in dates):
            raise M3SourcePredictorExtensionError(f"{city_id} dates are not unique ISO days.")
        expected = EXPECTED_CITY_COUNTS[city_id]
        all_cities.append(
            {
                "city_id": city_id,
                "target_dates": list(dates),
                "target_date_count": len(dates),
                "tract_count": expected["tracts"],
                "row_count": expected["all_rows"],
                "key_identity_sha256": canonical_sha256(
                    {
                        "city_id": city_id,
                        "target_dates": dates,
                        "tract_count": expected["tracts"],
                        "canonical_tract_identity": (
                            "bound_by_existing_predictor_and_static_provenance"
                        ),
                    }
                ),
            }
        )
        if city_id in EXTENSION_CITY_IDS:
            extension_dates = tuple(value for value in dates if int(value[:4]) in EXTENSION_YEARS)
            if (
                len(extension_dates) != expected["extension_dates"]
                or len(extension_dates) * expected["tracts"] != expected["extension_rows"]
            ):
                raise M3SourcePredictorExtensionError(f"{city_id} extension date universe changed.")
            extension_cities.append(
                {
                    "city_id": city_id,
                    "target_dates": list(extension_dates),
                    "target_date_count": len(extension_dates),
                    "tract_count": expected["tracts"],
                    "row_count": expected["extension_rows"],
                }
            )
    overpass_sha, city_sha = _qa_commit_evidence(settings, overpasses, qa_completion)
    payload = {
        "identity_columns": ["city_id", "tract_geoid", "target_date"],
        "all_source_cities": all_cities,
        "extension_cities": extension_cities,
        "extension_years": list(EXTENSION_YEARS),
        "logical_overpass_count": EXPECTED_OVERPASS_COUNT,
        "extension_row_count": 116_691,
        "qa_overpass_commit_set_sha256": overpass_sha,
        "qa_city_commit_set_sha256": city_sha,
    }
    return {**payload, "key_universe_sha256": canonical_sha256(payload)}


def _static_provenance(
    settings: PredictorExtensionSettings,
    city_id: str,
) -> dict[str, Any]:
    scope = settings.scope.get(city_id)
    configured_path = scope.get("static_provenance") if isinstance(scope, Mapping) else None
    path = _inside(
        settings.root,
        configured_path
        or (
            "data/processed/multicity/portable_predictors/components/"
            f"{city_id}/static_features_provenance.json"
        ),
        label="Static provenance",
    )
    payload = _read_committed(path, label=f"{city_id} static provenance")
    if (
        (
            isinstance(scope, Mapping)
            and scope.get("static_provenance_commit_sha256") is not None
            and payload.get("commit_sha256") != scope.get("static_provenance_commit_sha256")
        )
        or payload.get("city_id") != city_id
        or payload.get("row_count") != EXPECTED_CITY_COUNTS[city_id]["tracts"]
        or tuple(payload.get("model_feature_names", ())) != STATIC_FEATURES
        or payload.get("target_or_qa_values_read") is not False
    ):
        raise M3SourcePredictorExtensionError(f"{city_id} static provenance changed.")
    return _file_record(settings.root, path, commit_sha256=str(payload["commit_sha256"]))


def _source_footprint_record(
    settings: PredictorExtensionSettings,
    inventory: Mapping[str, Any],
    city_id: str,
) -> dict[str, Any]:
    path = settings.root / (
        f"manifests/multicity/cities/{city_id}/source_footprints/SOURCE_FOOTPRINTS.json"
    )
    payload = _read_committed(path, label=f"{city_id} source footprints")
    city = inventory.get("cities", {}).get(city_id)
    access = payload.get("access_contract", {})
    if (
        not isinstance(city, Mapping)
        or payload.get("commit_sha256") != city.get("source_manifest_commit_sha256")
        or payload.get("city", {}).get("id") != city_id
        or payload.get("city", {}).get("target_values_status") != "sealed"
        or access.get("landsat_thermal_values_read") is not False
        or access.get("landsat_target_qa_values_read") is not False
        or access.get("external_lst_values_read") is not False
    ):
        raise M3SourcePredictorExtensionError(f"{city_id} source-footprint seal changed.")
    return _file_record(settings.root, path, commit_sha256=str(payload["commit_sha256"]))


def build_m3_source_predictor_extension_authorization(
    project_root: str | Path,
    config_path: str | Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Build a metadata-only preview; no predictor, QA, or target table is opened."""

    settings = load_predictor_extension_settings(project_root, config_path)
    protocol, protocol_record = _anchor(
        settings,
        "m3_protocol_lock",
        "m3_protocol_lock_commit_sha256",
        label="M3 protocol lock",
    )
    integrity, integrity_record = _anchor(
        settings,
        "source_integrity_authorization",
        "source_integrity_authorization_commit_sha256",
        label="M3 source integrity authorization",
    )
    qa, qa_record = _anchor(
        settings,
        "source_qa_completion",
        "source_qa_completion_commit_sha256",
        label="M3 source QA completion",
    )
    contract, contract_record = _anchor(
        settings,
        "portable_predictor_contract",
        "portable_predictor_contract_commit_sha256",
        label="Portable predictor contract",
    )
    inventory, inventory_record = _anchor(
        settings,
        "portable_predictor_inventory",
        "portable_predictor_inventory_commit_sha256",
        label="Portable predictor inventory",
    )
    old_all46, old_all46_record = _anchor(
        settings,
        "existing_predictors_46_completion",
        "existing_predictors_46_completion_commit_sha256",
        label="Existing 46-feature predictor completion",
    )
    base_path = _inside(
        settings.root,
        settings.anchors.get("base_components_completion"),
        label="Base component completion",
    )
    try:
        base = json.loads(base_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise M3SourcePredictorExtensionError("Cannot read base component completion.") from error
    if (
        not isinstance(base, dict)
        or base_path.stat().st_size != settings.anchors.get("base_components_completion_bytes")
        or sha256_file(base_path) != settings.anchors.get("base_components_completion_sha256")
    ):
        raise M3SourcePredictorExtensionError(
            "Legacy uncommitted base component file lock changed."
        )
    base_record = _file_record(settings.root, base_path)
    if (
        protocol.get("commit_sha256") != qa.get("m3_protocol_lock_commit_sha256")
        or qa.get("state") != "source_qa_candidates_complete"
        or tuple(qa.get("source_city_ids", ())) != SOURCE_CITY_IDS
        or tuple(qa.get("candidate_ids", ())) != EXPECTED_QA_CANDIDATES
        or qa.get("overpass_count") != EXPECTED_OVERPASS_COUNT
        or qa.get("support_gate", {}).get("passed") is not True
        or qa.get("decision") != "eligible_for_separate_nested_loso_authorization"
        or any(
            qa.get("offline_audit", {}).get(key) != expected
            for key, expected in {
                "network_requests_performed": 0,
                "href_reads_performed": 0,
                "blind_test_city_accessed": False,
                "predictor_values_read_or_built": False,
                "model_fit_selection_prediction_or_scoring_performed": False,
            }.items()
        )
    ):
        raise M3SourcePredictorExtensionError("Authenticated QA completion changed.")
    if (
        tuple(old_all46.get("feature_order", ())) != FEATURE_NAMES
        or old_all46.get("feature_count") != len(FEATURE_NAMES)
        or old_all46.get("access_contract", {}).get("external_target_or_qa_values_read")
        is not False
        or old_all46.get("access_contract", {}).get("model_fit_or_prediction_performed")
        is not False
        or tuple(base.get("feature_order", ())) != FEATURE_NAMES[:41]
        or inventory.get("contract_commit_sha256") != contract.get("commit_sha256")
    ):
        raise M3SourcePredictorExtensionError("Existing predictor contract changed.")
    key_universe = _key_universe(settings, integrity, qa)
    code_records = [
        _file_record(settings.root, settings.config_path),
        *[
            _file_record(settings.root, settings.root / path)
            for path in (*CODE_PATHS, *REUSED_BUILDER_CODE_PATHS)
        ],
    ]
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "m3_source_predictor_extension_authorized",
        "scope": "source_only_houston_chicago_2020_2024_predictor_extension",
        "inputs": {
            "m3_protocol_lock": protocol_record,
            "source_integrity_authorization": integrity_record,
            "source_qa_candidates_completion": qa_record,
            "portable_predictor_contract": contract_record,
            "portable_predictor_inventory": inventory_record,
            "base_components_completion": base_record,
            "existing_predictors_46_completion": old_all46_record,
            "static_provenance": [
                _static_provenance(settings, city_id) for city_id in SOURCE_CITY_IDS
            ],
            "extension_source_footprints": [
                _source_footprint_record(settings, inventory, city_id)
                for city_id in EXTENSION_CITY_IDS
            ],
        },
        "source_qa_candidates_completion_commit_sha256": qa["commit_sha256"],
        "key_universe": key_universe,
        "feature_contract": {
            "feature_count": len(FEATURE_NAMES),
            "feature_names": list(FEATURE_NAMES),
            "required_columns": list(REQUIRED_COLUMNS),
            "static_count": len(STATIC_FEATURES),
            "calendar_count": len(CALENDAR_FEATURES),
            "daymet_count": len(DAYMET_FEATURES),
            "lagged_sentinel_count": len(SENTINEL_FEATURES),
            "lagged_sentinel_window_days": 60,
        },
        "city_context_contract": {
            "feature_names": list(CONTEXT_FEATURES),
            "counted_within_46_predictors": False,
            "centroid_algorithm": CITY_CENTROID_ALGORITHM,
            "source": "authenticated_census_place_city_boundary_geometry",
            "join_contract": (
                "repeat_exact_city_constant_by_city_id_only_after_"
                "predictor_completion_authentication"
            ),
            "table_column_forbidden_to_prevent_context_smuggling": True,
        },
        "reuse_contract": {
            "existing_2025_la_phoenix_authenticated_reuse_only": True,
            "houston_chicago_static_authenticated_reuse_only": True,
            "uncommitted_or_schema_drifted_existing_extension_data_accepted": False,
            "old_predictor_or_component_files_mutated": False,
        },
        "runtime_contract": {
            "phases": ["online_acquisition", "offline_assembly"],
            "online_network_and_href_reads_allowed": True,
            "offline_network_and_href_reads_allowed": False,
            "compute_workers": 1,
            "download_workers": 1,
            "maximum_active_tasks": 1,
            "resume_without_queue_rebuild_or_reset": True,
            "anonymous_official_daymet_dap_attempted_first": True,
            "earthdata_environment_token_optional_fallback": True,
            "official_sentinel_hosts": list(settings.official_sentinel_hosts),
            "credential_or_signed_url_persisted": False,
            "acquisition_completion_required_before_offline_values": True,
        },
        "write_paths": {
            "database": _relative(settings.root, settings.database),
            "control": _relative(settings.root, settings.control),
            "status": _relative(settings.root, settings.status),
            "log": _relative(settings.root, settings.log),
            "worker_lock": _relative(settings.root, settings.worker_lock),
            "acquisition_root": _relative(settings.root, settings.acquisition_root),
            "component_root": _relative(settings.root, settings.component_root),
            "output_root": _relative(settings.root, settings.output_root),
            "acquisition_completion": _relative(settings.root, settings.acquisition_completion),
            "predictor_completion": _relative(settings.root, settings.predictor_completion),
        },
        "permissions": {
            "read_public_predictor_values_after_this_permit_is_authenticated": True,
            "write_only_new_extension_runtime_cache_components_and_outputs": True,
            "read_or_write_blind_city_asset_predictor_qa_or_target": False,
            "read_landsat_thermal_qa_or_any_target_value": False,
            "fit_select_predict_score_or_choose_model_or_qa": False,
            "change_year_city_feature_key_or_support_gate": False,
            "modify_old_queue_cache_qa_predictor_or_manifest": False,
        },
        "code_identity": {
            "files": code_records,
            "set_sha256": canonical_sha256(code_records),
        },
        "authorization_access_audit": {
            "predictor_values_read": False,
            "qa_values_read": False,
            "target_values_read": False,
            "network_or_href_reads": 0,
            "blind_test_city_accessed": False,
            "old_artifact_modified": False,
            "model_fit_select_predict_or_score_performed": False,
        },
        "next_safe_stage": "initialize_paused_predictor_extension_runtime",
    }
    return _with_commit(payload)


def create_m3_source_predictor_extension_authorization(
    project_root: str | Path,
    output_path: str | Path = AUTHORIZATION_PATH,
    config_path: str | Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    settings = load_predictor_extension_settings(project_root, config_path)
    destination = _inside(settings.root, str(output_path), label="Authorization output")
    if destination != settings.authorization:
        raise M3SourcePredictorExtensionError("Authorization output path changed.")
    payload = build_m3_source_predictor_extension_authorization(settings.root, settings.config_path)
    _write_exclusive(payload, destination)
    return payload


def authenticate_m3_source_predictor_extension_authorization(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
    config_path: str | Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    settings = load_predictor_extension_settings(project_root, config_path)
    path = _inside(settings.root, str(authorization_path), label="Authorization")
    if path != settings.authorization:
        raise M3SourcePredictorExtensionError("Authorization path changed.")
    observed = _read_committed(path, label="Predictor extension authorization")
    expected = build_m3_source_predictor_extension_authorization(
        settings.root, settings.config_path
    )
    if observed != expected:
        raise M3SourcePredictorExtensionError("Predictor extension authorization drifted.")
    return observed


def load_m3_source_predictor_extension_runtime_permit(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
    config_path: str | Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Authenticate immutable permit/code without opening predictor values."""

    settings = load_predictor_extension_settings(project_root, config_path)
    path = _inside(settings.root, str(authorization_path), label="Authorization")
    if path != settings.authorization:
        raise M3SourcePredictorExtensionError("Authorization path changed.")
    permit = _read_committed(path, label="Predictor extension authorization")
    if (
        permit.get("schema_version") != SCHEMA_VERSION
        or permit.get("algorithm_version") != ALGORITHM_VERSION
        or permit.get("state") != "m3_source_predictor_extension_authorized"
        or permit.get("feature_contract", {}).get("feature_names") != list(FEATURE_NAMES)
        or permit.get("key_universe", {}).get("logical_overpass_count") != EXPECTED_OVERPASS_COUNT
    ):
        raise M3SourcePredictorExtensionError("Runtime permit scope changed.")
    records = permit.get("code_identity", {}).get("files")
    if not isinstance(records, list) or canonical_sha256(records) != permit.get(
        "code_identity", {}
    ).get("set_sha256"):
        raise M3SourcePredictorExtensionError("Runtime permit code set changed.")
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise M3SourcePredictorExtensionError("Runtime permit code record changed.")
        _record_path(settings.root, record, label=f"Code identity {index}")
    inputs = permit.get("inputs")
    if not isinstance(inputs, Mapping):
        raise M3SourcePredictorExtensionError("Runtime permit input locks changed.")
    for label, value in inputs.items():
        input_records = value if isinstance(value, list) else [value]
        if not input_records:
            raise M3SourcePredictorExtensionError(f"Runtime permit input {label} is empty.")
        for index, record in enumerate(input_records):
            if not isinstance(record, Mapping):
                raise M3SourcePredictorExtensionError(f"Runtime permit input {label} changed.")
            _record_path(
                settings.root,
                record,
                label=f"Runtime input {label}/{index}",
            )
    return permit


def _completion_record_path(
    settings: PredictorExtensionSettings,
    record: Mapping[str, Any],
    *,
    allowed_root: Path,
    label: str,
) -> Path:
    path = _record_path(settings.root, record, label=label)
    if not path.is_relative_to(allowed_root):
        raise M3SourcePredictorExtensionError(f"{label} escaped its new output root.")
    return path


def _completion_metadata_record_path(
    settings: PredictorExtensionSettings,
    record: Mapping[str, Any],
    *,
    allowed_root: Path,
    label: str,
    expected_path: Path | None = None,
) -> Path:
    """Validate a completion file record without touching the referenced file."""

    pure = PurePosixPath(str(record.get("path", "")))
    allowed = PurePosixPath(_relative(settings.root, allowed_root))
    if (
        pure.is_absolute()
        or not pure.parts
        or ".." in pure.parts
        or not pure.is_relative_to(allowed)
        or not isinstance(record.get("bytes"), int)
        or isinstance(record.get("bytes"), bool)
        or record["bytes"] <= 0
        or not _is_sha256(record.get("sha256"))
    ):
        raise M3SourcePredictorExtensionError(f"{label} metadata record changed.")
    path = settings.root / Path(*pure.parts)
    if expected_path is not None and pure != PurePosixPath(_relative(settings.root, expected_path)):
        raise M3SourcePredictorExtensionError(f"{label} metadata path changed.")
    return path


def _static_manifest_from_permit(
    settings: PredictorExtensionSettings,
    permit: Mapping[str, Any],
    city_id: str,
) -> tuple[dict[str, Any], Path]:
    records = permit.get("inputs", {}).get("static_provenance")
    if not isinstance(records, list):
        raise M3SourcePredictorExtensionError("Static provenance permit changed.")
    matches: list[tuple[dict[str, Any], Path]] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise M3SourcePredictorExtensionError("Static provenance record changed.")
        path = _record_path(settings.root, record, label=f"Static provenance {index}")
        payload = _read_committed(path, label=f"Static provenance {index}")
        if payload.get("city_id") == city_id:
            matches.append((payload, path))
    if len(matches) != 1:
        raise M3SourcePredictorExtensionError(
            f"Expected one static provenance manifest for {city_id}."
        )
    return matches[0]


def authenticated_static_tract_geoids(
    project_root: str | Path,
    permit: Mapping[str, Any],
    city_id: str,
    *,
    config_path: str | Path = DEFAULT_CONFIG,
) -> tuple[str, ...]:
    """Read the exact canonical static GEOID set after permit authentication."""

    if city_id not in SOURCE_CITY_IDS:
        raise M3SourcePredictorExtensionError("Non-source city GEOIDs are forbidden.")
    settings = load_predictor_extension_settings(project_root, config_path)
    manifest, manifest_path = _static_manifest_from_permit(settings, permit, city_id)
    record = manifest.get("output_files", {}).get("static_features.parquet")
    if not isinstance(record, Mapping):
        raise M3SourcePredictorExtensionError("Static feature file record changed.")
    path = manifest_path.parent / "static_features.parquet"
    if (
        not path.is_file()
        or path.is_symlink()
        or path.stat().st_size != record.get("bytes")
        or sha256_file(path) != record.get("sha256")
    ):
        raise M3SourcePredictorExtensionError("Static feature file lock changed.")
    import pandas as pd

    frame = pd.read_parquet(path, columns=["tract_geoid"])
    geoids = tuple(sorted(frame["tract_geoid"].astype(str)))
    if len(geoids) != EXPECTED_CITY_COUNTS[city_id]["tracts"] or len(set(geoids)) != len(geoids):
        raise M3SourcePredictorExtensionError("Canonical static GEOID set changed.")
    return geoids


def authenticated_city_centroid_latitude(
    project_root: str | Path,
    permit: Mapping[str, Any],
    city_id: str,
    *,
    config_path: str | Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Derive one city context constant from authenticated Census-place geometry."""

    if city_id not in SOURCE_CITY_IDS:
        raise M3SourcePredictorExtensionError("Non-source city centroid is forbidden.")
    settings = load_predictor_extension_settings(project_root, config_path)
    inventory_record = permit.get("inputs", {}).get("portable_predictor_inventory")
    if not isinstance(inventory_record, Mapping):
        raise M3SourcePredictorExtensionError("Predictor inventory permit changed.")
    inventory_path = _record_path(
        settings.root, inventory_record, label="Portable predictor inventory"
    )
    inventory = _read_committed(inventory_path, label="Portable predictor inventory")
    city = inventory.get("cities", {}).get(city_id)
    if not isinstance(city, Mapping):
        raise M3SourcePredictorExtensionError("Predictor city metadata changed.")
    geography_path = settings.root / (
        f"manifests/multicity/cities/{city_id}/geography/GEOGRAPHY_CONTRACT_V1.json"
    )
    geography = _read_committed(geography_path, label=f"{city_id} geography")
    if geography.get("commit_sha256") != city.get("geography_commit_sha256"):
        raise M3SourcePredictorExtensionError("Geography commit changed.")
    static, _ = _static_manifest_from_permit(settings, permit, city_id)
    support_path = settings.root / (
        f"manifests/multicity/cities/{city_id}/eligible_support/WORLDCOVER_ELIGIBLE_SUPPORT_V1.json"
    )
    support = _read_committed(support_path, label=f"{city_id} WorldCover support")
    if support.get("commit_sha256") != static.get("worldcover_commit_sha256"):
        raise M3SourcePredictorExtensionError("WorldCover support commit changed.")
    boundary_record = geography.get("output_tables", {}).get("city_boundary")
    if not isinstance(boundary_record, Mapping):
        raise M3SourcePredictorExtensionError("City boundary record changed.")
    boundary_path = _record_path(settings.root, boundary_record, label=f"{city_id} city boundary")
    target_crs = str(support.get("grid", {}).get("crs", ""))
    if not target_crs:
        raise M3SourcePredictorExtensionError("Locked target-grid CRS changed.")
    import geopandas as gpd
    import shapely

    boundary = gpd.read_parquet(boundary_path)
    if boundary.empty or boundary.crs is None:
        raise M3SourcePredictorExtensionError("City boundary geometry changed.")
    projected = boundary.to_crs(target_crs)
    union = shapely.union_all(projected.geometry.to_numpy())
    if union is None or union.is_empty:
        raise M3SourcePredictorExtensionError("City boundary union is empty.")
    point = gpd.GeoSeries([union.centroid], crs=target_crs).to_crs("EPSG:4326").iloc[0]
    latitude = float(point.y)
    if not (-90.0 <= latitude <= 90.0):
        raise M3SourcePredictorExtensionError("City centroid latitude is invalid.")
    return {
        "city_id": city_id,
        "city_centroid_latitude_deg": latitude,
        "algorithm": CITY_CENTROID_ALGORITHM,
        "geography_commit_sha256": geography["commit_sha256"],
        "worldcover_support_commit_sha256": support["commit_sha256"],
        "city_boundary_sha256": boundary_record["sha256"],
        "target_grid_crs": target_crs,
    }


def authenticate_source_predictor_acquisition_completion(
    project_root: str | Path,
    *,
    authorization_path: str | Path = AUTHORIZATION_PATH,
    completion_path: str | Path | None = None,
    config_path: str | Path = DEFAULT_CONFIG,
    authenticate_files: bool = True,
) -> dict[str, Any]:
    """Authenticate the online/offline boundary without persisting any URL."""

    settings = load_predictor_extension_settings(project_root, config_path)
    permit = load_m3_source_predictor_extension_runtime_permit(
        settings.root, authorization_path, settings.config_path
    )
    path = (
        settings.acquisition_completion
        if completion_path is None
        else _inside(settings.root, str(completion_path), label="Acquisition completion")
    )
    if path != settings.acquisition_completion:
        raise M3SourcePredictorExtensionError("Acquisition completion path changed.")
    payload = _read_committed(path, label="Source predictor acquisition completion")
    records = payload.get("files")
    expected_counts = {
        "acquire_daymet_metadata": 10,
        "acquire_daymet_subset": 60,
        "build_sentinel_inventory": 2,
        "acquire_sentinel_cache": 2,
    }
    if (
        payload.get("state") != "source_predictor_acquisition_complete"
        or payload.get("authorization_commit_sha256") != permit["commit_sha256"]
        or payload.get("key_universe_sha256") != permit["key_universe"]["key_universe_sha256"]
        or payload.get("network_phase") != "online_acquisition_closed"
        or payload.get("credentials_or_signed_urls_persisted") is not False
        or payload.get("blind_test_city_accessed") is not False
        or payload.get("target_or_landsat_values_read") is not False
        or payload.get("completed_task_counts") != expected_counts
        or not isinstance(records, list)
        or not records
        or payload.get("file_set_sha256") != canonical_sha256(records)
        or len({str(record.get("path", "")) for record in records if isinstance(record, Mapping)})
        != len(records)
    ):
        raise M3SourcePredictorExtensionError("Acquisition completion scope changed.")
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise M3SourcePredictorExtensionError("Acquisition file record changed.")
        if any(word in json.dumps(record).casefold() for word in ("token", "sig=", "se=")):
            raise M3SourcePredictorExtensionError("Credential-bearing acquisition record found.")
        if authenticate_files:
            _completion_record_path(
                settings,
                record,
                allowed_root=settings.acquisition_root,
                label=f"Acquisition file {index}",
            )
        else:
            _completion_metadata_record_path(
                settings,
                record,
                allowed_root=settings.acquisition_root,
                label=f"Acquisition file {index}",
            )
    return payload


def authenticate_source_predictors_46_completion(
    project_root: str | Path,
    *,
    authorization_path: str | Path = AUTHORIZATION_PATH,
    completion_path: str | Path | None = None,
    config_path: str | Path = DEFAULT_CONFIG,
    authenticate_values: bool = True,
) -> dict[str, Any]:
    """Reauthenticate the four source-city 46-feature outputs and audits."""

    settings = load_predictor_extension_settings(project_root, config_path)
    permit = load_m3_source_predictor_extension_runtime_permit(
        settings.root, authorization_path, settings.config_path
    )
    acquisition = authenticate_source_predictor_acquisition_completion(
        settings.root,
        authorization_path=authorization_path,
        config_path=settings.config_path,
        authenticate_files=authenticate_values,
    )
    path = (
        settings.predictor_completion
        if completion_path is None
        else _inside(settings.root, str(completion_path), label="Predictor completion")
    )
    if path != settings.predictor_completion:
        raise M3SourcePredictorExtensionError("Predictor completion path changed.")
    payload = _read_committed(path, label="Source predictors 46 completion")
    tables = payload.get("city_tables")
    audit = payload.get("audit")
    city_context = payload.get("city_context")
    if (
        payload.get("state") != "source_predictors_46_complete"
        or payload.get("authorization_commit_sha256") != permit["commit_sha256"]
        or payload.get("acquisition_completion_commit_sha256") != acquisition["commit_sha256"]
        or payload.get("source_qa_candidates_completion_commit_sha256")
        != permit["source_qa_candidates_completion_commit_sha256"]
        or payload.get("feature_names") != list(FEATURE_NAMES)
        or payload.get("feature_count") != len(FEATURE_NAMES)
        or payload.get("context_features") != list(CONTEXT_FEATURES)
        or payload.get("required_columns") != list(REQUIRED_COLUMNS)
        or payload.get("extension_key_universe") != permit["key_universe"]
        or payload.get("next_safe_stage")
        != "create_independent_nested_whole_city_loso_authorization"
        or not isinstance(tables, list)
        or not isinstance(audit, Mapping)
        or not isinstance(city_context, list)
        or any(
            audit.get(key) != expected
            for key, expected in {
                "offline_network_requests": 0,
                "offline_href_reads": 0,
                "blind_test_city_accessed": False,
                "target_or_landsat_values_read": False,
                "model_fit_select_predict_or_score_performed": False,
                "old_predictor_or_runtime_mutated": False,
            }.items()
        )
    ):
        raise M3SourcePredictorExtensionError("Predictor completion scope changed.")
    by_city = {
        str(record.get("city_id")): record for record in tables if isinstance(record, Mapping)
    }
    if tuple(by_city) != SOURCE_CITY_IDS or len(by_city) != len(SOURCE_CITY_IDS):
        raise M3SourcePredictorExtensionError("Predictor city table set changed.")
    context_by_city = {
        str(record.get("city_id")): record for record in city_context if isinstance(record, Mapping)
    }
    if tuple(context_by_city) != SOURCE_CITY_IDS:
        raise M3SourcePredictorExtensionError("Predictor city context set changed.")
    if not authenticate_values:
        for city_id, record in by_city.items():
            expected = settings.output_root / city_id / "predictors_46.parquet"
            _completion_metadata_record_path(
                settings,
                record,
                allowed_root=settings.output_root,
                label=f"{city_id} predictor table",
                expected_path=expected,
            )
            if (
                record.get("city_id") != city_id
                or record.get("rows") != EXPECTED_CITY_COUNTS[city_id]["all_rows"]
                or any(
                    not _is_sha256(record.get(key))
                    for key in (
                        "schema_sha256",
                        "semantic_sha256",
                        "tract_geoid_set_sha256",
                    )
                )
            ):
                raise M3SourcePredictorExtensionError(
                    f"{city_id} predictor table metadata changed."
                )
            context = context_by_city[city_id]
            if (
                tuple(context)
                != (
                    "city_id",
                    "city_centroid_latitude_deg",
                    "algorithm",
                    "geography_commit_sha256",
                    "worldcover_support_commit_sha256",
                    "city_boundary_sha256",
                    "target_grid_crs",
                )
                or context.get("city_id") != city_id
                or not isinstance(context.get("city_centroid_latitude_deg"), (int, float))
                or isinstance(context.get("city_centroid_latitude_deg"), bool)
                or not (-90.0 <= float(context["city_centroid_latitude_deg"]) <= 90.0)
                or context.get("algorithm") != CITY_CENTROID_ALGORITHM
                or any(
                    not _is_sha256(context.get(key))
                    for key in (
                        "geography_commit_sha256",
                        "worldcover_support_commit_sha256",
                        "city_boundary_sha256",
                    )
                )
                or not isinstance(context.get("target_grid_crs"), str)
                or not context["target_grid_crs"]
            ):
                raise M3SourcePredictorExtensionError(
                    f"{city_id} predictor context metadata changed."
                )
        return payload

    import numpy as np
    import pandas as pd

    expected_columns = REQUIRED_COLUMNS
    universe = {row["city_id"]: row for row in permit["key_universe"]["all_source_cities"]}
    for city_id, record in by_city.items():
        table_path = _completion_record_path(
            settings,
            record,
            allowed_root=settings.output_root,
            label=f"{city_id} predictor table",
        )
        frame = pd.read_parquet(table_path)
        if (
            tuple(frame.columns) != expected_columns
            or len(frame) != universe[city_id]["row_count"]
            or record.get("rows") != len(frame)
            or frame["city_id"].astype(str).nunique() != 1
            or str(frame["city_id"].astype(str).iloc[0]) != city_id
            or frame.duplicated(["city_id", "tract_geoid", "target_date"]).any()
        ):
            raise M3SourcePredictorExtensionError(f"{city_id} predictor table changed.")
        dates = tuple(sorted(pd.to_datetime(frame["target_date"]).dt.date.astype(str).unique()))
        if dates != tuple(universe[city_id]["target_dates"]):
            raise M3SourcePredictorExtensionError(f"{city_id} predictor dates changed.")
        geoid_counts = frame.groupby("target_date", sort=True)["tract_geoid"].nunique()
        if (
            len(geoid_counts) != universe[city_id]["target_date_count"]
            or not (geoid_counts == universe[city_id]["tract_count"]).all()
        ):
            raise M3SourcePredictorExtensionError(f"{city_id} tract keys changed.")
        canonical_geoids = authenticated_static_tract_geoids(
            settings.root, permit, city_id, config_path=settings.config_path
        )
        expected_geoid_set = set(canonical_geoids)
        for target_date, date_frame in frame.groupby("target_date", sort=True):
            if set(date_frame["tract_geoid"].astype(str)) != expected_geoid_set:
                raise M3SourcePredictorExtensionError(
                    f"{city_id}/{target_date} exact canonical GEOID set changed."
                )
        geoid_set_sha = canonical_sha256(canonical_geoids)
        if record.get("tract_geoid_set_sha256") != geoid_set_sha:
            raise M3SourcePredictorExtensionError(
                f"{city_id} canonical GEOID semantic hash changed."
            )
        base_values = frame[list((*STATIC_FEATURES, *CALENDAR_FEATURES, *DAYMET_FEATURES))]
        if not np.isfinite(base_values.to_numpy(dtype=float)).all():
            raise M3SourcePredictorExtensionError(f"{city_id} base predictor is non-finite.")
        sentinel_missing = frame[list(SENTINEL_FEATURES)].isna()
        if not sentinel_missing.nunique(axis=1).eq(1).all():
            raise M3SourcePredictorExtensionError(
                f"{city_id} Sentinel availability is not all-five-or-none."
            )
        schema = [(column, str(dtype)) for column, dtype in frame.dtypes.items()]
        if record.get("schema_sha256") != canonical_sha256(schema):
            raise M3SourcePredictorExtensionError(f"{city_id} schema hash changed.")
        semantic = canonical_sha256(
            frame.sort_values(["city_id", "target_date", "tract_geoid"], kind="stable").to_dict(
                "records"
            )
        )
        if record.get("semantic_sha256") != semantic:
            raise M3SourcePredictorExtensionError(f"{city_id} semantic hash changed.")
        expected_context = authenticated_city_centroid_latitude(
            settings.root, permit, city_id, config_path=settings.config_path
        )
        if context_by_city[city_id] != expected_context:
            raise M3SourcePredictorExtensionError(f"{city_id} centroid context evidence changed.")
    return payload


def authenticate_source_predictors_46_completion_metadata(
    project_root: str | Path,
    *,
    authorization_path: str | Path = AUTHORIZATION_PATH,
    completion_path: str | Path | None = None,
    config_path: str | Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Authenticate completion JSON/code lineage without touching predictor files."""

    return authenticate_source_predictors_46_completion(
        project_root,
        authorization_path=authorization_path,
        completion_path=completion_path,
        config_path=config_path,
        authenticate_values=False,
    )
