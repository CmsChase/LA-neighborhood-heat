"""Authorized offline compilation of complete blind-city predictor tables."""

from __future__ import annotations

import json
import socket
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from la_heat.calendar_features import build_calendar_features
from la_heat.daymet_feature_stage import compile_daymet_feature_tables
from la_heat.multicity import m3_blind_predictor_daymet_acquisition_v1 as daymet
from la_heat.multicity import m3_blind_predictor_sentinel_static_assembly_v1 as assembled
from la_heat.multicity import m3_blind_predictor_sentinel_static_runtime_v1 as support_runtime
from la_heat.multicity.m3_blind_predictor_build_authorization_v1 import (
    BLIND_CITY_IDS,
    EXPECTED_CITY_COUNTS,
)
from la_heat.multicity.m3_source_predictor_extension_authorization_v1 import (
    CALENDAR_FEATURES,
    CITY_CENTROID_ALGORITHM,
    DAYMET_FEATURES,
    FEATURE_NAMES,
    REQUIRED_COLUMNS,
    SENTINEL_FEATURES,
    STATIC_FEATURES,
)
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    parquet_file_record,
    sha256_file,
)
from la_heat.weather_daymet import DEFAULT_DAYMET_VARIABLES

ALGORITHM_VERSION: Final = "m3-blind-predictor-daymet-compilation-v1"
PARENT_AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_BLIND_PREDICTOR_BUILD_V1_PARENT_AUTHORIZATION.json"
)
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_BLIND_PREDICTOR_DAYMET_COMPILATION_V1_AUTHORIZATION.json"
)
OUTPUT_ROOT: Final = Path(
    "data/processed/multicity/m3_blind_predictor_build_v1/predictors_46_v1"
)
STATUS_PATH: Final = OUTPUT_ROOT / "runtime/status.json"
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_predictor_build_v1/"
    "M3_BLIND_PREDICTORS_46_COMPLETE.json"
)
EXPECTED_PARENT_COMMIT: Final = (
    "1a704fca3848471dfba16c28bf2dd2e282343af6ac2aa24e3cbbd2ef44d790f8"
)
EXPECTED_STATIC_SENTINEL_COMMIT: Final = (
    "bc56348b43c12f2d9e17cf67831190e47f1e3c49df1ad90caecd2f0b1a623f42"
)
EXPECTED_DAYMET_COMMIT: Final = (
    "e3269d965a97c88a43db82c61fa8930a19bf6aa00873efb981317817f84cba1a"
)
CODE_PATHS: Final = (
    "scripts/run_m3_blind_predictor_daymet_compilation_v1.py",
    "src/la_heat/multicity/m3_blind_predictor_daymet_compilation_v1.py",
    "src/la_heat/calendar_features.py",
    "src/la_heat/daymet_feature_stage.py",
    "src/la_heat/daymet_grid.py",
)


class M3BlindDaymetCompilationError(RuntimeError):
    """Raised when the offline compilation contract or inputs drift."""


def _inside(root: Path, value: str | Path) -> Path:
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise M3BlindDaymetCompilationError("Path escapes project root.")
    return path


