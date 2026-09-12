"""Authorized offline assembly of blind-city Sentinel and static predictors."""

from __future__ import annotations

import json
import math
import socket
import tomllib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

from la_heat.multicity import m3_blind_predictor_sentinel_static_runtime_v1 as acquired
from la_heat.multicity import portable_predictor_components as components
from la_heat.multicity import portable_sentinel_build as sentinel
from la_heat.provenance import atomic_json, canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-blind-predictor-sentinel-static-assembly-v1"
CITY_IDS: Final = acquired.BLIND_CITY_IDS
CONFIG_PATH: Final = Path(
    "configs/multicity/m3_blind_predictor_sentinel_static_assembly_v1.toml"
)
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_BLIND_PREDICTOR_SENTINEL_STATIC_ASSEMBLY_V1_AUTHORIZATION.json"
)
ACQUISITION_COMPLETION_PATH: Final = acquired.COMPLETION_PATH
RUNTIME_ROOT: Final = Path(
    "data/interim/multicity/m3_blind_predictor_build_v1/"
    "sentinel_static_assembly_v1/runtime"
)
OUTPUT_ROOT: Final = Path(
    "data/processed/multicity/m3_blind_predictor_build_v1/"
    "sentinel_static_assembly_v1"
)
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_predictor_build_v1/"
    "M3_BLIND_PREDICTOR_SENTINEL_STATIC_ASSEMBLY_COMPLETE.json"
)
STATUS_PATH: Final = RUNTIME_ROOT / "status.json"
PAUSE_PATH: Final = RUNTIME_ROOT / "PAUSE_REQUESTED"
CANARY_PATH: Final = RUNTIME_ROOT / "CANARY_COMPLETE.json"
EXPECTED_ACQUISITION_COMMIT: Final = (
    "33cf3e03ba8bbc1c1de5abb6c976ca50cbeb2a93ea8207e24e520d7c9f30f2c2"
)
CODE_PATHS: Final = (
    CONFIG_PATH.as_posix(),
    "scripts/run_m3_blind_predictor_sentinel_static_assembly_v1.py",
    "src/la_heat/multicity/m3_blind_predictor_sentinel_static_assembly_v1.py",
    "src/la_heat/multicity/m3_blind_predictor_sentinel_static_runtime_v1.py",
    "src/la_heat/multicity/portable_predictor_components.py",
    "src/la_heat/multicity/portable_sentinel_build.py",
    "src/la_heat/sentinel_features.py",
    "src/la_heat/static_features.py",
)


class M3BlindAssemblyError(RuntimeError):
    """Raised when the authorization, inputs, or offline contract drifts."""


def _read_committed(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise M3BlindAssemblyError(f"Cannot read {label}.") from error
    if not isinstance(payload, dict):
        raise M3BlindAssemblyError(f"{label} must be an object.")
    unsigned = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if payload.get("commit_sha256") != canonical_sha256(unsigned):
        raise M3BlindAssemblyError(f"{label} commit is invalid.")
    return payload


def _record(root: Path, relative: str | Path) -> dict[str, Any]:
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise M3BlindAssemblyError(f"Missing bound file: {relative}")
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _load_config(root: Path) -> dict[str, Any]:
    with (root / CONFIG_PATH).open("rb") as handle:
        config = tomllib.load(handle)
    runtime = config.get("runtime", {})
    if (
        runtime.get("schema_version") != 1
        or runtime.get("algorithm_version") != ALGORITHM_VERSION
        or runtime.get("distance_chunk_size") != 100_000
        or runtime.get("network_allowed") is not False
        or runtime.get("href_reads_allowed") is not False
    ):
        raise M3BlindAssemblyError("Offline runtime config changed.")
    return config


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    """Build the code-bound permit without opening predictor value files."""

    root = Path(project_root).resolve()
    acquisition = _read_committed(
        root / ACQUISITION_COMPLETION_PATH, label="acquisition completion"
    )
    if acquisition.get("commit_sha256") != EXPECTED_ACQUISITION_COMMIT:
        raise M3BlindAssemblyError("Acquisition completion changed.")
    config = _load_config(root)
    code = [_record(root, path) for path in CODE_PATHS]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "m3_blind_predictor_sentinel_static_offline_assembly_authorized",
        "acquisition_completion": {
            **_record(root, ACQUISITION_COMPLETION_PATH),
            "commit_sha256": acquisition["commit_sha256"],
        },
        "blind_city_ids": list(CITY_IDS),
        "outputs": {
            "static_feature_count": 18,
            "sentinel_feature_count": 5,
            "daymet_feature_count": 0,
            "tract_date_key_count": 23667,
        },
        "code_identity": {"files": code, "set_sha256": canonical_sha256(code)},
        "config": {**_record(root, CONFIG_PATH), "values": config},
        "permissions": {
            "read_completed_scope_bound_sentinel_and_static_values": True,
            "write_offline_assembly_runtime_and_outputs": True,
            "network_or_href_reads": False,
            "read_daymet_landsat_qa_or_target_values": False,
            "fit_predict_score_or_evaluate": False,
        },
        "runtime_contract": {
            "authenticate_before_each_task_value_read": True,
            "network_and_href_read_count": 0,
            "resumable_city_and_distance_chunk_boundaries": True,
            "canary_required_before_full_run": True,
            "blind_targets_remain_sealed": True,
        },
        "authorization_access_audit": {
            "predictor_value_files_opened_or_statted": 0,
            "network_or_href_reads": 0,
            "daymet_landsat_qa_or_target_values_read": False,
            "model_fit_predict_score_or_evaluate": False,
        },
        "runtime_root": RUNTIME_ROOT.as_posix(),
        "output_root": OUTPUT_ROOT.as_posix(),
        "completion": COMPLETION_PATH.as_posix(),
        "next_safe_stage": "run_offline_assembly_canary_only",
    }
    payload["run_id"] = f"m3-blind-offline-assembly-v1-{canonical_sha256(payload)[:16]}"
    payload["claim_id"] = canonical_sha256(payload)
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def create_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    path = root / AUTHORIZATION_PATH
    expected = build_authorization(root)
    if path.exists():
        if _read_committed(path, label="offline assembly authorization") != expected:
            raise M3BlindAssemblyError("Append-only authorization drifted.")
    else:
        atomic_json(expected, path)
    return authenticate_authorization(root)


