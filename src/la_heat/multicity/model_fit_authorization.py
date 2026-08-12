"""Append-only gate for the frozen LA fit and target-blind external prediction."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Final

from la_heat.multicity.evaluation_protocol_lock import (
    LOCK_PATH as PROTOCOL_LOCK_PATH,
)
from la_heat.multicity.evaluation_protocol_lock import (
    authenticate_protocol_model_lock,
)
from la_heat.multicity.source_target_authorization import (
    authenticate_source_target_authorization,
)
from la_heat.multicity.source_target_worker import SOURCE_COMPLETION
from la_heat.provenance import canonical_sha256, sha256_file

AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/model/MODEL_FIT_AUTHORIZATION.json"
)
PREDICTOR_COMPLETION_PATH: Final = Path(
    "data/processed/multicity/portable_predictors/components/"
    "PREDICTORS_ALL_46_COMPLETE.json"
)
PREDICTOR_TABLE_PATH: Final = Path(
    "data/processed/multicity/portable_predictors/components/predictors_all_46.parquet"
)
CODE_PATHS: Final = (
    "src/la_heat/multicity/model_fit_authorization.py",
    "src/la_heat/multicity/model_fit_prediction.py",
    "src/la_heat/multicity/source_target_worker.py",
    "src/la_heat/multicity/transfer_model.py",
    "src/la_heat/model_rows.py",
    "src/la_heat/provenance.py",
    "scripts/authorize_multicity_model_fit.py",
    "scripts/run_multicity_model_fit_prediction.py",
)


class ModelFitAuthorizationError(RuntimeError):
    """Raised when real fitting cannot be narrowly authorized or authenticated."""


def _inside(root: Path, value: str | Path, *, label: str) -> Path:
    raw = Path(value)
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not path.is_relative_to(root):
        raise ModelFitAuthorizationError(f"{label} must stay inside the project.")
    return path


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _read_committed(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ModelFitAuthorizationError(f"{label} is unavailable: {path}") from error
    if not isinstance(payload, dict):
        raise ModelFitAuthorizationError(f"{label} must be a JSON object.")
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(unsigned) != recorded:
        raise ModelFitAuthorizationError(f"{label} commit is invalid.")
    return payload


def _record(root: Path, path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": _relative(root, path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "commit_sha256": payload["commit_sha256"],
    }


def _file_record(root: Path, path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ModelFitAuthorizationError(f"Required input is unavailable: {path}")
    return {
        "path": _relative(root, path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _matches_file_record(path: Path, record: object) -> bool:
    return (
        isinstance(record, dict)
        and path.is_file()
        and path.stat().st_size == record.get("bytes")
        and sha256_file(path) == record.get("sha256")
    )


def _authenticate_source_completion(root: Path, payload: dict[str, Any]) -> None:
    source = payload.get("source_work_units", {})
    if (
        payload.get("state") != "la_source_targets_complete"
        or payload.get("lane") != "los_angeles_2020_2024_source"
        or payload.get("city_ids") != ["los_angeles_ca"]
        or not isinstance(source, dict)
        or source.get("overpass") != 90
        or source.get("compile") != 1
        or source.get("total") != 91
    ):
        raise ModelFitAuthorizationError("LA source completion is not exactly 91/91.")
    if not isinstance(source.get("result_commits_sha256"), str):
        raise ModelFitAuthorizationError("LA source work-unit commits are missing.")
    external = payload.get("external_cohort", {})
    if external != {
        "task_count": 68,
        "tasks_claimed": False,
        "target_values_read": False,
    }:
        raise ModelFitAuthorizationError("External target tasks were not left sealed.")
    city = payload.get("city_target_commit", {})
    outputs = city.get("output_files", {}) if isinstance(city, dict) else {}
    target = outputs.get("targets.parquet", {}) if isinstance(outputs, dict) else {}
    if (
        not isinstance(city.get("commit_sha256"), str)
        or not isinstance(target, dict)
        or target.get("rows") != 98_640
        or not isinstance(target.get("sha256"), str)
    ):
        raise ModelFitAuthorizationError("LA compiled target identity is incomplete.")
    city_path = _inside(root, str(city.get("path", "")), label="LA city commit path")
    if not _matches_file_record(city_path, city):
        raise ModelFitAuthorizationError("LA city target commit file changed.")
    city_commit = _read_committed(city_path, label="LA city target commit")
    if (
        city_commit.get("commit_sha256") != city.get("commit_sha256")
        or city_commit.get("output_files") != outputs
    ):
        raise ModelFitAuthorizationError("LA city target commit identity changed.")
    for name, record in outputs.items():
        output = city_path.parent / str(name)
        if not _matches_file_record(output, record):
            raise ModelFitAuthorizationError(f"LA target output changed: {name}")
    marker = payload.get("values_opened_marker", {})
    marker_path = _inside(root, str(marker.get("path", "")), label="Source marker path")
    if not _matches_file_record(marker_path, marker):
        raise ModelFitAuthorizationError("Source VALUES_OPENED marker changed.")
    marker_payload = _read_committed(marker_path, label="Source VALUES_OPENED marker")
    if marker_payload.get("commit_sha256") != marker.get("commit_sha256"):
        raise ModelFitAuthorizationError("Source VALUES_OPENED identity changed.")
    source_authorization = payload.get("authorization", {})
    authorization_path = _inside(
        root,
        str(source_authorization.get("path", "")),
        label="Source authorization path",
    )
    if not _matches_file_record(authorization_path, source_authorization):
        raise ModelFitAuthorizationError("Source-target authorization changed.")
    authorization_payload = _read_committed(
        authorization_path, label="Source-target authorization"
    )
    if (
        authorization_payload.get("commit_sha256")
        != source_authorization.get("commit_sha256")
    ):
        raise ModelFitAuthorizationError("Source-target authorization identity changed.")


def build_model_fit_authorization(
    project_root: str | Path,
    *,
    protocol_lock_path: str | Path = PROTOCOL_LOCK_PATH,
    source_completion_path: str | Path = SOURCE_COMPLETION,
    predictor_completion_path: str | Path = PREDICTOR_COMPLETION_PATH,
    predictor_table_path: str | Path = PREDICTOR_TABLE_PATH,
) -> dict[str, Any]:
    """Build a fit permit only after authenticated LA 91/91 completion."""

    root = Path(project_root).resolve()
    protocol_path = _inside(root, protocol_lock_path, label="Protocol lock path")
    source_path = _inside(root, source_completion_path, label="Source completion path")
    predictor_complete_path = _inside(
        root, predictor_completion_path, label="Predictor completion path"
    )
    predictor_path = _inside(root, predictor_table_path, label="Predictor table path")

    protocol = authenticate_protocol_model_lock(root, protocol_path)
    source = _read_committed(source_path, label="LA source completion")
    _authenticate_source_completion(root, source)
    source_authorization = authenticate_source_target_authorization(
        root, source["authorization"]["path"]
    )
    protocol_plan_commit = (
        protocol.get("input_fingerprints", {})
        .get("target_plan", {})
        .get("commit_sha256")
    )
    if (
        source["authorization"].get("commit_sha256")
        != source_authorization.get("commit_sha256")
        or source.get("plan_commit_sha256") != protocol_plan_commit
        or source_authorization.get("plan_commit_sha256") != protocol_plan_commit
    ):
        raise ModelFitAuthorizationError(
            "LA source completion does not bind the locked target plan."
        )
    predictor_complete = _read_committed(
        predictor_complete_path, label="Predictor completion"
    )
    predictor_record = _file_record(root, predictor_path)
    predictor_output = predictor_complete.get("output", {})
    if (
        predictor_complete.get("state") != "complete_target_blind_46_feature_predictors"
        or predictor_complete.get("row_count") != 136_941
        or predictor_complete.get("feature_count") != 46
        or predictor_output.get("path") != predictor_record["path"]
        or predictor_output.get("bytes") != predictor_record["bytes"]
        or predictor_output.get("sha256") != predictor_record["sha256"]
    ):
        raise ModelFitAuthorizationError("Predictor completion identity changed.")
    cohorts = protocol.get("cohorts", {})
    if (
        cohorts.get("training_city_id") != "los_angeles_ca"
        or cohorts.get("training_years") != [2020, 2021, 2022, 2023]
        or cohorts.get("training_rows") != 73_432
        or cohorts.get("calibration_city_id") != "los_angeles_ca"
        or cohorts.get("calibration_year") != 2024
        or cohorts.get("calibration_rows") != 25_208
        or cohorts.get("external_city_ids")
        != ["phoenix_az", "houston_tx", "chicago_il"]
        or cohorts.get("external_year") != 2025
        or cohorts.get("external_rows") != 38_301
        or cohorts.get("external_city_dates") != 64
    ):
        raise ModelFitAuthorizationError("Protocol cohort sizes changed.")
    if any(
        protocol.get("permissions", {}).get(key) is not False
        for key in (
            "external_target_build_authorized",
            "model_fit_authorized",
            "model_score_authorized",
            "external_targets_unlocked",
            "external_target_values_read",
            "external_prediction_commit_exists",
        )
    ):
        raise ModelFitAuthorizationError("Protocol lock opened a prohibited boundary.")

    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": "multicity-model-fit-authorization-v1",
        "state": "model_fit_authorized",
        "experiment_id": "la_to_three_city_zero_shot_v1",
        "protocol_lock": _record(root, protocol_path, protocol),
        "source_targets_complete": _record(root, source_path, source),
        "predictor_complete": _record(
            root, predictor_complete_path, predictor_complete
        ),
        "predictor_table": predictor_record,
        "protocol_model_lock_commit_sha256": protocol["commit_sha256"],
        "source_completion_commit_sha256": source["commit_sha256"],
        "predictor_complete_commit_sha256": predictor_complete["commit_sha256"],
        "predictor_sha256": predictor_record["sha256"],
        "source_target_identity": {
            "city_target_commit_sha256": source["city_target_commit"]["commit_sha256"],
            "target_table_sha256": source["city_target_commit"]["output_files"][
                "targets.parquet"
            ]["sha256"],
            "source_work_units": 91,
        },
        "cohorts": {
            "training_city_id": "los_angeles_ca",
            "training_years": [2020, 2021, 2022, 2023],
            "training_rows": 73_432,
            "calibration_city_id": "los_angeles_ca",
            "calibration_year": 2024,
            "calibration_rows": 25_208,
            "external_city_ids": ["phoenix_az", "houston_tx", "chicago_il"],
            "external_year": 2025,
            "external_rows": 38_301,
            "external_city_dates": 64,
        },
        "permissions": {
            "read_la_source_targets": True,
            "fit_frozen_models": True,
            "calibrate_la_2024_cqr": True,
            "create_external_predictor_only_predictions": True,
            "read_external_targets": False,
            "score_external_targets": False,
            "retune_or_select_models": False,
            "external_target_claim_authorized": False,
        },
        "output_contract": {
            "model_root": "data/processed/multicity/models/frozen_transfer",
            "model_artifact_path": (
                "data/processed/multicity/models/frozen_transfer/"
                "fitted_transfer_models.joblib"
            ),
            "fit_audit_path": (
                "data/processed/multicity/models/frozen_transfer/fit_audit.json"
            ),
            "external_predictions_path": (
                "data/processed/multicity/models/frozen_transfer/"
                "external_predictions_2025.parquet"
            ),
            "external_prediction_commit_manifest": (
                "manifests/multicity/evaluation/"
                "EXTERNAL_PREDICTIONS_COMMITTED.json"
            ),
            "completion_manifest": (
                "manifests/multicity/model/MODEL_FIT_COMPLETE.json"
            ),
        },
        "code_identity": {
            "files": {
                path: _file_record(root, root / path) for path in CODE_PATHS
            }
        },
        "access_audit": {
            "source_target_values_read_by_authorization": False,
            "external_target_values_read_by_authorization": False,
            "model_fit_performed_by_authorization": False,
            "external_prediction_performed_by_authorization": False,
        },
        "next_safe_stage": (
            "run_authorized_frozen_model_fit_and_external_predictor_only_prediction"
        ),
    }
    payload["claim_id"] = canonical_sha256(payload)
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def _write_exclusive(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as error:
        raise ModelFitAuthorizationError(
            f"Append-only authorization already exists: {path}"
        ) from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def authenticate_model_fit_authorization(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    """Reproduce the permit and all bound identities without reading data tables."""

    root = Path(project_root).resolve()
    path = _inside(root, authorization_path, label="Authorization path")
    observed = _read_committed(path, label="Model-fit authorization")
    expected = build_model_fit_authorization(
        root,
        protocol_lock_path=observed.get("protocol_lock", {}).get("path", ""),
        source_completion_path=observed.get("source_targets_complete", {}).get(
            "path", ""
        ),
        predictor_completion_path=observed.get("predictor_complete", {}).get(
            "path", ""
        ),
        predictor_table_path=observed.get("predictor_table", {}).get("path", ""),
    )
    if observed != expected:
        raise ModelFitAuthorizationError("Model-fit authorization no longer reproduces.")
    return observed


def create_model_fit_authorization(
    project_root: str | Path,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    """Issue the append-only permit; never fit or read target tables here."""

    root = Path(project_root).resolve()
    path = _inside(root, authorization_path, label="Authorization path")
    payload = build_model_fit_authorization(root)
    _write_exclusive(payload, path)
    return authenticate_model_fit_authorization(root, path)