def _read_committed(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise M3BlindDaymetCompilationError(f"Cannot read committed file: {path}") from error
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if payload.get("commit_sha256") != canonical_sha256(body):
        raise M3BlindDaymetCompilationError(f"Invalid commit: {path}")
    return payload


def _record(root: Path, value: str | Path) -> dict[str, Any]:
    path = _inside(root, value)
    if not path.is_file():
        raise M3BlindDaymetCompilationError(f"Missing bound file: {value}")
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _committed(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(payload)
    return result


def _write_exclusive(payload: Mapping[str, Any], path: Path) -> None:
    if path.exists():
        if _read_committed(path) != dict(payload):
            raise M3BlindDaymetCompilationError(f"Append-only artifact drifted: {path}")
        return
    atomic_json(dict(payload), path)


def _anchor(root: Path, path: Path, expected_commit: str) -> dict[str, Any]:
    payload = _read_committed(_inside(root, path))
    if payload.get("commit_sha256") != expected_commit:
        raise M3BlindDaymetCompilationError(f"Input completion changed: {path}")
    return {**_record(root, path), "commit_sha256": expected_commit}


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    """Build the permit without opening any predictor or raster value file."""

    root = Path(project_root).resolve()
    parent = _anchor(root, PARENT_AUTHORIZATION_PATH, EXPECTED_PARENT_COMMIT)
    static_sentinel = _anchor(
        root, assembled.COMPLETION_PATH, EXPECTED_STATIC_SENTINEL_COMMIT
    )
    daymet_completion = _anchor(root, daymet.COMPLETION_PATH, EXPECTED_DAYMET_COMMIT)
    code = [_record(root, path) for path in CODE_PATHS]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "m3_blind_predictor_daymet_offline_compilation_authorized",
        "inputs": {
            "parent_authorization": parent,
            "sentinel_static_completion": static_sentinel,
            "daymet_acquisition_completion": daymet_completion,
        },
        "blind_city_ids": list(BLIND_CITY_IDS),
        "expected": {
            "row_count": sum(row["row_count"] for row in EXPECTED_CITY_COUNTS.values()),
            "feature_count": len(FEATURE_NAMES),
            "required_columns": list(REQUIRED_COLUMNS),
            "city_count": 4,
        },
        "code_identity": {"files": code, "set_sha256": canonical_sha256(code)},
        "permissions": {
            "read_authenticated_static_sentinel_daymet_and_support_values": True,
            "write_predictors_46_and_daymet_audits": True,
            "network_or_href_reads": False,
            "read_landsat_qa_or_target_values": False,
            "fit_predict_score_or_evaluate": False,
        },
        "runtime_contract": {
            "authenticate_before_each_city_value_read": True,
            "one_resumable_task_per_city": True,
            "network_and_href_read_count": 0,
            "blind_targets_remain_sealed": True,
            "sentinel_window_ends_before_target_date": True,
        },
        "authorization_access_audit": {
            "predictor_or_raster_value_files_opened_or_statted": 0,
            "network_or_href_reads": 0,
            "landsat_qa_or_target_values_read": False,
            "fit_predict_score_or_evaluate": False,
        },
        "output_root": OUTPUT_ROOT.as_posix(),
        "completion": COMPLETION_PATH.as_posix(),
        "next_safe_stage": "run_offline_four_city_daymet_compilation",
    }
    payload["run_id"] = f"m3-blind-predictors-46-v1-{canonical_sha256(payload)[:16]}"
    payload["claim_id"] = canonical_sha256(payload)
    return _committed(payload)


def create_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    expected = build_authorization(root)
    _write_exclusive(expected, _inside(root, AUTHORIZATION_PATH))
    return authenticate_authorization(root)


def authenticate_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observed = _read_committed(_inside(root, AUTHORIZATION_PATH))
    if observed != build_authorization(root):
        raise M3BlindDaymetCompilationError("Compilation authorization drifted.")
    return observed


@contextmanager
def _network_denied() -> Iterator[None]:
    original = socket.socket

    class DeniedSocket:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise M3BlindDaymetCompilationError("Network is forbidden during compilation.")

    socket.socket = DeniedSocket  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket = original  # type: ignore[assignment]


def _verify_output(root: Path, record: Mapping[str, Any]) -> Path:
    path = _inside(root, str(record.get("path", "")))
    if (
        not path.is_file()
        or path.stat().st_size != record.get("bytes")
        or sha256_file(path) != record.get("sha256")
    ):
        raise M3BlindDaymetCompilationError("Authenticated input file changed.")
    return path


def _city_contract(
    root: Path, permit: Mapping[str, Any], city_id: str
) -> Mapping[str, Any]:
    parent_path = Path(str(permit["inputs"]["parent_authorization"]["path"]))
    parent = _read_committed(_inside(root, parent_path))
    matches = [row for row in parent["key_universe"]["cities"] if row["city_id"] == city_id]
    if len(matches) != 1:
        raise M3BlindDaymetCompilationError("Blind city key contract changed.")
    return matches[0]


def _city_context(root: Path, contract: Mapping[str, Any], target_crs: str) -> dict[str, Any]:
    import geopandas as gpd
    import shapely

    checkpoint = _read_committed(_inside(root, contract["checkpoint"]["path"]))
    record = checkpoint["census"]["outputs"]["city_boundary"]
    boundary_path = _verify_output(root, record)
    boundary = gpd.read_parquet(boundary_path)
    if boundary.empty or boundary.crs is None:
        raise M3BlindDaymetCompilationError("City boundary geometry changed.")
    union = shapely.union_all(boundary.to_crs(target_crs).geometry.to_numpy())
    point = gpd.GeoSeries([union.centroid], crs=target_crs).to_crs("EPSG:4326").iloc[0]
    latitude = float(point.y)
    if not np.isfinite(latitude) or not -90.0 <= latitude <= 90.0:
        raise M3BlindDaymetCompilationError("City centroid latitude is invalid.")
    return {
        "city_id": contract["city_id"],
        "city_centroid_latitude_deg": latitude,
        "algorithm": CITY_CENTROID_ALGORITHM,
        "checkpoint_commit_sha256": checkpoint["commit_sha256"],
        "city_boundary_sha256": record["sha256"],
        "target_grid_crs": target_crs,
    }


def _load_static_sentinel(root: Path, city_id: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    completion = _read_committed(_inside(root, assembled.COMPLETION_PATH))
    static_marker = _read_committed(
        _inside(
            root,
            assembled.OUTPUT_ROOT / "static" / city_id / "static_features_provenance.json",
        )
    )
    sentinel_marker = _read_committed(
        _inside(root, assembled.OUTPUT_ROOT / "sentinel" / city_id / "SENTINEL_COMPLETE.json")
    )
    if (
        static_marker["commit_sha256"]
        != completion["result_commits"][f"{city_id}/static_finalize"]
        or sentinel_marker["commit_sha256"]
        != completion["result_commits"][f"{city_id}/sentinel_compile"]
    ):
        raise M3BlindDaymetCompilationError("Static/Sentinel city lineage changed.")
    static_path = _inside(
        root, assembled.OUTPUT_ROOT / "static" / city_id / "static_features.parquet"
    )
    sentinel_path = _inside(
        root, assembled.OUTPUT_ROOT / "sentinel" / city_id / "sentinel_features.parquet"
    )
    for path, record in (
        (static_path, static_marker["output_files"]["static_features.parquet"]),
        (sentinel_path, sentinel_marker["outputs"]["sentinel_features.parquet"]),
    ):
        if (
            not path.is_file()
            or path.stat().st_size != record.get("bytes")
            or sha256_file(path) != record.get("sha256")
        ):
            raise M3BlindDaymetCompilationError("Static/Sentinel value file changed.")
    return pd.read_parquet(static_path), pd.read_parquet(sentinel_path)


def _daymet_records(root: Path, city_id: str) -> pd.DataFrame:
    completion = _read_committed(_inside(root, daymet.COMPLETION_PATH))
    expected = {
        row["variable"]: row["commit_sha256"]
        for row in completion["outputs"]
        if row["city_id"] == city_id
    }
    rows = []
    for variable in DEFAULT_DAYMET_VARIABLES:
        path = _inside(root, daymet.OUTPUT_ROOT / city_id / f"{variable}_2025.nc")
        marker = _read_committed(path.with_suffix(".COMPLETE.json"))
        if marker["commit_sha256"] != expected.get(variable):
            raise M3BlindDaymetCompilationError("Daymet city-variable lineage changed.")
        rows.append(
            {
                "path": _verify_output(root, marker["output"]),
                "year": 2025,
                "variable": variable,
            }
        )
    return pd.DataFrame(rows)


def _status(root: Path, permit: Mapping[str, Any], **changes: Any) -> None:
    payload = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "run_id": permit["run_id"],
        "state": "running",
        "completed_city_count": 0,
        "total_city_count": 4,
        "city_id": None,
        "network_request_count": 0,
        "href_read_count": 0,
        "blind_targets_sealed": True,
        "model_fit_predict_score_or_evaluate": False,
    }
    payload.update(changes)
    atomic_json(payload, _inside(root, STATUS_PATH))


def _validate_result(result: pd.DataFrame, city_id: str) -> dict[str, int]:
    expected = EXPECTED_CITY_COUNTS[city_id]
    fixed = result.loc[:, [*STATIC_FEATURES, *CALENDAR_FEATURES]].to_numpy(dtype=float)
    daymet_values = result.loc[:, DAYMET_FEATURES].to_numpy(dtype=float)
    daymet_missing = np.isnan(daymet_values).sum(axis=1)
    sentinel_missing = result.loc[:, SENTINEL_FEATURES].isna().sum(axis=1)
    if (
        len(result) != expected["row_count"]
        or tuple(result.columns) != REQUIRED_COLUMNS
        or result.duplicated(["city_id", "tract_geoid", "target_date"]).any()
        or not np.isfinite(fixed).all()
        or np.isinf(daymet_values).any()
        or not np.isin(daymet_missing, [0, len(DAYMET_FEATURES)]).all()
        or not sentinel_missing.isin([0, len(SENTINEL_FEATURES)]).all()
    ):
        raise M3BlindDaymetCompilationError("Compiled predictor semantics changed.")
    return {
        "daymet_missing_row_count": int((daymet_missing == len(DAYMET_FEATURES)).sum()),
        "daymet_missing_cell_count": int(daymet_missing.sum()),
        "sentinel_missing_row_count": int(
            sentinel_missing.eq(len(SENTINEL_FEATURES)).sum()
        ),
        "rows_dropped_or_imputed": 0,
    }


def _build_city(root: Path, permit: Mapping[str, Any], city_id: str) -> dict[str, Any]:
    marker_path = _inside(root, OUTPUT_ROOT / city_id / "CITY_PREDICTORS_46_COMPLETE.json")
    if marker_path.is_file():
        marker = _read_committed(marker_path)
        if marker.get("authorization_commit_sha256") != permit["commit_sha256"]:
            raise M3BlindDaymetCompilationError("Existing city output has another permit.")
        _verify_output(root, marker["output"])
        return marker

    authenticate_authorization(root)
    contract = _city_contract(root, permit, city_id)
    static, sentinel = _load_static_sentinel(root, city_id)
    support = support_runtime._city_support(root, city_id)
    geoids = tuple(sorted(static["tract_geoid"].astype(str)))
    dates = tuple(pd.Timestamp(value) for value in contract["target_dates"])
    keys = pd.MultiIndex.from_product(
        [geoids, dates], names=["tract_geoid", "target_date"]
    ).to_frame(index=False)
    calendar = build_calendar_features(keys.copy(), final_test_year=2025, unlock_final_test=True)
    compiled = compile_daymet_feature_tables(
        _daymet_records(root, city_id),
        keys.copy(),
        zone_raster=support.zones,
        eligible_land_mask=support.eligible_land,
        tract_geoids=support.tract_geoids,
        target_transform=support.grid.transform,
        target_crs=support.grid.crs,
        windows=(1, 3, 7),
        final_test_year=2026,
    )
    static = static.loc[:, ["tract_geoid", *STATIC_FEATURES]].copy()
    static["tract_geoid"] = static["tract_geoid"].astype(str)
    sentinel = sentinel.loc[:, ["city_id", "tract_geoid", "target_date", *SENTINEL_FEATURES]].copy()
    sentinel["city_id"] = sentinel["city_id"].astype(str)
    sentinel["tract_geoid"] = sentinel["tract_geoid"].astype(str)
    sentinel["target_date"] = pd.to_datetime(sentinel["target_date"])
    result = keys.merge(static, on="tract_geoid", how="left", validate="many_to_one")
    result = result.merge(calendar, on=["tract_geoid", "target_date"], validate="one_to_one")
    result = result.merge(
        compiled.features,
        on=["tract_geoid", "target_date"],
        validate="one_to_one",
    )
    result.insert(0, "city_id", city_id)
    result = result.merge(
        sentinel,
        on=["city_id", "tract_geoid", "target_date"],
        how="left",
        validate="one_to_one",
    )
    result = result.loc[:, REQUIRED_COLUMNS].sort_values(
        ["city_id", "target_date", "tract_geoid"], kind="stable"
    ).reset_index(drop=True)
    missingness = _validate_result(result, city_id)
    context = _city_context(root, contract, str(support.grid.crs))
    output_dir = marker_path.parent
    output_path = output_dir / "predictors_46.parquet"
    atomic_parquet(result, output_path)
    audits = {
        "daymet_feature_audit.parquet": compiled.audit,
        "daymet_fixed_cell_weights.parquet": compiled.weights,
        "daymet_tract_daily.parquet": compiled.tract_daily,
    }
    audit_records = {}
    for name, frame in audits.items():
        path = output_dir / name
        atomic_parquet(frame, path)
        audit_records[name] = _record(root, path)
    output = parquet_file_record(output_path, result)
    output["path"] = output_path.relative_to(root).as_posix()
    output["semantic_sha256"] = canonical_frame_sha256(
        result, sort_by=["city_id", "target_date", "tract_geoid"]
    )
    marker = _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_city_predictors_46_complete",
            "authorization_commit_sha256": permit["commit_sha256"],
            "city_id": city_id,
            "feature_count": 46,
            "feature_names": list(FEATURE_NAMES),
            "required_columns": list(REQUIRED_COLUMNS),
            "city_context": context,
            "predictor_support": missingness,
            "output": output,
            "audit_outputs": audit_records,
            "audit": {
                "network_requests": 0,
                "href_reads": 0,
                "landsat_qa_or_target_values_read": False,
                "fit_predict_score_or_evaluate": False,
                "blind_targets_sealed": True,
            },
        }
    )
    _write_exclusive(marker, marker_path)
    return marker


