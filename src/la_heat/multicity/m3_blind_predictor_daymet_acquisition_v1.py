"""Resumable authorized Daymet acquisition for M3 blind predictors."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import pandas as pd
import requests

from la_heat.daymet_grid import (
    DaymetGranule,
    build_daymet_direct_subset_url,
    inspect_daymet_netcdf,
    validate_daymet_direct_subset_spec,
)
from la_heat.multicity.m3_blind_predictor_build_authorization_v1 import BLIND_CITY_IDS
from la_heat.multicity.m3_blind_predictor_metadata_v1 import (
    OUTPUT_ROOT as METADATA_ROOT,
)
from la_heat.multicity.m3_blind_predictor_metadata_v1 import (
    authenticate_metadata_completion,
)
from la_heat.provenance import atomic_json, canonical_sha256, sha256_file
from la_heat.weather_daymet import DEFAULT_DAYMET_VARIABLES

ALGORITHM_VERSION: Final = "m3-blind-predictor-daymet-acquisition-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_BLIND_PREDICTOR_DAYMET_ACQUISITION_V1_AUTHORIZATION.json"
)
OUTPUT_ROOT: Final = Path(
    "data/raw/multicity/m3_blind_predictor_build_v1/daymet"
)
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_predictor_build_v1/"
    "M3_BLIND_PREDICTOR_DAYMET_ACQUISITION_COMPLETE.json"
)
CODE_PATHS: Final = (
    "scripts/run_m3_blind_predictor_daymet_acquisition_v1.py",
    "src/la_heat/daymet_grid.py",
    "src/la_heat/multicity/m3_blind_predictor_daymet_acquisition_v1.py",
    "src/la_heat/multicity/m3_blind_predictor_metadata_v1.py",
    "src/la_heat/provenance.py",
)


class M3BlindPredictorDaymetAcquisitionError(RuntimeError):
    """Raised when the Daymet acquisition contract drifts."""


def _inside(root: Path, value: str | Path) -> Path:
    path = (root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    if not path.is_relative_to(root):
        raise M3BlindPredictorDaymetAcquisitionError("Path escapes project root.")
    return path


def _record(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _committed(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(payload)
    return result


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if payload.get("commit_sha256") != canonical_sha256(body):
        raise M3BlindPredictorDaymetAcquisitionError(f"Commit changed: {path}")
    return payload


def _write_exclusive(payload: Mapping[str, Any], path: Path) -> None:
    if path.exists():
        if _read(path) != dict(payload):
            raise M3BlindPredictorDaymetAcquisitionError(f"Artifact drifted: {path}")
        return
    atomic_json(dict(payload), path)


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    metadata = authenticate_metadata_completion(root)
    code = [_record(root, _inside(root, path)) for path in CODE_PATHS]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "m3_blind_predictor_daymet_acquisition_authorized",
        "metadata_completion_commit_sha256": metadata["commit_sha256"],
        "blind_city_ids": list(BLIND_CITY_IDS),
        "year": 2025,
        "variables": list(DEFAULT_DAYMET_VARIABLES),
        "task_count": len(BLIND_CITY_IDS) * len(DEFAULT_DAYMET_VARIABLES),
        "code_identity": {"files": code, "set_sha256": canonical_sha256(code)},
        "network_contract": {
            "scheme": "https",
            "host": "opendap.earthdata.nasa.gov",
            "direct_dap4_subsets_only": True,
            "maximum_bytes_per_task": 1_000_000_000,
            "credentials_persisted": False,
        },
        "permissions": {
            "read_parent_bound_daymet_metadata": True,
            "download_daymet_2025_subsets": True,
            "read_sentinel_static_landsat_qa_or_target_values": False,
            "fit_predict_score_or_evaluate": False,
        },
        "output_root": OUTPUT_ROOT.as_posix(),
        "completion": COMPLETION_PATH.as_posix(),
    }
    payload["claim_id"] = canonical_sha256(payload)
    return _committed(payload)


def create_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    expected = build_authorization(root)
    _write_exclusive(expected, _inside(root, AUTHORIZATION_PATH))
    return authenticate_authorization(root)


def authenticate_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observed = _read(_inside(root, AUTHORIZATION_PATH))
    if observed != build_authorization(root):
        raise M3BlindPredictorDaymetAcquisitionError("Authorization drifted.")
    return observed


def _metadata(root: Path, city_id: str) -> tuple[dict[str, Any], pd.DataFrame]:
    marker = _read(_inside(root, METADATA_ROOT / city_id / "METADATA_COMPLETE.json"))
    record = marker["outputs"]["daymet_granules"]
    path = _inside(root, record["path"])
    if path.stat().st_size != record["bytes"] or sha256_file(path) != record["sha256"]:
        raise M3BlindPredictorDaymetAcquisitionError("Daymet metadata changed.")
    return marker, pd.read_parquet(path)


def _validate(path: Path, *, variable: str, window: Mapping[str, Any], bbox: list[float]) -> None:
    spec = inspect_daymet_netcdf(path, variable=variable, year=2025, final_test_year=2026)
    validate_daymet_direct_subset_spec(
        spec,
        y_indices=tuple(window["y_indices_inclusive"]),
        x_indices=tuple(window["x_indices_inclusive"]),
        bbox_wgs84=bbox,
    )


def _download(url: str, destination: Path) -> None:
    partial = destination.with_suffix(destination.suffix + ".partial")
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with requests.get(url, stream=True, timeout=(30, 900)) as response:
            response.raise_for_status()
            with partial.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > 1_000_000_000:
                        raise M3BlindPredictorDaymetAcquisitionError(
                            "Daymet task exceeded 1 GB."
                        )
                    handle.write(chunk)
        if written == 0:
            raise M3BlindPredictorDaymetAcquisitionError("Empty Daymet response.")
        partial.replace(destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def _task(root: Path, city_id: str, variable: str) -> dict[str, Any]:
    permit = authenticate_authorization(root)
    metadata, granules = _metadata(root, city_id)
    matches = granules.loc[granules["variable"].astype(str) == variable]
    if len(matches) != 1:
        raise M3BlindPredictorDaymetAcquisitionError("Granule identity changed.")
    row = matches.iloc[0]
    granule = DaymetGranule(
        concept_id=str(row["concept_id"]),
        title=str(row["title"]),
        variable=variable,
        year=2025,
        size_mb=float(row["size_mb"]),
        https_url=str(row["https_url"]),
        opendap_url=str(row["opendap_url"]),
        updated_at=None if pd.isna(row["updated_at"]) else str(row["updated_at"]),
    )
    window = metadata["daymet_window"]
    url = build_daymet_direct_subset_url(
        granule,
        y_indices=tuple(window["y_indices_inclusive"]),
        x_indices=tuple(window["x_indices_inclusive"]),
    )
    destination = _inside(root, OUTPUT_ROOT / city_id / f"{variable}_2025.nc")
    marker_path = destination.with_suffix(".COMPLETE.json")
    if marker_path.is_file():
        marker = _read(marker_path)
        locked = marker["output"]
        if (
            destination.stat().st_size != locked["bytes"]
            or sha256_file(destination) != locked["sha256"]
        ):
            raise M3BlindPredictorDaymetAcquisitionError("Completed subset changed.")
        return marker
    if destination.is_file():
        try:
            _validate(destination, variable=variable, window=window, bbox=metadata["bbox_wgs84"])
        except (OSError, ValueError):
            destination.unlink()
    if not destination.is_file():
        authenticate_authorization(root)
        _download(url, destination)
    _validate(destination, variable=variable, window=window, bbox=metadata["bbox_wgs84"])
    marker = _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "daymet_subset_complete",
            "authorization_commit_sha256": permit["commit_sha256"],
            "city_id": city_id,
            "year": 2025,
            "variable": variable,
            "output": _record(root, destination),
            "audit": {
                "credentials_or_urls_persisted": False,
                "sentinel_static_landsat_qa_or_target_values_read": False,
            },
        }
    )
    _write_exclusive(marker, marker_path)
    print(f"DAYMET_COMPLETE {city_id} {variable}", flush=True)
    return marker


def run_acquisition(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    tasks = [
        _task(root, city_id, variable)
        for city_id in BLIND_CITY_IDS
        for variable in DEFAULT_DAYMET_VARIABLES
    ]
    completion = _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_predictor_daymet_acquisition_complete",
            "authorization_commit_sha256": permit["commit_sha256"],
            "task_count": len(tasks),
            "outputs": [
                {
                    "city_id": row["city_id"],
                    "variable": row["variable"],
                    "commit_sha256": row["commit_sha256"],
                }
                for row in tasks
            ],
            "audit": {
                "credentials_or_urls_persisted": False,
                "sentinel_static_landsat_qa_or_target_values_read": False,
                "model_fit_predict_score_or_evaluate": False,
            },
            "next_safe_stage": (
                "authorize_offline_daymet_compilation_or_"
                "next_public_predictor_acquisition"
            ),
        }
    )
    _write_exclusive(completion, _inside(root, COMPLETION_PATH))
    return authenticate_completion(root)


def authenticate_completion(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    completion = _read(_inside(root, COMPLETION_PATH))
    if (
        completion.get("authorization_commit_sha256") != permit["commit_sha256"]
        or completion.get("task_count") != 24
        or completion.get("audit", {}).get(
            "sentinel_static_landsat_qa_or_target_values_read"
        )
        is not False
    ):
        raise M3BlindPredictorDaymetAcquisitionError("Completion changed.")
    return completion