def authenticate_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observed = _read_committed(
        root / AUTHORIZATION_PATH, label="offline assembly authorization"
    )
    if observed != build_authorization(root):
        raise M3BlindAssemblyError("Offline assembly authorization drifted.")
    return observed


@contextmanager
def _network_denied() -> Iterator[None]:
    original = socket.socket

    class DeniedSocket:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise M3BlindAssemblyError("Network access is forbidden in offline assembly.")

    socket.socket = DeniedSocket  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket = original  # type: ignore[assignment]


def _verify_acquired_file(root: Path, path: Path) -> dict[str, Any]:
    authenticate_authorization(root)
    marker = acquired._read_committed(
        acquired._static_marker(path), label=f"{path.name} acquisition marker"
    )
    record = marker.get("output", {})
    if (
        not path.is_file()
        or path.stat().st_size != record.get("bytes")
        or sha256_file(path) != record.get("sha256")
    ):
        raise M3BlindAssemblyError(f"Acquired static file changed: {path}")
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _configure(root: Path) -> dict[str, sentinel.CityBuildContext]:
    authenticate_authorization(root)
    acquired._configure_sentinel_adapter(root)
    sentinel.SENTINEL_COMPONENT_ROOT = OUTPUT_ROOT / "sentinel"
    contexts, _ = sentinel.prepare_contexts(root, CITY_IDS)

    components.CITY_IDS = CITY_IDS
    components.COMPONENT_ROOT = OUTPUT_ROOT / "static"
    components.RUNTIME_ROOT = RUNTIME_ROOT / "components"
    components.load_city_support = lambda _root, city_id: acquired._city_support(root, city_id)

    def static_sources(_root: Path, city_id: str) -> components.StaticSourcePaths:
        authenticate_authorization(root)
        base = root / acquired.OUTPUT_ROOT / "static" / city_id
        land = base / "nlcd_2016_land_cover.tif"
        impervious = base / "nlcd_2016_impervious.tif"
        terrain = tuple(sorted((base / "terrain").glob("*.tif")))
        records = [
            _verify_acquired_file(root, path)
            for path in (land, impervious, *terrain)
        ]
        if len(terrain) != 2:
            raise M3BlindAssemblyError(f"{city_id} requires two terrain tiles.")
        return components.StaticSourcePaths(land, impervious, terrain, tuple(records))

    components._static_source_paths = static_sources
    return contexts


def _status(root: Path, permit: Mapping[str, Any], **changes: Any) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "run_id": permit["run_id"],
        "state": "ready",
        "city_id": None,
        "task": None,
        "completed_city_tasks": 0,
        "total_city_tasks": 16,
        "network_request_count": 0,
        "href_read_count": 0,
        "blind_targets_sealed": True,
        "daymet_landsat_qa_or_target_access": False,
        "model_fit_predict_score_or_evaluate": False,
    }
    payload.update(changes)
    (root / RUNTIME_ROOT).mkdir(parents=True, exist_ok=True)
    atomic_json(payload, root / STATUS_PATH)
    return payload