def run(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    markers = []
    with _network_denied():
        for city_id in BLIND_CITY_IDS:
            _status(root, permit, completed_city_count=len(markers), city_id=city_id)
            markers.append(_build_city(root, permit, city_id))
            print(f"BLIND_PREDICTORS_46_COMPLETE {city_id} {len(markers)}/4", flush=True)
    completion = _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_predictors_46_complete",
            "authorization_commit_sha256": permit["commit_sha256"],
            "city_count": 4,
            "row_count": sum(marker["output"]["rows"] for marker in markers),
            "feature_count": 46,
            "feature_names": list(FEATURE_NAMES),
            "required_columns": list(REQUIRED_COLUMNS),
            "city_outputs": [marker["output"] for marker in markers],
            "city_context": [marker["city_context"] for marker in markers],
            "predictor_support": {
                "daymet_missing_row_count": sum(
                    marker["predictor_support"]["daymet_missing_row_count"]
                    for marker in markers
                ),
                "daymet_missing_cell_count": sum(
                    marker["predictor_support"]["daymet_missing_cell_count"]
                    for marker in markers
                ),
                "sentinel_missing_row_count": sum(
                    marker["predictor_support"]["sentinel_missing_row_count"]
                    for marker in markers
                ),
                "rows_dropped_or_imputed": 0,
                "deferred_imputation": "frozen_source_fit_training_fold_median_plus_indicator",
            },
            "city_completion_commits": {
                marker["city_id"]: marker["commit_sha256"] for marker in markers
            },
            "audit": {
                "network_requests": 0,
                "href_reads": 0,
                "landsat_qa_or_target_values_read": False,
                "fit_predict_score_or_evaluate": False,
                "blind_targets_sealed": True,
            },
            "next_safe_stage": "authorize_prediction_before_any_blind_target_access",
        }
    )
    _write_exclusive(completion, _inside(root, COMPLETION_PATH))
    _status(root, permit, state="complete", completed_city_count=4)
    return authenticate_completion(root)


def authenticate_completion(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    completion = _read_committed(_inside(root, COMPLETION_PATH))
    if (
        completion.get("authorization_commit_sha256") != permit["commit_sha256"]
        or completion.get("city_count") != 4
        or completion.get("row_count") != 23_667
        or completion.get("feature_count") != 46
        or tuple(completion.get("required_columns", ())) != REQUIRED_COLUMNS
        or completion.get("audit", {}).get("network_requests") != 0
        or completion.get("audit", {}).get("landsat_qa_or_target_values_read") is not False
    ):
        raise M3BlindDaymetCompilationError("Predictor completion changed.")
    for city_id, commit in completion["city_completion_commits"].items():
        marker = _read_committed(
            _inside(root, OUTPUT_ROOT / city_id / "CITY_PREDICTORS_46_COMPLETE.json")
        )
        if marker["commit_sha256"] != commit:
            raise M3BlindDaymetCompilationError("City completion lineage changed.")
        _verify_output(root, marker["output"])
    return completion