def _progress(root: Path, permit: Mapping[str, Any], city_id: str, task: str) -> None:
    _status(root, permit, state="running", city_id=city_id, task=task)


def _canary_current(root: Path, permit: Mapping[str, Any]) -> bool:
    try:
        marker = _read_committed(root / CANARY_PATH, label="canary completion")
    except M3BlindAssemblyError:
        return False
    return marker.get("authorization_commit_sha256") == permit["commit_sha256"]


def run(project_root: str | Path, *, canary: bool = False) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    config = _load_config(root)
    if not canary and not _canary_current(root, permit):
        raise M3BlindAssemblyError("Full run requires an authenticated canary.")
    if (root / PAUSE_PATH).exists():
        return _status(root, permit, state="paused")
    with _network_denied():
        contexts = _configure(root)
        if canary:
            city_id = str(config["canary"]["city_id"])
            _progress(root, permit, city_id, "sentinel_compile")
            authenticate_authorization(root)
            sentinel.compile_city(root, contexts[city_id])
            _progress(root, permit, city_id, "static_base")
            authenticate_authorization(root)
            components.build_static_base_component(root, city_id)
            _progress(root, permit, city_id, "gshhg_distance_first_chunk")
            authenticate_authorization(root)
            distance = components.build_gshhg_distance_component(
                root,
                city_id,
                chunk_size=int(config["runtime"]["distance_chunk_size"]),
                pause_callback=lambda: True,
            )
            marker = {
                "schema_version": 1,
                "algorithm_version": ALGORITHM_VERSION,
                "state": "m3_blind_sentinel_static_offline_assembly_canary_complete",
                "authorization_commit_sha256": permit["commit_sha256"],
                "city_id": city_id,
                "sentinel_city_compile": True,
                "static_base": True,
                "gshhg_completed_chunk_count": len(distance.get("completed_chunks", [])),
                "network_request_count": 0,
                "href_read_count": 0,
                "blind_targets_sealed": True,
            }
            marker["commit_sha256"] = canonical_sha256(marker)
            atomic_json(marker, root / CANARY_PATH)
            return _status(root, permit, state="canary_complete", city_id=city_id)

        completed = 0
        results: dict[str, Any] = {}
        for city_id in CITY_IDS:
            for task, action in (
                (
                    "sentinel_compile",
                    lambda city_id=city_id: sentinel.compile_city(root, contexts[city_id]),
                ),
                (
                    "static_base",
                    lambda city_id=city_id: components.build_static_base_component(
                        root, city_id
                    ),
                ),
                (
                    "gshhg_distance",
                    lambda city_id=city_id: components.build_gshhg_distance_component(
                        root,
                        city_id,
                        chunk_size=int(config["runtime"]["distance_chunk_size"]),
                        pause_callback=lambda: (root / PAUSE_PATH).exists(),
                    ),
                ),
                (
                    "static_finalize",
                    lambda city_id=city_id: components.finalize_static_component(
                        root, city_id
                    ),
                ),
            ):
                if (root / PAUSE_PATH).exists():
                    return _status(root, permit, state="paused", city_id=city_id, task=task)
                _progress(root, permit, city_id, task)
                authenticate_authorization(root)
                result = action()
                if task == "gshhg_distance" and result.get("state") != "complete":
                    return _status(root, permit, state="paused", city_id=city_id, task=task)
                results[f"{city_id}/{task}"] = result
                completed += 1
        completion = {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_predictor_sentinel_static_assembly_complete",
            "authorization_commit_sha256": permit["commit_sha256"],
            "cities": list(CITY_IDS),
            "city_count": 4,
            "static_feature_count": 18,
            "sentinel_feature_count": 5,
            "tract_date_key_count": 23667,
            "network_request_count": 0,
            "href_read_count": 0,
            "blind_targets_sealed": True,
            "daymet_landsat_qa_or_target_access": False,
            "model_fit_predict_score_or_evaluate": False,
            "result_commits": {
                key: value.get("commit_sha256") for key, value in results.items()
            },
        }
        completion["commit_sha256"] = canonical_sha256(completion)
        atomic_json(completion, root / COMPLETION_PATH)
        return _status(
            root,
            permit,
            state="complete",
            completed_city_tasks=completed,
        )


def estimate_distance_chunks(project_root: str | Path) -> dict[str, int]:
    """Return exact chunk counts; callable only after authorization."""

    root = Path(project_root).resolve()
    authenticate_authorization(root)
    config = _load_config(root)
    _configure(root)
    size = int(config["runtime"]["distance_chunk_size"])
    return {
        city_id: math.ceil(int(acquired._city_support(root, city_id).eligible_land.sum()) / size)
        for city_id in CITY_IDS
    }
