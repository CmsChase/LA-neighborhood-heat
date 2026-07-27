"""Fail-closed, one-time execution protocol for the locked 2025 evaluation.

The protocol has two deliberately separate phases:

* :func:`prepare_final_evaluation_readiness` authenticates models, blind
  predictors, Landsat inventory metadata, code, configuration, and output
  contracts while the 2025 target remains locked.
* :func:`execute_locked_final_evaluation` consumes an explicit authorization,
  freezes predictions for every inventory key, and only then permits the
  dedicated target transaction to open thermal or QA assets.

No function in this module fits, tunes, selects, or mutates a model.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import re
import subprocess
import tempfile
import tomllib
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from la_heat.config import load_config
from la_heat.final_model import (
    FinalModelError,
    load_hashed_model_bundle,
    predict_model_bundle,
)
from la_heat.final_test_authorization import (
    AUTHORIZATION_ALGORITHM_VERSION,
    AUTHORIZATION_SCHEMA_VERSION,
    DEFAULT_AUTHORIZATION_PATH,
    DEFAULT_EVALUATION_READINESS_PATH,
    DEFAULT_MODEL_LOCK_PATH,
    EVALUATION_READINESS_ALGORITHM_VERSION,
    EVALUATION_READINESS_SCHEMA_VERSION,
    FinalTestAuthorizationError,
    _authenticate_formal_model_lock,
    _committed_file_record,
    _git,
    _git_state,
    _verify_commit,
)
from la_heat.final_test_predictor_assembler import (
    FinalTestPredictorAssemblyError,
)
from la_heat.final_test_predictor_assembler import (
    _authenticate_existing as _authenticate_existing_predictors,
)
from la_heat.final_test_predictor_assembler import (
    _read_json_stable as _read_predictor_json_stable,
)
from la_heat.final_test_predictor_assembler import (
    _read_parquet_stable as _read_predictor_parquet_stable,
)
from la_heat.final_test_state_lock import (
    DEFAULT_FINAL_TEST_STATE_LOCK_PATH,
    FinalTestStateLock,
)
from la_heat.provenance import (
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    code_runtime_fingerprint,
    parquet_file_record,
    sha256_file,
)
from la_heat.stage_config import target_config_sha256

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "one-time-final-evaluation-v1"
DEFAULT_CONFIG_PATH: Final = Path("configs/final_evaluation_2025.toml")
DEFAULT_EVALUATOR_MODULE: Final = Path(
    "src/la_heat/final_evaluation_protocol.py"
)
CLAIM_SCHEMA_VERSION: Final = 1
CLAIM_ALGORITHM_VERSION: Final = "final-evaluation-consumption-claim-v1"
PREDICTIONS_ALGORITHM_VERSION: Final = "blind-final-predictions-v1"
VALUES_OPENED_ALGORITHM_VERSION: Final = "final-target-values-opened-v1"
COMPLETION_ALGORITHM_VERSION: Final = "atomic-final-evaluation-publication-v1"

KEY_COLUMNS: Final = ("tract_geoid", "target_date")
PREDICTION_COLUMNS: Final = (
    "tract_geoid",
    "target_date",
    "y_pred_b1",
    "y_pred_m2",
)
SENTINEL_FEATURES: Final = (
    "sentinel_ndvi_lag60",
    "sentinel_evi_lag60",
    "sentinel_ndwi_lag60",
    "sentinel_ndbi_lag60",
    "sentinel_albedo_proxy_lag60",
)
EXPECTED_OUTPUT_FILES: Final = (
    "blind_predictions.parquet",
    "final_target_qa.parquet",
    "date_summary.parquet",
    "scene_contributions.parquet",
    "evaluation_rows.parquet",
    "model_metrics.csv",
    "per_date_metrics.csv",
    "paired_date_block_errors.csv",
    "crossed_bootstrap.json",
    "protocol_gates.csv",
    "hotspot_per_date.csv",
    "hotspot_summary.csv",
    "sensor_per_date_metrics.csv",
    "sensor_summary.csv",
    "sentinel_stratum_summary.csv",
    "qa_missingness_summary.csv",
    "tract_choropleth_summary.csv",
    "observed_predicted_residual_maps.pdf",
    "per_date_error_and_rank.png",
    "hotspot_precision_recall.png",
    "EVALUATION_COMMIT.json",
)
OUTPUT_SEMANTIC_SORT_BY: Final = {
    "blind_predictions.parquet": ("target_date", "tract_geoid"),
    "final_target_qa.parquet": ("target_date", "tract_geoid"),
    "date_summary.parquet": ("target_date",),
    "scene_contributions.parquet": (
        "target_date",
        "overpass_id",
        "tract_geoid",
        "scene_id",
    ),
    "evaluation_rows.parquet": ("target_date", "tract_geoid"),
    "model_metrics.csv": ("model_id",),
    "per_date_metrics.csv": ("target_date", "model_id"),
    "paired_date_block_errors.csv": ("target_date", "spatial_block"),
    "protocol_gates.csv": ("gate_id",),
    "hotspot_per_date.csv": ("target_date", "model_id"),
    "hotspot_summary.csv": ("model_id",),
    "sensor_per_date_metrics.csv": ("sensor", "target_date", "model_id"),
    "sensor_summary.csv": ("sensor", "model_id"),
    "sentinel_stratum_summary.csv": ("sentinel_stratum", "model_id"),
    "qa_missingness_summary.csv": ("summary_level", "target_date"),
    "tract_choropleth_summary.csv": ("tract_geoid",),
}
OUTPUT_PRIMARY_KEYS: Final = {
    "blind_predictions.parquet": ("tract_geoid", "target_date"),
    "final_target_qa.parquet": ("tract_geoid", "target_date"),
    "date_summary.parquet": ("target_date",),
    "scene_contributions.parquet": (
        "target_date",
        "overpass_id",
        "scene_id",
        "tract_geoid",
    ),
    "evaluation_rows.parquet": ("tract_geoid", "target_date"),
    "model_metrics.csv": ("model_id",),
    "per_date_metrics.csv": ("target_date", "model_id"),
    "paired_date_block_errors.csv": ("target_date", "spatial_block"),
    "protocol_gates.csv": ("gate_id",),
    "hotspot_per_date.csv": ("target_date", "model_id"),
    "hotspot_summary.csv": ("model_id",),
    "sensor_per_date_metrics.csv": ("sensor", "target_date", "model_id"),
    "sensor_summary.csv": ("sensor", "model_id"),
    "sentinel_stratum_summary.csv": ("sentinel_stratum", "model_id"),
    "qa_missingness_summary.csv": ("summary_level", "target_date"),
    "tract_choropleth_summary.csv": ("tract_geoid",),
}
FIGURE_OUTPUT_CONTRACTS: Final = {
    "observed_predicted_residual_maps.pdf": {
        "format": "pdf",
        "panel_count": 6,
        "source_table": "tract_choropleth_summary.csv",
    },
    "per_date_error_and_rank.png": {
        "format": "png",
        "panel_count": 2,
        "source_table": "per_date_metrics.csv",
    },
    "hotspot_precision_recall.png": {
        "format": "png",
        "panel_count": 2,
        "source_table": "hotspot_per_date.csv",
    },
}
BOOTSTRAP_OUTPUT_KEYS: Final = frozenset(
    {
        "schema_version",
        "algorithm_version",
        "state",
        "final_test_year",
        "evaluation_cohort",
        "baseline_model_id",
        "primary_model_id",
        "bootstrap_method",
        "bootstrap_sampling_unit",
        "bootstrap_estimand",
        "date_block_cell_aggregation",
        "complete_date_resampling",
        "complete_spatial_block_resampling",
        "date_and_block_draws_independent",
        "bootstrap_seed",
        "bootstrap_replicates",
        "confidence_level",
        "percentile_interval_method",
        "paired_models_share_every_cluster_draw",
        "random_row_sampling_used",
        "date_block_cell_count",
        "independent_date_count",
        "independent_spatial_block_count",
        "tract_date_row_count",
        "zero_observation_sampled_date_draw_count",
        "baseline_point_mae_c",
        "target_model_point_mae_c",
        "absolute_mae_improvement_c",
        "absolute_mae_improvement_ci_lower_c",
        "absolute_mae_improvement_ci_upper_c",
        "relative_mae_improvement_fraction",
        "relative_mae_improvement_percent",
        "relative_mae_improvement_ci_lower_fraction",
        "relative_mae_improvement_ci_upper_fraction",
        "relative_mae_improvement_ci_lower_percent",
        "relative_mae_improvement_ci_upper_percent",
        "probability_improvement_gt_zero",
        "probability_relative_improvement_gt_10_percent",
        "point_estimate_reconciliation",
    }
)
BOOTSTRAP_RECONCILIATION_KEYS: Final = frozenset(
    {
        "primary_metric",
        "baseline_model_id",
        "baseline_model_metric_mae_c",
        "baseline_bootstrap_point_mae_c",
        "primary_model_id",
        "primary_model_metric_mae_c",
        "primary_bootstrap_point_mae_c",
        "relative_tolerance",
        "absolute_tolerance",
        "point_estimates_reconciled",
    }
)
PIPELINE_FILES: Final = (
    "pyproject.toml",
    "src/la_heat/aligned_landsat.py",
    "src/la_heat/config.py",
    "src/la_heat/final_evaluation_protocol.py",
    "src/la_heat/final_evaluation_reporting.py",
    "src/la_heat/final_evaluation_targets.py",
    "src/la_heat/final_model.py",
    "src/la_heat/final_test_authorization.py",
    "src/la_heat/final_test_inventory.py",
    "src/la_heat/final_test_predictor_assembler.py",
    "src/la_heat/final_test_state_lock.py",
    "src/la_heat/formal_model_lock.py",
    "src/la_heat/grid.py",
    "src/la_heat/guardrails.py",
    "src/la_heat/inventory.py",
    "src/la_heat/landmask.py",
    "src/la_heat/landsat.py",
    "src/la_heat/metrics.py",
    "src/la_heat/model_endpoint_diagnostics.py",
    "src/la_heat/model_result_analysis.py",
    "src/la_heat/mosaic.py",
    "src/la_heat/provenance.py",
    "src/la_heat/stage_config.py",
    "src/la_heat/target_aggregation.py",
    "src/la_heat/target_builder.py",
    "src/la_heat/targets.py",
    "scripts/prepare_final_evaluation.py",
    "scripts/execute_locked_final_evaluation.py",
)
ALLOWED_UNLOCK_TRANSITION_FILES: Final = frozenset(
    {
        "configs/research.toml",
        "docs/DECISION_LOG.md",
        "docs/PROJECT_HANDOFF.md",
    }
)
_SHA256: Final = re.compile(r"[0-9a-f]{64}")
_EXTRA_RUNTIME_PACKAGES: Final = (
    "joblib",
    "matplotlib",
    "Pillow",
    "scikit-learn",
    "scipy",
)


class FinalEvaluationProtocolError(RuntimeError):
    """Raised when the one-time evaluation contract cannot be authenticated."""


@dataclass(frozen=True)
class FinalEvaluationConfig:
    """Strict typed view of the frozen final-evaluation TOML."""

    root: Path
    path: Path
    raw: dict[str, Any]
    paths: Mapping[str, Path]
    locks: Mapping[str, str]
    analysis: Mapping[str, Any]
    bootstrap: Mapping[str, Any]
    success_gates: Mapping[str, Any]
    hotspot: Mapping[str, Any]
    publication: Mapping[str, Any]
    semantic_sha256: str


@dataclass(frozen=True)
class BlindPredictionArtifacts:
    """Authenticated predictions frozen before any target/QA value is opened."""

    frame: pd.DataFrame
    marker: dict[str, Any]
    staging_directory: Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _exact_keys(
    payload: Mapping[str, Any],
    expected: set[str],
    *,
    label: str,
) -> None:
    observed = set(payload)
    if observed != expected:
        raise FinalEvaluationProtocolError(
            f"{label} keys drifted; missing={sorted(expected - observed)}, "
            f"extra={sorted(observed - expected)}."
        )


def _inside(root: Path, value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise FinalEvaluationProtocolError(f"{label} must be a non-empty path.")
    candidate = Path(value)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise FinalEvaluationProtocolError(
            f"{label} must remain inside the project root."
        ) from error
    return resolved


def _positive_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise FinalEvaluationProtocolError(f"{label} must be a positive integer.")
    return value


def _finite_float(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FinalEvaluationProtocolError(f"{label} must be numeric.")
    result = float(value)
    if not np.isfinite(result):
        raise FinalEvaluationProtocolError(f"{label} must be finite.")
    return result


def load_final_evaluation_config(
    path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    project_root: str | Path | None = None,
) -> FinalEvaluationConfig:
    """Load and validate the exact predeclared 2025 evaluation contract."""

    root = (
        _project_root().resolve()
        if project_root is None
        else Path(project_root).resolve()
    )
    source = _inside(root, str(path), label="Final-evaluation configuration")
    expected_source = (root / DEFAULT_CONFIG_PATH).resolve()
    if source != expected_source or not source.is_file():
        raise FinalEvaluationProtocolError(
            f"Configuration must be exactly {DEFAULT_CONFIG_PATH.as_posix()}."
        )
    try:
        with source.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise FinalEvaluationProtocolError(
            "Cannot read the final-evaluation configuration."
        ) from error
    _exact_keys(
        raw,
        {
            "schema_version",
            "algorithm_version",
            "state",
            "paths",
            "locks",
            "analysis",
            "bootstrap",
            "success_gates",
            "hotspot",
            "publication",
        },
        label="Final-evaluation configuration",
    )
    if (
        raw["schema_version"] != SCHEMA_VERSION
        or raw["algorithm_version"] != ALGORITHM_VERSION
        or raw["state"] != "frozen_before_2025_target_values"
    ):
        raise FinalEvaluationProtocolError(
            "Final-evaluation configuration identity is not frozen."
        )

    path_keys = {
        "formal_model_lock",
        "predictor_table",
        "predictor_provenance",
        "landsat_inventory",
        "research_config",
        "readiness",
        "authorization",
        "claim",
        "values_opened",
        "predictions_frozen",
        "complete",
        "staging_root",
        "target_cache_directory",
        "final_output_directory",
    }
    paths_raw = raw["paths"]
    if not isinstance(paths_raw, dict):
        raise FinalEvaluationProtocolError("paths must be a TOML table.")
    _exact_keys(paths_raw, path_keys, label="paths")
    paths = {
        name: _inside(root, value, label=f"paths.{name}")
        for name, value in paths_raw.items()
    }
    canonical_paths = {
        "formal_model_lock": DEFAULT_MODEL_LOCK_PATH,
        "readiness": DEFAULT_EVALUATION_READINESS_PATH,
        "authorization": DEFAULT_AUTHORIZATION_PATH,
    }
    for name, relative in canonical_paths.items():
        if paths[name] != (root / relative).resolve():
            raise FinalEvaluationProtocolError(
                f"paths.{name} must be exactly {relative.as_posix()}."
            )
    if paths["staging_root"].parent != paths["final_output_directory"].parent:
        raise FinalEvaluationProtocolError(
            "Staging and final output directories must share one parent."
        )
    if paths["staging_root"] == paths["final_output_directory"]:
        raise FinalEvaluationProtocolError(
            "Staging and final output directories must be distinct."
        )

    locks = raw["locks"]
    if not isinstance(locks, dict) or not locks:
        raise FinalEvaluationProtocolError("locks must be a non-empty TOML table.")
    expected_lock_keys = {
        "formal_model_lock_file_sha256",
        "formal_model_lock_commit_sha256",
        "predictor_file_sha256",
        "predictor_schema_sha256",
        "predictor_semantic_sha256",
        "predictor_key_semantic_sha256",
        "predictor_provenance_file_sha256",
        "predictor_provenance_commit_sha256",
        "landsat_inventory_file_sha256",
        "landsat_inventory_commit_sha256",
        "landsat_key_semantic_sha256",
        "locked_research_config_file_sha256",
        "target_config_semantic_sha256",
    }
    _exact_keys(locks, expected_lock_keys, label="locks")
    if any(
        not isinstance(value, str) or _SHA256.fullmatch(value) is None
        for value in locks.values()
    ):
        raise FinalEvaluationProtocolError("Every lock must be a lowercase SHA-256.")

    analysis = raw["analysis"]
    bootstrap = raw["bootstrap"]
    success_gates = raw["success_gates"]
    hotspot = raw["hotspot"]
    publication = raw["publication"]
    if not all(
        isinstance(section, dict)
        for section in (
            analysis,
            bootstrap,
            success_gates,
            hotspot,
            publication,
        )
    ):
        raise FinalEvaluationProtocolError(
            "Analysis, bootstrap, gate, hotspot, and publication sections "
            "must be TOML tables."
        )
    if (
        analysis.get("final_test_year") != 2025
        or analysis.get("expected_key_count") != 25_208
        or analysis.get("expected_tract_count") != 1_096
        or analysis.get("expected_inventory_overpass_count") != 23
        or analysis.get("expected_inventory_scene_count") != 45
        or analysis.get("expected_model_feature_count") != 46
        or analysis.get("model_ids") != ["B1", "M2"]
        or analysis.get("baseline_model_id") != "B1"
        or analysis.get("primary_model_id") != "M2"
        or analysis.get("primary_metric") != "equal_date_weighted_mae_c"
        or analysis.get("evaluation_cohort")
        != "all_date_usable_and_target_available_rows"
        or analysis.get("minimum_usable_date_count_for_metrics") != 1
    ):
        raise FinalEvaluationProtocolError("Frozen final analysis contract drifted.")
    if (
        bootstrap.get("method") != "crossed_date_spatial_block"
        or bootstrap.get("sampling_unit") != "complete_clusters_only"
        or bootstrap.get("seed") != 20_260_722
        or bootstrap.get("replicates") != 5_000
        or _finite_float(
            bootstrap.get("confidence_level"),
            label="bootstrap.confidence_level",
        )
        != 0.95
    ):
        raise FinalEvaluationProtocolError("Frozen bootstrap contract drifted.")
    if (
        _finite_float(
            success_gates.get("minimum_relative_mae_improvement_fraction"),
            label="minimum relative improvement",
        )
        != 0.10
        or _finite_float(
            success_gates.get("minimum_median_per_date_spearman"),
            label="minimum median Spearman",
        )
        != 0.50
        or _finite_float(
            success_gates.get("uncertainty_relative_ci_lower_must_exceed"),
            label="uncertainty lower bound",
        )
        != 0.0
    ):
        raise FinalEvaluationProtocolError("Frozen success gates drifted.")
    outputs = publication.get("exact_output_files")
    if (
        outputs != list(EXPECTED_OUTPUT_FILES)
        or len(outputs) != len(set(outputs))
        or publication.get("protocol")
        != "append_only_claim_then_atomic_directory_promotion"
        or publication.get("allow_same_claim_resume") is not True
        or publication.get("allow_second_claim") is not False
        or publication.get("print_target_values_or_metrics_before_complete")
        is not False
    ):
        raise FinalEvaluationProtocolError("Frozen publication contract drifted.")
    if (
        hotspot.get("positive_fraction") != 0.20
        or hotspot.get("rank_order") != "score_desc_geoid_asc"
        or hotspot.get("exact_top_k") is not True
        or hotspot.get("average_precision_input") != "continuous_y_pred"
    ):
        raise FinalEvaluationProtocolError("Frozen hotspot contract drifted.")

    return FinalEvaluationConfig(
        root=root,
        path=source,
        raw=raw,
        paths=paths,
        locks=locks,
        analysis=analysis,
        bootstrap=bootstrap,
        success_gates=success_gates,
        hotspot=hotspot,
        publication=publication,
        semantic_sha256=canonical_sha256(raw),
    )


def _exclusive_json(
    payload: Mapping[str, Any],
    destination: Path,
    *,
    label: str,
) -> None:
    """Atomically create one immutable JSON marker without a replace path."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{uuid.uuid4().hex}.partial"
    )
    encoded = json.dumps(
        dict(payload),
        indent=2,
        ensure_ascii=False,
    ).encode("utf-8")
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, destination)
    except FileExistsError as error:
        raise FinalEvaluationProtocolError(
            f"{label} already exists and cannot be overwritten."
        ) from error
    finally:
        temporary.unlink(missing_ok=True)


def _clean_owned_marker_partials(config: FinalEvaluationConfig) -> None:
    """Remove only abandoned temp names owned by this protocol's markers."""

    for name in (
        "readiness",
        "claim",
        "values_opened",
        "predictions_frozen",
        "complete",
    ):
        destination = config.paths[name]
        pattern = f".{destination.name}.*.partial"
        for temporary in destination.parent.glob(pattern):
            if temporary.is_file():
                temporary.unlink()


def _clean_owned_staging_partials(config: FinalEvaluationConfig) -> None:
    staging = config.paths["staging_root"]
    if not staging.is_dir():
        return
    for temporary in staging.iterdir():
        if temporary.is_file() and temporary.name.endswith(".partial"):
            temporary.unlink()


def _committed_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(result)
    return result


def _read_committed(path: Path, *, label: str) -> tuple[dict[str, Any], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FinalEvaluationProtocolError(f"Cannot read valid {label}.") from error
    if not isinstance(payload, dict):
        raise FinalEvaluationProtocolError(f"{label} must be a JSON object.")
    working = dict(payload)
    commit = working.pop("commit_sha256", None)
    if (
        not isinstance(commit, str)
        or _SHA256.fullmatch(commit) is None
        or canonical_sha256(working) != commit
    ):
        raise FinalEvaluationProtocolError(f"{label} canonical commit failed.")
    return payload, commit


def _file_record(path: Path, *, relative_to: Path | None = None) -> dict[str, Any]:
    if not path.is_file():
        raise FinalEvaluationProtocolError(f"Required file is missing: {path}")
    recorded_path = (
        path.relative_to(relative_to).as_posix()
        if relative_to is not None
        else path.as_posix()
    )
    return {
        "path": recorded_path,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def _verify_file_record(
    path: Path,
    record: Mapping[str, Any],
    *,
    label: str,
) -> None:
    expected_hash = record.get("sha256", record.get("file_sha256"))
    expected_bytes = record.get("bytes")
    if (
        not path.is_file()
        or not isinstance(expected_hash, str)
        or _SHA256.fullmatch(expected_hash) is None
        or isinstance(expected_bytes, bool)
        or not isinstance(expected_bytes, int)
        or path.stat().st_size != expected_bytes
        or sha256_file(path) != expected_hash
    ):
        raise FinalEvaluationProtocolError(f"{label} byte/SHA-256 lock failed.")


def _formal_model_record(
    config: FinalEvaluationConfig,
    *,
    head: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    lock_path = config.paths["formal_model_lock"]
    git_record = _committed_file_record(
        config.root,
        lock_path,
        head=head,
        label="Formal model lock",
    )
    try:
        formal, commit = _authenticate_formal_model_lock(
            config.root,
            lock_path,
        )
    except FinalTestAuthorizationError as error:
        raise FinalEvaluationProtocolError(str(error)) from error
    record = {
        **{key: value for key, value in git_record.items() if key != "sha256"},
        "file_sha256": git_record["sha256"],
        "commit_sha256": commit,
    }
    if (
        record["file_sha256"]
        != config.locks["formal_model_lock_file_sha256"]
        or record["commit_sha256"]
        != config.locks["formal_model_lock_commit_sha256"]
    ):
        raise FinalEvaluationProtocolError("Formal model-lock config pin failed.")
    return formal, record


def _predictor_readiness_record(
    config: FinalEvaluationConfig,
) -> tuple[dict[str, Any], pd.DataFrame, dict[str, Any]]:
    try:
        provenance, _ = _read_predictor_json_stable(
            config.paths["predictor_provenance"],
            label="Final predictor provenance",
        )
        request = provenance.get("request")
        if not isinstance(request, Mapping):
            raise FinalTestPredictorAssemblyError(
                "Final predictor provenance has no frozen request."
            )
        authenticated = _authenticate_existing_predictors(
            config.paths["predictor_provenance"],
            expected_request=request,
            root=config.root,
        )
        if authenticated is None:
            raise FinalTestPredictorAssemblyError(
                "Frozen final predictor provenance is missing."
            )
        provenance = authenticated
    except FinalTestPredictorAssemblyError as error:
        raise FinalEvaluationProtocolError(str(error)) from error
    provenance_commit = _verify_commit(
        provenance,
        label="final predictor provenance",
    )
    output_files = provenance.get("output_files")
    output = (
        output_files.get("final_predictors.parquet")
        if isinstance(output_files, Mapping)
        else None
    )
    if not isinstance(output, Mapping):
        raise FinalEvaluationProtocolError("Predictor output lock is missing.")
    predictors = _read_predictor_parquet_stable(
        config.paths["predictor_table"],
        record=output,
        label="Published predictor table",
    )
    expected = {
        "provenance_file_sha256": config.locks[
            "predictor_provenance_file_sha256"
        ],
        "provenance_commit_sha256": config.locks[
            "predictor_provenance_commit_sha256"
        ],
        "file_sha256": config.locks["predictor_file_sha256"],
        "schema_sha256": config.locks["predictor_schema_sha256"],
        "semantic_sha256": config.locks["predictor_semantic_sha256"],
        "key_semantic_sha256": config.locks[
            "predictor_key_semantic_sha256"
        ],
    }
    observed = {
        "provenance_file_sha256": sha256_file(
            config.paths["predictor_provenance"]
        ),
        "provenance_commit_sha256": provenance_commit,
        "file_sha256": output.get("sha256"),
        "schema_sha256": output.get("schema_sha256"),
        "semantic_sha256": provenance.get("semantic_predictor_table_sha256"),
        "key_semantic_sha256": provenance.get("semantic_key_sha256"),
    }
    if observed != expected:
        raise FinalEvaluationProtocolError("Frozen predictor config pins failed.")
    if Path(str(output.get("path", ""))).resolve() != config.paths[
        "predictor_table"
    ]:
        raise FinalEvaluationProtocolError("Predictor output path drifted.")
    record = {
        "provenance_path": config.paths["predictor_provenance"]
        .relative_to(config.root)
        .as_posix(),
        **observed,
        "file_bytes": output.get("bytes"),
        "row_count": provenance.get("row_count"),
        "date_count": provenance.get("date_count"),
        "tract_count": provenance.get("tract_count"),
        "feature_count": provenance.get("feature_count"),
        "feature_names": provenance.get("feature_names"),
        "sentinel_missing_row_count": provenance.get(
            "sentinel_missing_row_count"
        ),
        "target_blind": provenance.get("target_blind"),
        "contains_target_or_qa_values": provenance.get(
            "contains_target_or_qa_values"
        ),
    }
    return provenance, predictors, record


def _code_records(
    config: FinalEvaluationConfig,
    *,
    head: str,
) -> tuple[dict[str, dict[str, Any]], str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for relative in PIPELINE_FILES:
        path = (config.root / relative).resolve()
        records[relative] = _committed_file_record(
            config.root,
            path,
            head=head,
            label=f"Evaluation code {relative}",
        )
    fingerprint_sha, fingerprint = code_runtime_fingerprint(
        project_root=config.root,
        relative_paths=PIPELINE_FILES,
        algorithm_version=ALGORITHM_VERSION,
    )
    return records, fingerprint_sha, fingerprint


def _extended_runtime_record() -> dict[str, Any]:
    packages: dict[str, str] = {}
    for name in _EXTRA_RUNTIME_PACKAGES:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "MISSING"
    return {
        "python": platform.python_version(),
        "packages": packages,
    }


def _default_inventory_authenticator(
    config: FinalEvaluationConfig,
) -> tuple[Any, dict[str, Any]]:
    from la_heat.final_evaluation_targets import (
        authenticate_final_landsat_inventory,
    )

    authenticated = authenticate_final_landsat_inventory(
        inventory_path=config.paths["landsat_inventory"],
        expected_inventory_file_sha256=config.locks[
            "landsat_inventory_file_sha256"
        ],
        expected_inventory_commit_sha256=config.locks[
            "landsat_inventory_commit_sha256"
        ],
        expected_key_semantic_sha256=config.locks[
            "landsat_key_semantic_sha256"
        ],
        expected_scene_count=int(
            config.analysis["expected_inventory_scene_count"]
        ),
        expected_overpass_count=int(
            config.analysis["expected_inventory_overpass_count"]
        ),
        expected_key_count=int(config.analysis["expected_key_count"]),
        expected_tract_count=int(config.analysis["expected_tract_count"]),
        project_root=config.root,
    )
    record = getattr(authenticated, "readiness_record", None)
    if callable(record):
        record = record()
    if not isinstance(record, dict):
        raise FinalEvaluationProtocolError(
            "Authenticated Landsat inventory lacks a readiness record."
        )
    keys = authenticated.key_universe.loc[
        :,
        ["tract_geoid", "target_date"],
    ].copy()
    keys["tract_geoid"] = keys["tract_geoid"].astype(str)
    keys["target_date"] = pd.to_datetime(
        keys["target_date"],
        errors="raise",
    ).astype("datetime64[ns]")
    record["shared_predictor_key_semantic_sha256"] = (
        canonical_frame_sha256(
            keys,
            sort_by=["target_date", "tract_geoid"],
        )
    )
    return authenticated, record


def _validate_predictor_frame(
    predictors: pd.DataFrame,
    *,
    formal: Mapping[str, Any],
    config: FinalEvaluationConfig,
) -> pd.DataFrame:
    models = formal.get("models")
    if not isinstance(models, Mapping) or set(models) != {"B1", "M2"}:
        raise FinalEvaluationProtocolError("Formal B1/M2 model contract is invalid.")
    b1 = models["B1"]
    m2 = models["M2"]
    if not isinstance(b1, Mapping) or not isinstance(m2, Mapping):
        raise FinalEvaluationProtocolError("Formal B1/M2 records are invalid.")
    b1_features = b1.get("feature_names")
    m2_features = m2.get("feature_names")
    if (
        not isinstance(b1_features, list)
        or not isinstance(m2_features, list)
        or len(b1_features) != 23
        or len(m2_features) != 46
        or predictors.columns.tolist() != [*KEY_COLUMNS, *m2_features]
        or not set(b1_features).issubset(m2_features)
        or "tract_geoid" in m2_features
        or "target_date" in m2_features
    ):
        raise FinalEvaluationProtocolError(
            "Predictor schema does not exactly match frozen B1/M2 feature order."
        )
    if (
        len(predictors) != int(config.analysis["expected_key_count"])
        or predictors.duplicated(list(KEY_COLUMNS)).any()
    ):
        raise FinalEvaluationProtocolError(
            "Predictor tract-date key cardinality is not exact."
        )
    result = predictors.copy()
    result["tract_geoid"] = result["tract_geoid"].astype(str)
    result["target_date"] = pd.to_datetime(
        result["target_date"],
        errors="raise",
    ).astype("datetime64[ns]")
    if (
        result["tract_geoid"].str.fullmatch(r"\d{11}").ne(True).any()
        or not result["target_date"].dt.year.eq(2025).all()
        or result["target_date"].nunique()
        != int(config.analysis["expected_inventory_overpass_count"])
        or result["tract_geoid"].nunique()
        != int(config.analysis["expected_tract_count"])
    ):
        raise FinalEvaluationProtocolError("Predictor key semantics drifted.")
    numeric = result.loc[:, m2_features].apply(pd.to_numeric, errors="raise")
    values = numeric.to_numpy(dtype=float)
    if np.isinf(values).any():
        raise FinalEvaluationProtocolError("Predictors contain infinite values.")
    sentinel = numeric.loc[:, list(SENTINEL_FEATURES)].isna()
    if np.logical_xor(
        sentinel.any(axis=1).to_numpy(),
        sentinel.all(axis=1).to_numpy(),
    ).any():
        raise FinalEvaluationProtocolError(
            "Sentinel missingness must be all-five or none."
        )
    non_sentinel = [name for name in m2_features if name not in SENTINEL_FEATURES]
    if numeric.loc[:, non_sentinel].isna().any().any():
        raise FinalEvaluationProtocolError(
            "Non-Sentinel frozen predictors may not be missing."
        )
    result.loc[:, m2_features] = numeric
    return result.sort_values(
        ["target_date", "tract_geoid"],
        kind="stable",
    ).reset_index(drop=True)


def authenticate_final_evaluation_readiness(
    config: FinalEvaluationConfig,
) -> tuple[dict[str, Any], str]:
    """Authenticate the immutable target-blind readiness marker."""

    path = config.paths["readiness"]
    payload, commit = _read_committed(
        path,
        label="final-evaluation readiness",
    )
    request = payload.get("request")
    if (
        payload.get("schema_version")
        != EVALUATION_READINESS_SCHEMA_VERSION
        or payload.get("algorithm_version")
        != EVALUATION_READINESS_ALGORITHM_VERSION
        or payload.get("state") != "ready_target_blind"
        or payload.get("target_blind") is not True
        or payload.get("values_read") is not False
        or payload.get("authorized") is not False
        or not isinstance(request, dict)
        or payload.get("request_sha256") != canonical_sha256(request)
    ):
        raise FinalEvaluationProtocolError(
            "Final-evaluation readiness state is invalid."
        )
    if request.get("configuration_semantic_sha256") != config.semantic_sha256:
        raise FinalEvaluationProtocolError(
            "Readiness belongs to another evaluation configuration."
        )
    return payload, commit


def prepare_final_evaluation_readiness(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    inventory_authenticator: Callable[
        [FinalEvaluationConfig],
        tuple[Any, dict[str, Any]],
    ]
    | None = None,
) -> dict[str, Any]:
    """Freeze the complete evaluation request without opening target/QA values."""

    config = load_final_evaluation_config(config_path)
    inventory_auth = (
        _default_inventory_authenticator
        if inventory_authenticator is None
        else inventory_authenticator
    )
    state_paths = (
        "authorization",
        "claim",
        "values_opened",
        "predictions_frozen",
        "complete",
    )
    with FinalTestStateLock(
        config.root / DEFAULT_FINAL_TEST_STATE_LOCK_PATH
    ):
        _clean_owned_marker_partials(config)
        if config.paths["readiness"].exists():
            payload, _ = authenticate_final_evaluation_readiness(config)
            return payload
        if any(os.path.lexists(config.paths[name]) for name in state_paths):
            raise FinalEvaluationProtocolError(
                "Readiness cannot be prepared after authorization or consumption."
            )
        if (
            os.path.lexists(config.paths["staging_root"])
            or os.path.lexists(config.paths["final_output_directory"])
            or os.path.lexists(config.paths["target_cache_directory"])
        ):
            raise FinalEvaluationProtocolError(
                "Evaluation staging, target cache, and final outputs must be absent."
            )

        git = _git_state(config.root)
        head = str(git["head"])
        config_record = _committed_file_record(
            config.root,
            config.path,
            head=head,
            label="Evaluator configuration",
        )
        evaluator_record = _committed_file_record(
            config.root,
            config.root / DEFAULT_EVALUATOR_MODULE,
            head=head,
            label="Evaluator module",
        )
        formal, formal_record = _formal_model_record(config, head=head)

        research_path = config.paths["research_config"]
        research = load_config(research_path)
        if (
            research.final_test_year != 2025
            or research.final_test_unlocked
            or sha256_file(research_path)
            != config.locks["locked_research_config_file_sha256"]
            or target_config_sha256(research)
            != config.locks["target_config_semantic_sha256"]
        ):
            raise FinalEvaluationProtocolError(
                "Readiness requires the exact still-locked research configuration."
            )
        research_git_record = _committed_file_record(
            config.root,
            research_path,
            head=head,
            label="Locked research configuration",
        )

        provenance, predictor_frame, predictor_record = (
            _predictor_readiness_record(config)
        )
        predictor_frame = _validate_predictor_frame(
            predictor_frame,
            formal=formal,
            config=config,
        )
        _, inventory_record = inventory_auth(config)
        if (
            inventory_record.get("locks", {}).get(
                "key_universe_semantic_sha256"
            )
            != config.locks["landsat_key_semantic_sha256"]
            or inventory_record.get("key_count")
            != config.analysis["expected_key_count"]
            or inventory_record.get("tract_count")
            != config.analysis["expected_tract_count"]
            or inventory_record.get("physical_overpass_count")
            != config.analysis["expected_inventory_overpass_count"]
            or inventory_record.get("scene_count")
            != config.analysis["expected_inventory_scene_count"]
            or inventory_record.get("target_or_qa_values_read") is not False
            or inventory_record.get(
                "shared_predictor_key_semantic_sha256"
            )
            != predictor_record["key_semantic_sha256"]
        ):
            raise FinalEvaluationProtocolError(
                "Authenticated inventory readiness contract drifted."
            )
        if (
            provenance.get("semantic_key_sha256")
            != predictor_record["key_semantic_sha256"]
        ):
            raise FinalEvaluationProtocolError(
                "Predictor key commitment is internally inconsistent."
            )

        code_records, pipeline_sha, pipeline = _code_records(
            config,
            head=head,
        )
        paths_relative = {
            name: path.relative_to(config.root).as_posix()
            for name, path in config.paths.items()
        }
        request: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "configuration_semantic_sha256": config.semantic_sha256,
            "paths": paths_relative,
            "locks": dict(config.locks),
            "analysis": dict(config.analysis),
            "bootstrap": dict(config.bootstrap),
            "success_gates": dict(config.success_gates),
            "hotspot": dict(config.hotspot),
            "publication": dict(config.publication),
            "code_git_commit": head,
            "pipeline_sha256": pipeline_sha,
            "pipeline": pipeline,
            "extended_runtime": _extended_runtime_record(),
            "code_files": code_records,
            "locked_research_config": {
                **research_git_record,
                "target_config_semantic_sha256": target_config_sha256(
                    research
                ),
                "unlock_final_test": False,
            },
            "formal_model_lock": formal_record,
            "models": formal["models"],
            "predictors": predictor_record,
            "landsat_inventory": inventory_record,
            "exact_output_files": list(EXPECTED_OUTPUT_FILES),
        }
        payload = _committed_payload(
            {
                "schema_version": EVALUATION_READINESS_SCHEMA_VERSION,
                "algorithm_version": EVALUATION_READINESS_ALGORITHM_VERSION,
                "state": "ready_target_blind",
                "generated_at_utc": datetime.now(UTC).isoformat(),
                "target_blind": True,
                "values_read": False,
                "authorized": False,
                "code_git_commit": head,
                "evaluator_module": evaluator_record,
                "evaluator_config": config_record,
                "formal_model_lock": formal_record,
                "request": request,
                "request_sha256": canonical_sha256(request),
            }
        )

        if (
            sha256_file(config.paths["predictor_table"])
            != predictor_record["file_sha256"]
            or sha256_file(config.paths["predictor_provenance"])
            != predictor_record["provenance_file_sha256"]
            or sha256_file(config.paths["landsat_inventory"])
            != config.locks["landsat_inventory_file_sha256"]
        ):
            raise FinalEvaluationProtocolError(
                "A frozen readiness input changed before publication."
            )
        _exclusive_json(
            payload,
            config.paths["readiness"],
            label="EVALUATION_READINESS.json",
        )
        return payload


def _allowed_state_paths(config: FinalEvaluationConfig) -> frozenset[str]:
    names = (
        "readiness",
        "authorization",
        "claim",
        "values_opened",
        "predictions_frozen",
        "complete",
    )
    return frozenset(
        config.paths[name].relative_to(config.root).as_posix()
        for name in names
        if os.path.lexists(config.paths[name])
    )


def _authenticate_authorization(
    config: FinalEvaluationConfig,
    readiness: Mapping[str, Any],
    readiness_commit: str,
) -> tuple[dict[str, Any], str]:
    payload, commit = _read_committed(
        config.paths["authorization"],
        label="one-time final-test authorization",
    )
    readiness_record = payload.get("evaluation_readiness")
    expected_readiness = {
        "path": config.paths["readiness"].relative_to(config.root).as_posix(),
        "file_sha256": sha256_file(config.paths["readiness"]),
        "bytes": config.paths["readiness"].stat().st_size,
        "commit_sha256": readiness_commit,
        "request_sha256": readiness.get("request_sha256"),
    }
    if (
        payload.get("schema_version") != AUTHORIZATION_SCHEMA_VERSION
        or payload.get("algorithm_version") != AUTHORIZATION_ALGORITHM_VERSION
        or payload.get("state")
        != "authorized_for_one_time_2025_evaluation"
        or payload.get("final_test_year") != 2025
        or payload.get("authorized") is not True
        or payload.get("values_read") is not False
        or payload.get("authorization_consumed") is not False
        or payload.get("evaluator_code_git_commit")
        != readiness.get("code_git_commit")
        or readiness_record != expected_readiness
        or payload.get("evaluator_module") != readiness.get("evaluator_module")
        or payload.get("evaluator_config") != readiness.get("evaluator_config")
        or payload.get("formal_model_lock")
        != readiness.get("formal_model_lock")
    ):
        raise FinalEvaluationProtocolError(
            "Authorization does not bind the exact current readiness contract."
        )
    return payload, commit


def _toml_at_git_commit(
    root: Path,
    commit: str,
    relative: str,
) -> dict[str, Any]:
    result = subprocess.run(
        ["git", "show", f"{commit}:{relative}"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise FinalEvaluationProtocolError(
            f"Cannot authenticate {relative} at authorized Git commit."
        )
    try:
        return tomllib.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise FinalEvaluationProtocolError(
            f"Authorized {relative} is not valid UTF-8 TOML."
        ) from error


def _verify_unlock_transition(
    config: FinalEvaluationConfig,
    authorization: Mapping[str, Any],
) -> dict[str, Any]:
    """Allow only the separately committed false-to-true unlock transition."""

    git = _git_state(
        config.root,
        allowed_untracked_paths=_allowed_state_paths(config),
    )
    current_head = str(git["head"])
    authorized_head = str(authorization["evaluator_code_git_commit"])
    if current_head == authorized_head:
        raise FinalEvaluationProtocolError(
            "The final-test unlock must be a separate committed transition "
            "after authorization."
        )
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", authorized_head, current_head],
        cwd=config.root,
        capture_output=True,
        text=True,
        check=False,
    )
    if ancestor.returncode != 0:
        raise FinalEvaluationProtocolError(
            "Current Git HEAD is not a descendant of the authorized evaluator."
        )
    changed = {
        line.strip().replace("\\", "/")
        for line in _git(
            config.root,
            "diff",
            "--name-only",
            authorized_head,
            current_head,
        ).stdout.splitlines()
        if line.strip()
    }
    if (
        not changed
        or not changed.issubset(ALLOWED_UNLOCK_TRANSITION_FILES)
        or "configs/research.toml" not in changed
        or "docs/DECISION_LOG.md" not in changed
    ):
        raise FinalEvaluationProtocolError(
            "Post-authorization Git history may contain only the documented "
            "research unlock and handoff updates."
        )

    old_raw = _toml_at_git_commit(
        config.root,
        authorized_head,
        "configs/research.toml",
    )
    current_research = load_config(config.paths["research_config"])
    old_study = old_raw.get("study")
    current_raw = current_research.raw
    current_study = current_raw.get("study")
    if not isinstance(old_study, dict) or not isinstance(current_study, dict):
        raise FinalEvaluationProtocolError("Research study configuration is invalid.")
    old_without_unlock = json.loads(json.dumps(old_raw))
    current_without_unlock = json.loads(json.dumps(current_raw))
    old_unlock = old_without_unlock["study"].pop("unlock_final_test", None)
    current_unlock = current_without_unlock["study"].pop(
        "unlock_final_test",
        None,
    )
    if (
        old_unlock is not False
        or current_unlock is not True
        or old_without_unlock != current_without_unlock
        or current_research.final_test_year != 2025
        or target_config_sha256(current_research)
        != config.locks["target_config_semantic_sha256"]
    ):
        raise FinalEvaluationProtocolError(
            "Research configuration changed by more than the one frozen unlock flag."
        )

    for key in ("evaluator_module", "evaluator_config"):
        expected = authorization[key]
        if not isinstance(expected, Mapping):
            raise FinalEvaluationProtocolError(
                f"Authorization {key} record is invalid."
            )
        current = _committed_file_record(
            config.root,
            config.root / str(expected["path"]),
            head=current_head,
            label=key,
        )
        if current != dict(expected):
            raise FinalEvaluationProtocolError(
                f"Committed {key} changed after authorization."
            )
    formal = authorization.get("formal_model_lock")
    if not isinstance(formal, Mapping):
        raise FinalEvaluationProtocolError(
            "Authorization formal model-lock record is invalid."
        )
    _verify_file_record(
        config.paths["formal_model_lock"],
        formal,
        label="Formal model lock",
    )
    return {
        "authorized_git_commit": authorized_head,
        "unlocked_git_commit": current_head,
        "changed_paths": sorted(changed),
        "research_config_file_sha256": sha256_file(
            config.paths["research_config"]
        ),
        "target_config_semantic_sha256": target_config_sha256(
            current_research
        ),
        "unlock_final_test": True,
    }


def _claim_request(
    config: FinalEvaluationConfig,
    *,
    readiness: Mapping[str, Any],
    readiness_commit: str,
    authorization_commit: str,
    unlock: Mapping[str, Any],
) -> dict[str, Any]:
    semantic_unlock = {
        "authorized_git_commit": unlock["authorized_git_commit"],
        "research_config_file_sha256": unlock[
            "research_config_file_sha256"
        ],
        "target_config_semantic_sha256": unlock[
            "target_config_semantic_sha256"
        ],
        "unlock_final_test": unlock["unlock_final_test"],
    }
    return {
        "schema_version": CLAIM_SCHEMA_VERSION,
        "algorithm_version": CLAIM_ALGORITHM_VERSION,
        "final_test_year": 2025,
        "readiness_commit_sha256": readiness_commit,
        "readiness_request_sha256": readiness["request_sha256"],
        "authorization_commit_sha256": authorization_commit,
        # Deliberately exclude the descendant documentation-only HEAD so a
        # mandatory failure/handoff commit cannot manufacture a second claim.
        "unlock_transition": semantic_unlock,
        "configuration_semantic_sha256": config.semantic_sha256,
        "pipeline_sha256": readiness["request"]["pipeline_sha256"],
        "formal_model_lock": readiness["formal_model_lock"],
        "models": readiness["request"]["models"],
        "predictors": readiness["request"]["predictors"],
        "landsat_inventory": readiness["request"]["landsat_inventory"],
        "paths": readiness["request"]["paths"],
        "exact_output_files": list(EXPECTED_OUTPUT_FILES),
    }


def _authenticate_claim(
    config: FinalEvaluationConfig,
    *,
    expected_request: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    payload, commit = _read_committed(
        config.paths["claim"],
        label="final-evaluation consumption claim",
    )
    request = payload.get("request")
    claim_id = canonical_sha256(expected_request)
    if (
        payload.get("schema_version") != CLAIM_SCHEMA_VERSION
        or payload.get("algorithm_version") != CLAIM_ALGORITHM_VERSION
        or payload.get("state") != "claimed_for_single_evaluation"
        or payload.get("final_test_year") != 2025
        or payload.get("claim_id") != claim_id
        or request != dict(expected_request)
        or payload.get("request_sha256") != claim_id
    ):
        raise FinalEvaluationProtocolError(
            "Existing consumption claim belongs to another evaluation request."
        )
    return payload, commit


def _create_or_authenticate_claim(
    config: FinalEvaluationConfig,
    *,
    request: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    if config.paths["claim"].exists():
        return _authenticate_claim(config, expected_request=request)
    if any(
        os.path.lexists(config.paths[name])
        for name in ("values_opened", "predictions_frozen", "complete")
    ):
        raise FinalEvaluationProtocolError(
            "Evaluation state exists without its immutable consumption claim."
        )
    claim_id = canonical_sha256(request)
    payload = _committed_payload(
        {
            "schema_version": CLAIM_SCHEMA_VERSION,
            "algorithm_version": CLAIM_ALGORITHM_VERSION,
            "state": "claimed_for_single_evaluation",
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "final_test_year": 2025,
            "claim_id": claim_id,
            "request": dict(request),
            "request_sha256": claim_id,
            "values_read": False,
            "completed": False,
        }
    )
    _exclusive_json(
        payload,
        config.paths["claim"],
        label="CONSUMPTION_CLAIM.json",
    )
    return _authenticate_claim(config, expected_request=request)


def _load_predictors_after_claim(
    config: FinalEvaluationConfig,
    *,
    readiness: Mapping[str, Any],
    formal: Mapping[str, Any],
) -> pd.DataFrame:
    record = readiness["request"]["predictors"]
    if not isinstance(record, Mapping):
        raise FinalEvaluationProtocolError(
            "Readiness predictor commitment is invalid."
        )
    path = config.paths["predictor_table"]
    _verify_file_record(
        path,
        {
            "sha256": record.get("file_sha256"),
            "bytes": record.get("file_bytes"),
        },
        label="Frozen predictor table",
    )
    predictors = pd.read_parquet(path)
    schema = canonical_sha256(
        [(column, str(dtype)) for column, dtype in predictors.dtypes.items()]
    )
    semantic = canonical_frame_sha256(
        predictors,
        sort_by=["target_date", "tract_geoid"],
    )
    key_semantic = canonical_frame_sha256(
        predictors,
        sort_by=["target_date", "tract_geoid"],
        columns=["tract_geoid", "target_date"],
    )
    if (
        schema != record.get("schema_sha256")
        or semantic != record.get("semantic_sha256")
        or key_semantic != record.get("key_semantic_sha256")
    ):
        raise FinalEvaluationProtocolError(
            "Predictor semantic/schema commitment failed after deserialization."
        )
    return _validate_predictor_frame(
        predictors,
        formal=formal,
        config=config,
    )


def _loaded_bundle_matches_lock(
    bundle: Mapping[str, Any],
    locked: Mapping[str, Any],
    *,
    model_id: str,
) -> None:
    comparisons = {
        "candidate_id": "selected_candidate_id",
        "candidate_parameters": "selected_parameters",
        "random_state": "random_state",
        "feature_names": "feature_names",
        "training_row_count": "training_row_count",
        "training_date_count": "training_date_count",
        "training_spatial_block_count": "training_spatial_block_count",
        "training_keys_sha256": "training_keys_sha256",
    }
    if bundle.get("model_id") != model_id or any(
        bundle.get(bundle_key) != locked.get(lock_key)
        for bundle_key, lock_key in comparisons.items()
    ):
        raise FinalEvaluationProtocolError(
            f"Loaded {model_id} bundle metadata disagrees with MODEL_LOCK."
        )


def _load_locked_models(
    config: FinalEvaluationConfig,
    *,
    formal: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    development = formal.get("development_build")
    models = formal.get("models")
    if (
        not isinstance(development, Mapping)
        or not isinstance(development.get("path"), str)
        or not isinstance(models, Mapping)
        or set(models) != {"B1", "M2"}
    ):
        raise FinalEvaluationProtocolError(
            "Formal model build/artifact records are incomplete."
        )
    provenance_path = Path(development["path"]).resolve()
    try:
        provenance_path.relative_to(config.root)
    except ValueError as error:
        raise FinalEvaluationProtocolError(
            "Development model provenance is outside the project."
        ) from error
    if (
        not provenance_path.is_file()
        or sha256_file(provenance_path) != development.get("sha256")
    ):
        raise FinalEvaluationProtocolError(
            "Development model provenance byte lock failed."
        )
    build, build_commit = _read_committed(
        provenance_path,
        label="development final-model provenance",
    )
    if build_commit != development.get("commit_sha256"):
        raise FinalEvaluationProtocolError(
            "Development final-model provenance commit drifted."
        )
    build_models = build.get("models")
    if not isinstance(build_models, Mapping) or set(build_models) != {"B1", "M2"}:
        raise FinalEvaluationProtocolError(
            "Development final-model artifact records are incomplete."
        )
    loaded: dict[str, dict[str, Any]] = {}
    for model_id in ("B1", "M2"):
        locked = models[model_id]
        built = build_models[model_id]
        if not isinstance(locked, Mapping) or not isinstance(built, Mapping):
            raise FinalEvaluationProtocolError(
                f"{model_id} artifact record is invalid."
            )
        filename = built.get("path")
        if (
            built.get("path_base") != "run_directory"
            or not isinstance(filename, str)
            or Path(filename).name != filename
            or filename != locked.get("artifact_path")
            or built.get("sha256") != locked.get("fitted_pipeline_sha256")
            or built.get("bytes") != locked.get("fitted_pipeline_bytes")
        ):
            raise FinalEvaluationProtocolError(
                f"{model_id} artifact path/hash record drifted."
            )
        artifact = (provenance_path.parent / filename).resolve()
        if artifact.parent != provenance_path.parent:
            raise FinalEvaluationProtocolError(
                f"{model_id} artifact escaped its authenticated run directory."
            )
        try:
            bundle = load_hashed_model_bundle(
                artifact,
                expected_sha256=str(built["sha256"]),
                expected_bytes=int(built["bytes"]),
                expected_model_id=model_id,
                expected_candidate_id=str(locked["selected_candidate_id"]),
            )
        except FinalModelError as error:
            raise FinalEvaluationProtocolError(str(error)) from error
        _loaded_bundle_matches_lock(bundle, locked, model_id=model_id)
        loaded[model_id] = bundle
    return loaded


def _prediction_output_record(path: Path, frame: pd.DataFrame) -> dict[str, Any]:
    record = parquet_file_record(path, frame)
    return {
        "filename": path.name,
        **record,
        "semantic_sha256": canonical_frame_sha256(
            frame,
            sort_by=["target_date", "tract_geoid"],
        ),
        "key_semantic_sha256": canonical_frame_sha256(
            frame,
            sort_by=["target_date", "tract_geoid"],
            columns=["tract_geoid", "target_date"],
        ),
    }


def _authenticate_prediction_marker(
    config: FinalEvaluationConfig,
    *,
    claim: Mapping[str, Any],
    claim_commit: str,
    read_frame: bool = True,
) -> tuple[dict[str, Any], str, pd.DataFrame | None]:
    claim_id = str(claim.get("claim_id", ""))
    request = claim.get("request")
    predictors = request.get("predictors") if isinstance(request, Mapping) else None
    models = request.get("models") if isinstance(request, Mapping) else None
    if (
        not claim_id
        or not isinstance(predictors, Mapping)
        or not isinstance(models, Mapping)
        or set(models) != {"B1", "M2"}
    ):
        raise FinalEvaluationProtocolError(
            "Consumption claim lacks frozen prediction dependencies."
        )
    expected_models = {
        model_id: {
            "artifact_sha256": models[model_id]["fitted_pipeline_sha256"],
            "artifact_bytes": models[model_id]["fitted_pipeline_bytes"],
            "candidate_id": models[model_id]["selected_candidate_id"],
            "feature_names": models[model_id]["feature_names"],
        }
        for model_id in ("B1", "M2")
    }
    marker, commit = _read_committed(
        config.paths["predictions_frozen"],
        label="blind-predictions marker",
    )
    record = marker.get("output")
    if (
        marker.get("schema_version") != 1
        or marker.get("algorithm_version")
        != PREDICTIONS_ALGORITHM_VERSION
        or marker.get("state") != "blind_predictions_frozen"
        or marker.get("claim_id") != claim_id
        or marker.get("claim_commit_sha256") != claim_commit
        or marker.get("target_or_qa_values_read") is not False
        or marker.get("row_count") != config.analysis["expected_key_count"]
        or marker.get("predictor_file_sha256") != predictors.get("file_sha256")
        or marker.get("models") != expected_models
        or not isinstance(record, Mapping)
        or record.get("filename") != "blind_predictions.parquet"
        or _SHA256.fullmatch(str(record.get("semantic_sha256"))) is None
        or _SHA256.fullmatch(str(record.get("key_semantic_sha256"))) is None
        or record.get("key_semantic_sha256")
        != predictors.get("key_semantic_sha256")
    ):
        raise FinalEvaluationProtocolError(
            "Blind-predictions marker is not the exact current claim."
        )
    staging_path = config.paths["staging_root"] / "blind_predictions.parquet"
    final_path = (
        config.paths["final_output_directory"] / "blind_predictions.parquet"
    )
    if staging_path.is_file() and final_path.is_file():
        raise FinalEvaluationProtocolError(
            "Blind predictions exist in both staging and final directories."
        )
    path = final_path if final_path.is_file() else staging_path
    _verify_file_record(path, record, label="Frozen blind predictions")
    if not read_frame:
        return marker, commit, None
    frame = pd.read_parquet(path)
    if (
        frame.columns.tolist() != list(PREDICTION_COLUMNS)
        or len(frame) != config.analysis["expected_key_count"]
        or frame.duplicated(list(KEY_COLUMNS)).any()
        or canonical_frame_sha256(
            frame,
            sort_by=["target_date", "tract_geoid"],
        )
        != record.get("semantic_sha256")
        or canonical_frame_sha256(
            frame,
            sort_by=["target_date", "tract_geoid"],
            columns=["tract_geoid", "target_date"],
        )
        != record.get("key_semantic_sha256")
    ):
        raise FinalEvaluationProtocolError(
            "Frozen blind-prediction semantic contract failed."
        )
    values = frame.loc[:, ["y_pred_b1", "y_pred_m2"]].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise FinalEvaluationProtocolError(
            "Frozen blind predictions contain non-finite values."
        )
    return marker, commit, frame


def _authenticate_and_replay_blind_predictions(
    config: FinalEvaluationConfig,
    *,
    readiness: Mapping[str, Any],
    formal: Mapping[str, Any],
    claim: Mapping[str, Any],
    claim_commit: str,
) -> tuple[dict[str, Any], str, pd.DataFrame]:
    """Reproduce both frozen prediction surfaces before the value boundary."""

    marker, commit, observed = _authenticate_prediction_marker(
        config,
        claim=claim,
        claim_commit=claim_commit,
    )
    if observed is None:
        raise AssertionError("Prediction replay requires the frozen table.")
    predictors = _load_predictors_after_claim(
        config,
        readiness=readiness,
        formal=formal,
    )
    bundles = _load_locked_models(config, formal=formal)
    expected = predictors.loc[:, list(KEY_COLUMNS)].copy()
    expected["y_pred_b1"] = predict_model_bundle(bundles["B1"], predictors)
    expected["y_pred_m2"] = predict_model_bundle(bundles["M2"], predictors)
    expected = expected.loc[:, list(PREDICTION_COLUMNS)].sort_values(
        ["target_date", "tract_geoid"],
        kind="stable",
    ).reset_index(drop=True)
    if not observed.equals(expected):
        raise FinalEvaluationProtocolError(
            "Frozen blind predictions do not reproduce from the locked "
            "predictor table and fitted models."
        )
    return marker, commit, observed


def _freeze_blind_predictions(
    config: FinalEvaluationConfig,
    *,
    readiness: Mapping[str, Any],
    formal: Mapping[str, Any],
    claim: Mapping[str, Any],
    claim_commit: str,
) -> BlindPredictionArtifacts:
    claim_id = str(claim["claim_id"])
    if config.paths["predictions_frozen"].exists():
        marker, _, frame = _authenticate_and_replay_blind_predictions(
            config,
            readiness=readiness,
            formal=formal,
            claim=claim,
            claim_commit=claim_commit,
        )
        return BlindPredictionArtifacts(
            frame=frame,
            marker=marker,
            staging_directory=config.paths["staging_root"],
        )
    if config.paths["values_opened"].exists():
        raise FinalEvaluationProtocolError(
            "Target values were marked opened before predictions were frozen."
        )
    if config.paths["final_output_directory"].exists():
        raise FinalEvaluationProtocolError(
            "Final output exists before blind predictions are committed."
        )
    staging = config.paths["staging_root"]
    staging.mkdir(parents=True, exist_ok=True)
    predictors = _load_predictors_after_claim(
        config,
        readiness=readiness,
        formal=formal,
    )
    bundles = _load_locked_models(config, formal=formal)
    predictions: dict[str, np.ndarray] = {}
    for model_id in ("B1", "M2"):
        predictions[model_id] = predict_model_bundle(
            bundles[model_id],
            predictors,
        )
    frame = predictors.loc[:, list(KEY_COLUMNS)].copy()
    frame["y_pred_b1"] = predictions["B1"]
    frame["y_pred_m2"] = predictions["M2"]
    frame = frame.loc[:, list(PREDICTION_COLUMNS)].sort_values(
        ["target_date", "tract_geoid"],
        kind="stable",
    ).reset_index(drop=True)
    output = staging / "blind_predictions.parquet"
    if output.exists():
        existing = pd.read_parquet(output)
        if (
            existing.columns.tolist() != frame.columns.tolist()
            or not existing.equals(frame)
        ):
            raise FinalEvaluationProtocolError(
                "Uncommitted blind predictions disagree with deterministic replay."
            )
    else:
        atomic_parquet(frame, output)
    record = _prediction_output_record(output, frame)
    model_records = {
        model_id: {
            "artifact_sha256": formal["models"][model_id][
                "fitted_pipeline_sha256"
            ],
            "artifact_bytes": formal["models"][model_id][
                "fitted_pipeline_bytes"
            ],
            "candidate_id": formal["models"][model_id][
                "selected_candidate_id"
            ],
            "feature_names": formal["models"][model_id]["feature_names"],
        }
        for model_id in ("B1", "M2")
    }
    marker = _committed_payload(
        {
            "schema_version": 1,
            "algorithm_version": PREDICTIONS_ALGORITHM_VERSION,
            "state": "blind_predictions_frozen",
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "claim_id": claim_id,
            "claim_commit_sha256": claim_commit,
            "target_or_qa_values_read": False,
            "row_count": len(frame),
            "models": model_records,
            "predictor_file_sha256": readiness["request"]["predictors"][
                "file_sha256"
            ],
            "output": record,
        }
    )
    _exclusive_json(
        marker,
        config.paths["predictions_frozen"],
        label="PREDICTIONS_FROZEN.json",
    )
    authenticated, _, authenticated_frame = (
        _authenticate_and_replay_blind_predictions(
        config,
        readiness=readiness,
        formal=formal,
        claim=claim,
        claim_commit=claim_commit,
        )
    )
    return BlindPredictionArtifacts(
        frame=authenticated_frame,
        marker=authenticated,
        staging_directory=staging,
    )


def _authenticate_values_opened(
    config: FinalEvaluationConfig,
    *,
    claim_id: str,
    claim_commit: str,
    predictions_commit: str,
) -> tuple[dict[str, Any], str]:
    payload, commit = _read_committed(
        config.paths["values_opened"],
        label="target-values-opened marker",
    )
    if (
        payload.get("schema_version") != 1
        or payload.get("algorithm_version") != VALUES_OPENED_ALGORITHM_VERSION
        or payload.get("state") != "target_and_qa_values_opened"
        or payload.get("final_test_year") != 2025
        or payload.get("claim_id") != claim_id
        or payload.get("claim_commit_sha256") != claim_commit
        or payload.get("predictions_commit_sha256") != predictions_commit
        or payload.get("blind_predictions_frozen") is not True
        or payload.get("values_read") is not True
    ):
        raise FinalEvaluationProtocolError(
            "VALUES_OPENED marker is not bound to the current frozen predictions."
        )
    return payload, commit


def _values_opened_callback(
    config: FinalEvaluationConfig,
    *,
    claim: Mapping[str, Any],
    claim_commit: str,
    predictions: BlindPredictionArtifacts,
    readiness: Mapping[str, Any],
    formal: Mapping[str, Any],
) -> Callable[[], None]:
    claim_id = str(claim["claim_id"])
    prediction_commit = str(predictions.marker["commit_sha256"])

    def before_first_value_access() -> None:
        _, replayed_commit, replayed_frame = (
            _authenticate_and_replay_blind_predictions(
            config,
            readiness=readiness,
            formal=formal,
            claim=claim,
            claim_commit=claim_commit,
            )
        )
        if (
            replayed_commit != prediction_commit
            or not replayed_frame.equals(predictions.frame)
        ):
            raise FinalEvaluationProtocolError(
                "Value-opening callback received a stale prediction surface."
            )
        if config.paths["values_opened"].exists():
            _authenticate_values_opened(
                config,
                claim_id=claim_id,
                claim_commit=claim_commit,
                predictions_commit=prediction_commit,
            )
            return
        payload = _committed_payload(
            {
                "schema_version": 1,
                "algorithm_version": VALUES_OPENED_ALGORITHM_VERSION,
                "state": "target_and_qa_values_opened",
                "generated_at_utc": datetime.now(UTC).isoformat(),
                "final_test_year": 2025,
                "claim_id": claim_id,
                "claim_commit_sha256": claim_commit,
                "predictions_commit_sha256": prediction_commit,
                "blind_predictions_frozen": True,
                "values_read": True,
                "completion_published": False,
            }
        )
        _exclusive_json(
            payload,
            config.paths["values_opened"],
            label="VALUES_OPENED.json",
        )
        _authenticate_values_opened(
            config,
            claim_id=claim_id,
            claim_commit=claim_commit,
            predictions_commit=prediction_commit,
        )

    return before_first_value_access


def _reporting_settings(config: FinalEvaluationConfig) -> Any:
    from la_heat.final_evaluation_reporting import (
        FinalEvaluationReportingSettings,
    )

    research = load_config(config.paths["research_config"])
    landsat = research.raw["landsat"]
    return FinalEvaluationReportingSettings(
        final_test_year=int(config.analysis["final_test_year"]),
        baseline_model_id=str(config.analysis["baseline_model_id"]),
        primary_model_id=str(config.analysis["primary_model_id"]),
        primary_metric=str(config.analysis["primary_metric"]),
        evaluation_cohort=str(config.analysis["evaluation_cohort"]),
        minimum_usable_date_count_for_metrics=int(
            config.analysis["minimum_usable_date_count_for_metrics"]
        ),
        bootstrap_method=str(config.bootstrap["method"]),
        bootstrap_sampling_unit=str(config.bootstrap["sampling_unit"]),
        bootstrap_seed=int(config.bootstrap["seed"]),
        bootstrap_replicates=int(config.bootstrap["replicates"]),
        confidence_level=float(config.bootstrap["confidence_level"]),
        minimum_relative_mae_improvement_fraction=float(
            config.success_gates[
                "minimum_relative_mae_improvement_fraction"
            ]
        ),
        minimum_median_per_date_spearman=float(
            config.success_gates["minimum_median_per_date_spearman"]
        ),
        uncertainty_relative_ci_lower_must_exceed=float(
            config.success_gates[
                "uncertainty_relative_ci_lower_must_exceed"
            ]
        ),
        hotspot_positive_fraction=float(config.hotspot["positive_fraction"]),
        minimum_tract_footprint_fraction=float(
            landsat["minimum_tract_footprint_fraction"]
        ),
        minimum_valid_pixel_fraction=float(
            landsat["minimum_valid_pixel_fraction"]
        ),
        minimum_valid_pixels_per_tract=int(
            landsat["minimum_valid_pixels_per_tract"]
        ),
        minimum_city_union_coverage_fraction=float(
            landsat["minimum_city_union_coverage_fraction"]
        ),
        minimum_date_tract_retention_fraction=float(
            landsat["minimum_date_tract_retention_fraction"]
        ),
        excluded_qa_pixel_bits=tuple(
            int(bit) for bit in landsat["excluded_qa_pixel_bits"]
        ),
        minimum_cloud_distance_km=float(
            landsat["minimum_cloud_distance_km"]
        ),
        apply_st_uncertainty_threshold=bool(
            landsat["apply_st_uncertainty_threshold"]
        ),
        maximum_st_uncertainty_kelvin=float(
            landsat["maximum_st_uncertainty_kelvin"]
        ),
        exclude_terrain_occlusion=bool(
            landsat["exclude_terrain_occlusion"]
        ),
    )


def _joined_reporting_rows(
    *,
    target_qa: pd.DataFrame,
    date_summary: pd.DataFrame,
    blind_predictions: pd.DataFrame,
    predictors: pd.DataFrame,
) -> pd.DataFrame:
    keys = ["tract_geoid", "target_date"]
    if (
        len(target_qa) != len(blind_predictions)
        or target_qa.duplicated(keys).any()
        or blind_predictions.duplicated(keys).any()
        or predictors.duplicated(keys).any()
    ):
        raise FinalEvaluationProtocolError(
            "Target, prediction, or predictor key surfaces are not exactly unique."
        )
    target = target_qa.copy()
    target["tract_geoid"] = target["tract_geoid"].astype(str)
    target["target_date"] = pd.to_datetime(
        target["target_date"],
        errors="raise",
    ).astype("datetime64[ns]")
    predictions = blind_predictions.copy()
    predictions["tract_geoid"] = predictions["tract_geoid"].astype(str)
    predictions["target_date"] = pd.to_datetime(
        predictions["target_date"],
        errors="raise",
    ).astype("datetime64[ns]")
    predictor_keys = predictors.loc[:, keys].copy()
    predictor_keys["tract_geoid"] = predictor_keys["tract_geoid"].astype(str)
    predictor_keys["target_date"] = pd.to_datetime(
        predictor_keys["target_date"],
        errors="raise",
    ).astype("datetime64[ns]")
    predictor_keys["sentinel_available"] = ~predictors.loc[
        :,
        list(SENTINEL_FEATURES),
    ].isna().all(axis=1)
    joined = target.merge(
        predictions,
        on=keys,
        how="outer",
        validate="one_to_one",
        indicator="_prediction_merge",
        sort=False,
    )
    if not joined["_prediction_merge"].eq("both").all():
        raise FinalEvaluationProtocolError(
            "Target keys are not exactly identical to blind-prediction keys."
        )
    joined = joined.drop(columns="_prediction_merge").merge(
        predictor_keys,
        on=keys,
        how="outer",
        validate="one_to_one",
        indicator="_predictor_merge",
        sort=False,
    )
    if not joined["_predictor_merge"].eq("both").all():
        raise FinalEvaluationProtocolError(
            "Target keys are not exactly identical to predictor keys."
        )
    joined = joined.drop(columns="_predictor_merge")
    dates = date_summary.loc[
        :,
        [
            "target_date",
            "relative_endpoint_coverage_pass",
            "date_exclusion_reason",
            "union_city_coverage_fraction",
            "retained_tract_count",
            "retained_tract_fraction",
            "minimum_eligible_joint_cell_retention_fraction",
        ],
    ].copy()
    dates["target_date"] = pd.to_datetime(
        dates["target_date"],
        errors="raise",
    ).astype("datetime64[ns]")
    if dates["target_date"].duplicated().any():
        raise FinalEvaluationProtocolError(
            "Final date summary contains duplicate physical overpasses."
        )
    joined = joined.merge(
        dates,
        on="target_date",
        how="left",
        validate="many_to_one",
        suffixes=("", "_date_summary"),
    )
    if joined["relative_endpoint_coverage_pass"].isna().any():
        raise FinalEvaluationProtocolError(
            "A target row lacks its frozen relative-endpoint gate."
        )
    if "date_exclusion_reason_date_summary" in joined:
        left = joined["date_exclusion_reason"].astype("string")
        right = joined["date_exclusion_reason_date_summary"].astype("string")
        if not left.fillna("<NA>").eq(right.fillna("<NA>")).all():
            raise FinalEvaluationProtocolError(
                "Target and date-summary exclusion reasons disagree."
            )
        joined = joined.drop(columns="date_exclusion_reason_date_summary")
    joined["sensor"] = joined["platform"].astype(str)
    joined["y_true"] = joined["target_lst_c"].astype(float)
    return joined.sort_values(keys[::-1], kind="stable").reset_index(drop=True)


def _write_target_and_reports(
    config: FinalEvaluationConfig,
    *,
    target_artifacts: Any,
    predictions: BlindPredictionArtifacts,
    predictors: pd.DataFrame,
    inventory: Any,
) -> dict[str, Any]:
    from la_heat.final_evaluation_reporting import (
        generate_final_evaluation_reports,
    )

    staging = config.paths["staging_root"]
    target_paths = {
        "final_target_qa.parquet": target_artifacts.target_qa,
        "date_summary.parquet": target_artifacts.date_summary,
        "scene_contributions.parquet": target_artifacts.scene_contributions,
    }
    for filename, frame in target_paths.items():
        atomic_parquet(frame, staging / filename)
    joined = _joined_reporting_rows(
        target_qa=target_artifacts.target_qa,
        date_summary=target_artifacts.date_summary,
        blind_predictions=predictions.frame,
        predictors=predictors,
    )
    reports = generate_final_evaluation_reports(
        joined,
        _reporting_settings(config),
        staging,
        tract_geometries=inventory.tracts,
    )
    atomic_parquet(
        reports.tables.evaluation_rows,
        staging / "evaluation_rows.parquet",
    )
    return {
        "target_audit": dict(target_artifacts.audit),
        "evaluation_row_count": int(len(reports.tables.evaluation_rows)),
        "inventory_date_count": int(target_artifacts.audit["inventory_date_count"]),
        "usable_date_count": int(
            reports.tables.evaluation_rows["target_date"].nunique()
        ),
        "independent_spatial_block_count": int(
            reports.tables.evaluation_rows["spatial_block"].nunique()
        ),
        "tract_choropleth": {
            "tract_count": int(len(reports.tract_map_frame)),
            "tract_manifest_sha256": inventory.locks[
                "tract_manifest_sha256"
            ],
            "primary_tract_file_sha256": inventory.locks[
                "primary_tract_file_sha256"
            ],
            "crs": inventory.tracts.crs.to_string(),
            "aggregation": (
                "unweighted_per_tract_mean_over_all_usable_matched_dates"
            ),
            "geometry_used_for_diagnostics_only": True,
            "coordinates_used_as_predictors": False,
        },
    }


def _output_table_column_contracts() -> dict[str, tuple[str, ...]]:
    """Return the predeclared exact schema for every structured table."""

    from la_heat.final_evaluation_reporting import (
        EVALUATION_ROW_COLUMNS,
        REPORT_TABLE_COLUMN_CONTRACTS,
    )
    from la_heat.final_evaluation_targets import (
        FINAL_DATE_SUMMARY_COLUMNS,
        FINAL_SCENE_CONTRIBUTION_COLUMNS,
        FINAL_TARGET_COLUMNS,
    )

    contracts = {
        "blind_predictions.parquet": PREDICTION_COLUMNS,
        "final_target_qa.parquet": FINAL_TARGET_COLUMNS,
        "date_summary.parquet": FINAL_DATE_SUMMARY_COLUMNS,
        "scene_contributions.parquet": FINAL_SCENE_CONTRIBUTION_COLUMNS,
        "evaluation_rows.parquet": EVALUATION_ROW_COLUMNS,
        **dict(REPORT_TABLE_COLUMN_CONTRACTS),
    }
    expected = set(OUTPUT_SEMANTIC_SORT_BY)
    if (
        set(contracts) != expected
        or set(OUTPUT_PRIMARY_KEYS) != expected
        or any(
            not set(OUTPUT_SEMANTIC_SORT_BY[filename]).issubset(columns)
            or not set(OUTPUT_PRIMARY_KEYS[filename]).issubset(columns)
            for filename, columns in contracts.items()
        )
    ):
        raise FinalEvaluationProtocolError(
            "Structured output schema/key registry is internally inconsistent."
        )
    return contracts


def _read_output_table(path: Path, *, filename: str) -> pd.DataFrame:
    """Read one output table without losing identifier semantics."""

    contracts = _output_table_column_contracts()
    if filename not in contracts or path.name != filename:
        raise FinalEvaluationProtocolError(
            f"Unknown structured final output: {filename}"
        )
    expected_columns = contracts[filename]
    if path.suffix == ".parquet":
        frame = pd.read_parquet(path)
    elif path.suffix == ".csv":
        dtype = (
            {"tract_geoid": "string"}
            if "tract_geoid" in expected_columns
            else None
        )
        frame = pd.read_csv(path, dtype=dtype)
    else:
        raise FinalEvaluationProtocolError(
            f"Unsupported structured final output: {filename}"
        )
    if frame.columns.tolist() != list(expected_columns):
        raise FinalEvaluationProtocolError(
            f"Final output exact column contract failed: {filename}"
        )
    primary_key = list(OUTPUT_PRIMARY_KEYS[filename])
    canonical_primary_key = frame.loc[:, primary_key].copy()
    canonical_dates: pd.Series | None = None
    if "target_date" in frame:
        normalized_dates: list[str | None] = []
        for value in frame["target_date"]:
            if pd.isna(value):
                normalized_dates.append(None)
                continue
            try:
                timestamp = pd.Timestamp(value)
            except (TypeError, ValueError) as error:
                raise FinalEvaluationProtocolError(
                    f"Final output target-date domain failed: {filename}"
                ) from error
            if (
                timestamp.tzinfo is not None
                or timestamp != timestamp.normalize()
                or timestamp.year != 2025
            ):
                raise FinalEvaluationProtocolError(
                    f"Final output target-date domain failed: {filename}"
                )
            normalized_dates.append(timestamp.date().isoformat())
        canonical_dates = pd.Series(
            normalized_dates,
            index=frame.index,
            dtype="string",
        )
        if "target_date" in canonical_primary_key:
            canonical_primary_key["target_date"] = canonical_dates
    if filename == "qa_missingness_summary.csv":
        overall = frame["summary_level"].eq("overall")
        date_level = frame["summary_level"].eq("date")
        valid_qa_key = (
            frame["summary_level"].notna().all()
            and bool(overall.sum() == 1)
            and bool((overall | date_level).all())
            and canonical_dates is not None
            and canonical_dates.loc[overall].isna().all()
            and canonical_dates.loc[date_level].notna().all()
        )
        if not valid_qa_key:
            raise FinalEvaluationProtocolError(
                "Final QA summary-level/date key contract failed."
            )
    elif canonical_primary_key.isna().to_numpy().any():
        raise FinalEvaluationProtocolError(
            f"Final output primary key contains missing values: {filename}"
        )
    if canonical_primary_key.duplicated().any():
        raise FinalEvaluationProtocolError(
            f"Final output primary key is not unique: {filename}"
        )
    if "tract_geoid" in frame:
        geoids = frame["tract_geoid"].astype("string")
        valid = geoids.str.fullmatch(r"\d{11}").fillna(False)
        if not bool(valid.all()):
            raise FinalEvaluationProtocolError(
                f"Final output contains an invalid 11-digit GEOID: {filename}"
            )
    boolean_columns = {
        "final_target_qa.parquet": {
            "target_available": False,
            "date_usable": False,
            "relative_hotspot_top20": True,
        },
        "date_summary.parquet": {
            "date_usable": False,
            "relative_endpoint_coverage_pass": False,
        },
        "evaluation_rows.parquet": {
            "sentinel_available": False,
            "target_available": False,
            "date_usable": False,
            "relative_endpoint_coverage_pass": False,
            "relative_hotspot_top20": True,
        },
        "per_date_metrics.csv": {"spearman_defined": False},
        "protocol_gates.csv": {
            "passed": False,
            "required_for_protocol_success": False,
            "overall_protocol_success_gate_pass": False,
        },
        "sensor_per_date_metrics.csv": {"spearman_defined": False},
        "qa_missingness_summary.csv": {
            "date_usable": True,
            "relative_endpoint_coverage_pass": True,
        },
    }.get(filename, {})
    for column, allow_missing in boolean_columns.items():
        values = frame[column]
        nonmissing = values.dropna()
        if (
            (not allow_missing and values.isna().any())
            or not nonmissing.map(
                lambda value: isinstance(value, (bool, np.bool_))
            ).all()
        ):
            raise FinalEvaluationProtocolError(
                f"Final output boolean domain failed: {filename}.{column}"
            )
    return frame


def _inspect_figure_output(
    path: Path,
    *,
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify that a declared figure is a readable file of the claimed type."""

    expected_format = str(contract["format"])
    if expected_format == "pdf":
        try:
            content = path.read_bytes()
        except OSError as error:
            raise FinalEvaluationProtocolError(
                f"Final PDF output cannot be read: {path.name}"
            ) from error
        page_count = len(re.findall(rb"/Type\s*/Page\b", content))
        if (
            not content.startswith(b"%PDF-")
            or not content.rstrip().endswith(b"%%EOF")
            or page_count != int(contract["panel_count"])
        ):
            raise FinalEvaluationProtocolError(
                f"Final PDF format/page contract failed: {path.name}"
            )
        return {
            "verified_format": "pdf",
            "verified_page_count": page_count,
        }
    if expected_format == "png":
        try:
            from PIL import Image, UnidentifiedImageError

            with Image.open(path) as image:
                observed_format = image.format
                width, height = image.size
                image.verify()
        except (OSError, UnidentifiedImageError) as error:
            raise FinalEvaluationProtocolError(
                f"Final PNG output cannot be decoded: {path.name}"
            ) from error
        if observed_format != "PNG" or width <= 0 or height <= 0:
            raise FinalEvaluationProtocolError(
                f"Final PNG format/dimension contract failed: {path.name}"
            )
        return {
            "verified_format": "png",
            "pixel_width": int(width),
            "pixel_height": int(height),
        }
    raise FinalEvaluationProtocolError(
        f"Unknown final figure format contract: {path.name}"
    )


def _assert_replayed_figure_outputs(
    directory: Path,
    *,
    config: FinalEvaluationConfig,
    claim: Mapping[str, Any],
    output_records: Mapping[str, Any],
) -> None:
    """Re-render all three figures and require byte-identical outputs."""

    import geopandas as gpd

    from la_heat.final_evaluation_reporting import (
        _write_hotspot_figure,
        _write_per_date_figure,
        _write_tract_maps,
    )

    inventory, inventory_record = _default_inventory_authenticator(config)
    request = claim.get("request")
    claimed_inventory = (
        request.get("landsat_inventory")
        if isinstance(request, Mapping)
        else None
    )
    if inventory_record != claimed_inventory:
        raise FinalEvaluationProtocolError(
            "Figure replay inventory differs from the consumption claim."
        )
    tract_summary = _read_output_table(
        directory / "tract_choropleth_summary.csv",
        filename="tract_choropleth_summary.csv",
    )
    geometry_name = inventory.tracts.geometry.name
    geography = inventory.tracts.loc[
        :, ["GEOID", "spatial_block", geometry_name]
    ].copy()
    geography["tract_geoid"] = geography["GEOID"].astype("string")
    geography["spatial_block"] = geography["spatial_block"].astype("string")
    summary_blocks = (
        tract_summary.loc[:, ["tract_geoid", "spatial_block"]]
        .astype({"tract_geoid": "string", "spatial_block": "string"})
        .sort_values("tract_geoid", kind="stable")
        .reset_index(drop=True)
    )
    geography_blocks = (
        geography.loc[:, ["tract_geoid", "spatial_block"]]
        .sort_values("tract_geoid", kind="stable")
        .reset_index(drop=True)
    )
    if not summary_blocks.equals(geography_blocks):
        raise FinalEvaluationProtocolError(
            "Figure replay tract/block geography differs from its summary."
        )
    map_frame = tract_summary.merge(
        geography.loc[:, ["tract_geoid", geometry_name]],
        on="tract_geoid",
        how="left",
        validate="one_to_one",
        sort=False,
    )
    if map_frame[geometry_name].isna().any():
        raise FinalEvaluationProtocolError(
            "Figure replay is missing authenticated tract geometry."
        )
    map_frame = gpd.GeoDataFrame(
        map_frame,
        geometry=geometry_name,
        crs=inventory.tracts.crs,
    )
    per_date = _read_output_table(
        directory / "per_date_metrics.csv",
        filename="per_date_metrics.csv",
    )
    hotspot = _read_output_table(
        directory / "hotspot_per_date.csv",
        filename="hotspot_per_date.csv",
    )
    per_date["target_date"] = pd.to_datetime(
        per_date["target_date"],
        errors="raise",
    )
    hotspot["target_date"] = pd.to_datetime(
        hotspot["target_date"],
        errors="raise",
    )
    settings = _reporting_settings(config)
    with tempfile.TemporaryDirectory(
        prefix="la-heat-final-figure-replay-"
    ) as temporary:
        replay = Path(temporary)
        paths = {
            "observed_predicted_residual_maps.pdf": replay
            / "observed_predicted_residual_maps.pdf",
            "per_date_error_and_rank.png": replay
            / "per_date_error_and_rank.png",
            "hotspot_precision_recall.png": replay
            / "hotspot_precision_recall.png",
        }
        _write_tract_maps(
            map_frame,
            paths["observed_predicted_residual_maps.pdf"],
        )
        _write_per_date_figure(
            per_date,
            paths["per_date_error_and_rank.png"],
            settings,
        )
        _write_hotspot_figure(
            hotspot,
            paths["hotspot_precision_recall.png"],
            settings,
        )
        for filename, path in paths.items():
            record = output_records.get(filename)
            if (
                not isinstance(record, Mapping)
                or sha256_file(path) != record.get("sha256")
                or path.read_bytes() != (directory / filename).read_bytes()
            ):
                raise FinalEvaluationProtocolError(
                    f"Final figure does not replay deterministically: {filename}"
                )


def _assert_prediction_output_binding(
    output_record: Mapping[str, Any],
    marker_record: Mapping[str, Any],
) -> None:
    """Bind the published blind-prediction bytes to the frozen marker."""

    shared_fields = (
        "sha256",
        "bytes",
        "rows",
        "schema_sha256",
        "semantic_sha256",
        "key_semantic_sha256",
    )
    if (
        marker_record.get("filename") != "blind_predictions.parquet"
        or output_record.get("path") != "blind_predictions.parquet"
        or any(
            output_record.get(field) != marker_record.get(field)
            for field in shared_fields
        )
    ):
        raise FinalEvaluationProtocolError(
            "Published blind predictions differ from PREDICTIONS_FROZEN."
        )


def _claim_tract_geometry_contract(
    claim: Mapping[str, Any],
    *,
    config: FinalEvaluationConfig,
) -> dict[str, Any]:
    request = claim.get("request")
    inventory = (
        request.get("landsat_inventory")
        if isinstance(request, Mapping)
        else None
    )
    locks = (
        inventory.get("locks")
        if isinstance(inventory, Mapping)
        else None
    )
    contract = {
        "tract_manifest_sha256": (
            locks.get("tract_manifest_sha256")
            if isinstance(locks, Mapping)
            else None
        ),
        "primary_tract_file_sha256": (
            locks.get("primary_tract_file_sha256")
            if isinstance(locks, Mapping)
            else None
        ),
        "tract_count": (
            inventory.get("tract_count")
            if isinstance(inventory, Mapping)
            else None
        ),
        "crs": (
            inventory.get("tract_crs")
            if isinstance(inventory, Mapping)
            else None
        ),
    }
    if (
        claim.get("claim_id") is None
        or _SHA256.fullmatch(str(contract["tract_manifest_sha256"])) is None
        or _SHA256.fullmatch(
            str(contract["primary_tract_file_sha256"])
        )
        is None
        or contract["tract_count"]
        != int(config.analysis["expected_tract_count"])
        or not isinstance(contract["crs"], str)
        or not contract["crs"]
    ):
        raise FinalEvaluationProtocolError(
            "Consumption claim lacks the authenticated tract geometry contract."
        )
    return contract


def _staged_output_records(
    config: FinalEvaluationConfig,
    *,
    expected_prediction_output: Mapping[str, Any],
    safe_count_summary: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    staging = config.paths["staging_root"]
    expected = set(EXPECTED_OUTPUT_FILES) - {"EVALUATION_COMMIT.json"}
    entries = list(staging.iterdir())
    if (
        {path.name for path in entries} != expected
        or any(path.is_symlink() or not path.is_file() for path in entries)
    ):
        raise FinalEvaluationProtocolError(
            "Staged final-evaluation output set is not exact regular files."
        )
    _assert_staged_output_cardinalities(config)
    records: dict[str, dict[str, Any]] = {}
    for filename, sort_by in OUTPUT_SEMANTIC_SORT_BY.items():
        path = staging / filename
        record = _file_record(path, relative_to=staging)
        frame = _read_output_table(path, filename=filename)
        record.update(
            {
                "rows": int(len(frame)),
                "columns": frame.columns.tolist(),
                "primary_key": list(OUTPUT_PRIMARY_KEYS[filename]),
                "schema_sha256": canonical_sha256(
                    [
                        (column, str(dtype))
                        for column, dtype in frame.dtypes.items()
                    ]
                ),
                "semantic_sort_by": list(sort_by),
                "semantic_sha256": canonical_frame_sha256(
                    frame,
                    sort_by=list(sort_by),
                ),
            }
        )
        if filename == "blind_predictions.parquet":
            record["key_semantic_sha256"] = canonical_frame_sha256(
                frame,
                sort_by=list(sort_by),
                columns=list(KEY_COLUMNS),
            )
        records[filename] = record
    _assert_prediction_output_binding(
        records["blind_predictions.parquet"],
        expected_prediction_output,
    )

    bootstrap_path = staging / "crossed_bootstrap.json"
    try:
        bootstrap = json.loads(bootstrap_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FinalEvaluationProtocolError(
            "Staged crossed-bootstrap JSON is invalid."
        ) from error
    records["crossed_bootstrap.json"] = {
        **_file_record(bootstrap_path, relative_to=staging),
        "semantic_sha256": canonical_sha256(bootstrap),
    }

    for filename, contract in FIGURE_OUTPUT_CONTRACTS.items():
        source_table = str(contract["source_table"])
        inspection = _inspect_figure_output(
            staging / filename,
            contract=contract,
        )
        geometry_contract: dict[str, Any] = {}
        if filename == "observed_predicted_residual_maps.pdf":
            tract_contract = safe_count_summary.get("tract_choropleth")
            if not isinstance(tract_contract, Mapping):
                raise FinalEvaluationProtocolError(
                    "Tract-map geometry contract is missing."
                )
            geometry_contract = {
                "tract_manifest_sha256": tract_contract.get(
                    "tract_manifest_sha256"
                ),
                "primary_tract_file_sha256": tract_contract.get(
                    "primary_tract_file_sha256"
                ),
                "tract_count": tract_contract.get("tract_count"),
                "crs": tract_contract.get("crs"),
            }
            if (
                _SHA256.fullmatch(
                    str(geometry_contract["tract_manifest_sha256"])
                )
                is None
                or _SHA256.fullmatch(
                    str(
                        geometry_contract[
                            "primary_tract_file_sha256"
                        ]
                    )
                )
                is None
                or geometry_contract["tract_count"]
                != int(config.analysis["expected_tract_count"])
                or not isinstance(geometry_contract["crs"], str)
                or not geometry_contract["crs"]
            ):
                raise FinalEvaluationProtocolError(
                    "Tract-map geometry contract is invalid."
                )
        figure_contract = {
            **contract,
            "source_table_semantic_sha256": records[source_table][
                "semantic_sha256"
            ],
            **inspection,
            **geometry_contract,
        }
        records[filename] = {
            **_file_record(staging / filename, relative_to=staging),
            **contract,
            "source_table_semantic_sha256": figure_contract[
                "source_table_semantic_sha256"
            ],
            **inspection,
            **geometry_contract,
            "figure_contract_sha256": canonical_sha256(figure_contract),
        }
    if set(records) != expected:
        raise FinalEvaluationProtocolError(
            "Staged final-evaluation provenance contract is incomplete."
        )
    return records


def _normalized_key_set(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
) -> set[tuple[str, ...]]:
    normalized = frame.loc[:, list(columns)].copy()
    if "target_date" in normalized:
        dates = pd.to_datetime(normalized["target_date"], errors="coerce")
        normalized["target_date"] = dates.dt.strftime("%Y-%m-%d").fillna(
            "<NA>"
        )
    for column in columns:
        if column != "target_date":
            normalized[column] = normalized[column].astype("string").fillna(
                "<NA>"
            )
    return {
        tuple(str(value) for value in row)
        for row in normalized.itertuples(index=False, name=None)
    }


def _assert_bootstrap_and_paired_output(
    bootstrap: Mapping[str, Any],
    *,
    paired: pd.DataFrame,
    evaluation: pd.DataFrame,
    model_metrics: pd.DataFrame,
    config: FinalEvaluationConfig,
) -> None:
    """Independently reconcile paired cells and the bootstrap point estimand."""

    from la_heat.final_evaluation_reporting import (
        REPORTING_ALGORITHM_VERSION,
    )

    reconciliation = bootstrap.get("point_estimate_reconciliation")
    expected_models = {
        "baseline": str(config.analysis["baseline_model_id"]),
        "primary": str(config.analysis["primary_model_id"]),
    }
    boolean_contract = {
        "complete_date_resampling": True,
        "complete_spatial_block_resampling": True,
        "date_and_block_draws_independent": True,
        "paired_models_share_every_cluster_draw": True,
        "random_row_sampling_used": False,
    }
    integer_fields = (
        "bootstrap_seed",
        "bootstrap_replicates",
        "date_block_cell_count",
        "independent_date_count",
        "independent_spatial_block_count",
        "tract_date_row_count",
        "zero_observation_sampled_date_draw_count",
    )
    numeric_fields = (
        "confidence_level",
        "baseline_point_mae_c",
        "target_model_point_mae_c",
        "absolute_mae_improvement_c",
        "absolute_mae_improvement_ci_lower_c",
        "absolute_mae_improvement_ci_upper_c",
        "relative_mae_improvement_fraction",
        "relative_mae_improvement_percent",
        "relative_mae_improvement_ci_lower_fraction",
        "relative_mae_improvement_ci_upper_fraction",
        "relative_mae_improvement_ci_lower_percent",
        "relative_mae_improvement_ci_upper_percent",
        "probability_improvement_gt_zero",
        "probability_relative_improvement_gt_10_percent",
    )
    if (
        set(bootstrap) != BOOTSTRAP_OUTPUT_KEYS
        or not isinstance(reconciliation, Mapping)
        or set(reconciliation) != BOOTSTRAP_RECONCILIATION_KEYS
        or bootstrap.get("schema_version") != 1
        or bootstrap.get("algorithm_version")
        != REPORTING_ALGORITHM_VERSION
        or bootstrap.get("state") != "complete"
        or bootstrap.get("final_test_year") != 2025
        or bootstrap.get("evaluation_cohort")
        != config.analysis["evaluation_cohort"]
        or bootstrap.get("baseline_model_id") != expected_models["baseline"]
        or bootstrap.get("primary_model_id") != expected_models["primary"]
        or bootstrap.get("bootstrap_method") != config.bootstrap["method"]
        or bootstrap.get("bootstrap_sampling_unit")
        != config.bootstrap["sampling_unit"]
        or bootstrap.get("bootstrap_seed") != config.bootstrap["seed"]
        or bootstrap.get("bootstrap_replicates")
        != config.bootstrap["replicates"]
        or not np.isclose(
            float(bootstrap.get("confidence_level", np.nan)),
            float(config.bootstrap["confidence_level"]),
            rtol=0.0,
            atol=0.0,
        )
        or any(
            bootstrap.get(key) is not value
            for key, value in boolean_contract.items()
        )
        or any(
            not isinstance(bootstrap.get(key), int)
            or isinstance(bootstrap.get(key), bool)
            or int(bootstrap[key]) < 0
            for key in integer_fields
        )
        or any(
            not isinstance(bootstrap.get(key), (int, float))
            or isinstance(bootstrap.get(key), bool)
            or not np.isfinite(float(bootstrap[key]))
            for key in numeric_fields
        )
    ):
        raise FinalEvaluationProtocolError(
            "Crossed-bootstrap exact structure/type contract failed."
        )

    expected_cells = (
        evaluation.assign(
            target_date=pd.to_datetime(
                evaluation["target_date"]
            ).dt.strftime("%Y-%m-%d"),
            spatial_block=evaluation["spatial_block"].astype(str),
        )
        .groupby(["target_date", "spatial_block"], sort=True)
        .agg(
            row_count=("tract_geoid", "size"),
            baseline_absolute_error_sum_c=("b1_absolute_error_c", "sum"),
            target_absolute_error_sum_c=("m2_absolute_error_c", "sum"),
        )
    )
    observed_cells = paired.assign(
        target_date=pd.to_datetime(paired["target_date"]).dt.strftime(
            "%Y-%m-%d"
        ),
        spatial_block=paired["spatial_block"].astype(str),
    ).set_index(["target_date", "spatial_block"])
    observed_cells = observed_cells.reindex(expected_cells.index)
    baseline_sums = expected_cells[
        "baseline_absolute_error_sum_c"
    ].to_numpy(dtype=float)
    primary_sums = expected_cells[
        "target_absolute_error_sum_c"
    ].to_numpy(dtype=float)
    cell_counts = expected_cells["row_count"].to_numpy(dtype=float)
    paired_ok = (
        not observed_cells.isna().all(axis=1).any()
        and observed_cells["baseline_model_id"]
        .astype(str)
        .eq(expected_models["baseline"])
        .all()
        and observed_cells["primary_model_id"]
        .astype(str)
        .eq(expected_models["primary"])
        .all()
        and np.array_equal(
            observed_cells["row_count"].to_numpy(dtype=float),
            cell_counts,
        )
        and np.allclose(
            observed_cells[
                "baseline_absolute_error_sum_c"
            ].to_numpy(dtype=float),
            baseline_sums,
            rtol=0.0,
            atol=1e-12,
        )
        and np.allclose(
            observed_cells[
                "target_absolute_error_sum_c"
            ].to_numpy(dtype=float),
            primary_sums,
            rtol=0.0,
            atol=1e-12,
        )
        and np.allclose(
            observed_cells["baseline_cell_mae_c"].to_numpy(dtype=float),
            baseline_sums / cell_counts,
            rtol=0.0,
            atol=1e-12,
        )
        and np.allclose(
            observed_cells["target_cell_mae_c"].to_numpy(dtype=float),
            primary_sums / cell_counts,
            rtol=0.0,
            atol=1e-12,
        )
        and np.allclose(
            observed_cells[
                "paired_absolute_mae_improvement_c"
            ].to_numpy(dtype=float),
            (baseline_sums - primary_sums) / cell_counts,
            rtol=0.0,
            atol=1e-12,
        )
    )
    if not paired_ok:
        raise FinalEvaluationProtocolError(
            "Paired date-block errors do not reproduce evaluation rows."
        )

    date_cells = expected_cells.groupby(level="target_date").sum()
    baseline_point = float(
        np.mean(
            date_cells["baseline_absolute_error_sum_c"]
            / date_cells["row_count"]
        )
    )
    primary_point = float(
        np.mean(
            date_cells["target_absolute_error_sum_c"]
            / date_cells["row_count"]
        )
    )
    absolute = baseline_point - primary_point
    relative = absolute / baseline_point
    metric_lookup = model_metrics.set_index("model_id")
    bootstrap_relations_ok = (
        int(bootstrap["date_block_cell_count"]) == len(expected_cells)
        and int(bootstrap["independent_date_count"])
        == evaluation["target_date"].nunique()
        and int(bootstrap["independent_spatial_block_count"])
        == evaluation["spatial_block"].nunique()
        and int(bootstrap["tract_date_row_count"]) == len(evaluation)
        and int(bootstrap["zero_observation_sampled_date_draw_count"]) >= 0
        and np.isclose(
            float(bootstrap["baseline_point_mae_c"]),
            baseline_point,
            rtol=0.0,
            atol=1e-12,
        )
        and np.isclose(
            float(bootstrap["target_model_point_mae_c"]),
            primary_point,
            rtol=0.0,
            atol=1e-12,
        )
        and np.isclose(
            float(bootstrap["absolute_mae_improvement_c"]),
            absolute,
            rtol=0.0,
            atol=1e-12,
        )
        and np.isclose(
            float(bootstrap["relative_mae_improvement_fraction"]),
            relative,
            rtol=0.0,
            atol=1e-12,
        )
        and np.isclose(
            float(bootstrap["relative_mae_improvement_percent"]),
            100.0 * relative,
            rtol=0.0,
            atol=1e-10,
        )
        and float(bootstrap["absolute_mae_improvement_ci_lower_c"])
        <= float(bootstrap["absolute_mae_improvement_ci_upper_c"])
        and float(bootstrap["relative_mae_improvement_ci_lower_fraction"])
        <= float(bootstrap["relative_mae_improvement_ci_upper_fraction"])
        and np.isclose(
            float(
                bootstrap[
                    "relative_mae_improvement_ci_lower_percent"
                ]
            ),
            100.0
            * float(
                bootstrap[
                    "relative_mae_improvement_ci_lower_fraction"
                ]
            ),
            rtol=0.0,
            atol=1e-10,
        )
        and np.isclose(
            float(
                bootstrap[
                    "relative_mae_improvement_ci_upper_percent"
                ]
            ),
            100.0
            * float(
                bootstrap[
                    "relative_mae_improvement_ci_upper_fraction"
                ]
            ),
            rtol=0.0,
            atol=1e-10,
        )
        and 0.0 <= float(bootstrap["probability_improvement_gt_zero"]) <= 1.0
        and 0.0
        <= float(
            bootstrap[
                "probability_relative_improvement_gt_10_percent"
            ]
        )
        <= 1.0
        and np.isclose(
            float(
                metric_lookup.loc[
                    expected_models["baseline"],
                    "equal_date_weighted_mae_c",
                ]
            ),
            baseline_point,
            rtol=0.0,
            atol=1e-12,
        )
        and np.isclose(
            float(
                metric_lookup.loc[
                    expected_models["primary"],
                    "equal_date_weighted_mae_c",
                ]
            ),
            primary_point,
            rtol=0.0,
            atol=1e-12,
        )
    )
    expected_reconciliation = {
        "primary_metric": config.analysis["primary_metric"],
        "baseline_model_id": expected_models["baseline"],
        "baseline_model_metric_mae_c": baseline_point,
        "baseline_bootstrap_point_mae_c": baseline_point,
        "primary_model_id": expected_models["primary"],
        "primary_model_metric_mae_c": primary_point,
        "primary_bootstrap_point_mae_c": primary_point,
        "relative_tolerance": 1e-12,
        "absolute_tolerance": 1e-12,
        "point_estimates_reconciled": True,
    }
    reconciliation_ok = all(
        (
            reconciliation.get(key) == value
            if not isinstance(value, float)
            else np.isclose(
                float(reconciliation.get(key, np.nan)),
                value,
                rtol=0.0,
                atol=1e-12,
            )
        )
        for key, value in expected_reconciliation.items()
    )
    if not bootstrap_relations_ok or not reconciliation_ok:
        raise FinalEvaluationProtocolError(
            "Crossed-bootstrap point/count/interval reconciliation failed."
        )


def _assert_replayed_report_outputs(
    *,
    evaluation: pd.DataFrame,
    model_metrics: pd.DataFrame,
    per_date: pd.DataFrame,
    paired: pd.DataFrame,
    bootstrap: Mapping[str, Any],
    gates: pd.DataFrame,
    hotspot_per_date: pd.DataFrame,
    hotspot_summary: pd.DataFrame,
    sensor_per_date: pd.DataFrame,
    sensor_summary: pd.DataFrame,
    sentinel: pd.DataFrame,
    config: FinalEvaluationConfig,
) -> None:
    """Replay every performance report derivable from evaluation rows."""

    from la_heat.final_evaluation_reporting import (
        _hotspot_diagnostics,
        _model_evaluations,
        _paired_bootstrap,
        _protocol_gates,
        _reconcile_bootstrap_point_estimates,
        _sensor_diagnostics,
        _sentinel_diagnostics,
    )

    settings = _reporting_settings(config)
    expected_model_metrics, expected_per_date_internal = (
        _model_evaluations(evaluation, settings)
    )
    expected_paired, expected_bootstrap = _paired_bootstrap(
        evaluation,
        settings,
    )
    _reconcile_bootstrap_point_estimates(
        expected_model_metrics,
        expected_bootstrap,
        settings,
    )
    expected_gates = _protocol_gates(
        expected_model_metrics,
        expected_bootstrap,
        settings,
    )
    expected_hotspot_per_date, expected_hotspot_summary = (
        _hotspot_diagnostics(evaluation, settings)
    )
    expected_sensor_per_date, expected_sensor_summary = _sensor_diagnostics(
        evaluation,
        expected_per_date_internal,
        settings,
    )
    expected_sentinel = _sentinel_diagnostics(evaluation, settings)
    observed_frames = {
        "model_metrics.csv": model_metrics,
        "per_date_metrics.csv": per_date,
        "paired_date_block_errors.csv": paired,
        "protocol_gates.csv": gates,
        "hotspot_per_date.csv": hotspot_per_date,
        "hotspot_summary.csv": hotspot_summary,
        "sensor_per_date_metrics.csv": sensor_per_date,
        "sensor_summary.csv": sensor_summary,
        "sentinel_stratum_summary.csv": sentinel,
    }
    expected_frames = {
        "model_metrics.csv": expected_model_metrics,
        "per_date_metrics.csv": expected_per_date_internal.loc[
            :, per_date.columns
        ],
        "paired_date_block_errors.csv": expected_paired,
        "protocol_gates.csv": expected_gates,
        "hotspot_per_date.csv": expected_hotspot_per_date,
        "hotspot_summary.csv": expected_hotspot_summary,
        "sensor_per_date_metrics.csv": expected_sensor_per_date,
        "sensor_summary.csv": expected_sensor_summary,
        "sentinel_stratum_summary.csv": expected_sentinel,
    }

    def normalized(frame: pd.DataFrame, *, filename: str) -> pd.DataFrame:
        result = frame.copy()
        if "target_date" in result:
            result["target_date"] = pd.to_datetime(
                result["target_date"],
                errors="raise",
            ).dt.strftime("%Y-%m-%d")
        return result.sort_values(
            list(OUTPUT_SEMANTIC_SORT_BY[filename]),
            kind="stable",
        ).reset_index(drop=True)

    try:
        for filename, observed in observed_frames.items():
            expected = expected_frames[filename].loc[:, observed.columns]
            pd.testing.assert_frame_equal(
                normalized(observed, filename=filename),
                normalized(expected, filename=filename),
                check_dtype=False,
                check_exact=False,
                rtol=1e-12,
                atol=1e-12,
            )
    except AssertionError as error:
        raise FinalEvaluationProtocolError(
            "Published performance tables do not replay from evaluation rows."
        ) from error
    if dict(bootstrap) != expected_bootstrap:
        raise FinalEvaluationProtocolError(
            "Crossed-bootstrap draws do not replay from the frozen seed."
        )


def _assert_staged_output_cardinalities(
    config: FinalEvaluationConfig,
    *,
    directory: Path | None = None,
) -> None:
    staging = config.paths["staging_root"] if directory is None else directory
    expected_keys = int(config.analysis["expected_key_count"])
    expected_dates = int(config.analysis["expected_inventory_overpass_count"])
    tables = {
        filename: _read_output_table(
            staging / filename,
            filename=filename,
        )
        for filename in OUTPUT_SEMANTIC_SORT_BY
    }
    blind = tables["blind_predictions.parquet"]
    target = tables["final_target_qa.parquet"]
    date_summary = tables["date_summary.parquet"]
    contributions = tables["scene_contributions.parquet"]
    evaluation = tables["evaluation_rows.parquet"]
    model_metrics = tables["model_metrics.csv"]
    per_date = tables["per_date_metrics.csv"]
    paired = tables["paired_date_block_errors.csv"]
    gates = tables["protocol_gates.csv"]
    hotspot_per_date = tables["hotspot_per_date.csv"]
    hotspot_summary = tables["hotspot_summary.csv"]
    sensor_per_date = tables["sensor_per_date_metrics.csv"]
    sensor_summary = tables["sensor_summary.csv"]
    sentinel = tables["sentinel_stratum_summary.csv"]
    qa = tables["qa_missingness_summary.csv"]
    tract_map = tables["tract_choropleth_summary.csv"]
    try:
        bootstrap = json.loads(
            (staging / "crossed_bootstrap.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise FinalEvaluationProtocolError(
            "Crossed-bootstrap output is not valid JSON."
        ) from error

    usable_mask = target["date_usable"].astype(bool) & target[
        "target_available"
    ].astype(bool)
    usable_rows = int(usable_mask.sum())
    usable_dates = int(target.loc[usable_mask, "target_date"].nunique())
    hotspot_dates = int(
        evaluation.loc[
            evaluation["relative_endpoint_coverage_pass"].astype(bool),
            "target_date",
        ].nunique()
    )
    observed_sensor_count = int(evaluation["sensor"].nunique())
    observed_block_count = int(evaluation["spatial_block"].nunique())
    target_keys = _normalized_key_set(target, KEY_COLUMNS)
    blind_keys = _normalized_key_set(blind, KEY_COLUMNS)
    expected_evaluation_keys = _normalized_key_set(
        target.loc[usable_mask],
        KEY_COLUMNS,
    )
    evaluation_keys = _normalized_key_set(evaluation, KEY_COLUMNS)
    inventory_dates = {
        value[0]
        for value in _normalized_key_set(target, ("target_date",))
    }
    usable_date_keys = {
        value[0]
        for value in _normalized_key_set(
            target.loc[usable_mask],
            ("target_date",),
        )
    }
    blind_geoids = set(blind["tract_geoid"].astype(str))
    target_geoids = set(target["tract_geoid"].astype(str))
    map_geoids = set(tract_map["tract_geoid"].astype(str))
    target_block_counts = target.groupby(
        "tract_geoid",
        observed=True,
    )["spatial_block"].nunique(dropna=False)
    target_blocks = (
        target.loc[:, ["tract_geoid", "spatial_block"]]
        .drop_duplicates()
        .assign(
            tract_geoid=lambda frame: frame["tract_geoid"].astype(str),
            spatial_block=lambda frame: frame["spatial_block"].astype(str),
        )
        .set_index("tract_geoid")["spatial_block"]
        .to_dict()
    )
    map_blocks = (
        tract_map.assign(
            tract_geoid=lambda frame: frame["tract_geoid"].astype(str),
            spatial_block=lambda frame: frame["spatial_block"].astype(str),
        )
        .set_index("tract_geoid")["spatial_block"]
        .to_dict()
    )
    support_count = pd.to_numeric(
        tract_map["evaluated_date_count"],
        errors="coerce",
    ).to_numpy(dtype=float)
    support_fraction = pd.to_numeric(
        tract_map["evaluated_date_fraction"],
        errors="coerce",
    ).to_numpy(dtype=float)
    expected_support = (
        evaluation.groupby("tract_geoid", observed=True)["target_date"]
        .nunique()
        .reindex(tract_map["tract_geoid"].astype(str), fill_value=0)
        .to_numpy(dtype=float)
    )
    expected_map_metrics = (
        evaluation.assign(
            tract_geoid=evaluation["tract_geoid"].astype(str)
        )
        .groupby("tract_geoid", observed=True)
        .agg(
            observed_lst_c=("y_true", "mean"),
            b1_predicted_lst_c=("y_pred_b1", "mean"),
            m2_predicted_lst_c=("y_pred_m2", "mean"),
            b1_residual_c=("b1_error_c", "mean"),
            m2_residual_c=("m2_error_c", "mean"),
            b1_mean_absolute_error_c=("b1_absolute_error_c", "mean"),
            m2_mean_absolute_error_c=("m2_absolute_error_c", "mean"),
        )
        .reindex(tract_map["tract_geoid"].astype(str))
    )
    map_metric_columns = expected_map_metrics.columns.tolist()
    map_metrics_ok = all(
        np.allclose(
            pd.to_numeric(tract_map[column], errors="coerce").to_numpy(
                dtype=float
            ),
            expected_map_metrics[column].to_numpy(dtype=float),
            rtol=0.0,
            atol=1e-12,
            equal_nan=True,
        )
        for column in map_metric_columns
    )
    support_ok = (
        usable_dates > 0
        and np.isfinite(support_count).all()
        and np.equal(support_count, np.floor(support_count)).all()
        and (support_count >= 0).all()
        and (support_count <= usable_dates).all()
        and np.isfinite(support_fraction).all()
        and (support_fraction >= 0.0).all()
        and (support_fraction <= 1.0).all()
        and np.allclose(
            support_fraction,
            support_count / usable_dates,
            rtol=0.0,
            atol=1e-12,
        )
        and np.array_equal(support_count, expected_support)
        and map_metrics_ok
    )
    expected_model_ids = {
        str(config.analysis["baseline_model_id"]),
        str(config.analysis["primary_model_id"]),
    }
    expected_model_keys = {(model_id,) for model_id in expected_model_ids}
    expected_per_date_keys = {
        (date, model_id)
        for date in usable_date_keys
        for model_id in expected_model_ids
    }
    expected_paired_keys = _normalized_key_set(
        evaluation,
        ("target_date", "spatial_block"),
    )
    date_sensor = evaluation.loc[:, ["target_date", "sensor"]].drop_duplicates()
    date_sensor_dates = pd.to_datetime(
        date_sensor["target_date"],
        errors="coerce",
    ).dt.strftime("%Y-%m-%d")
    date_sensor_pairs = set(
        zip(
            date_sensor_dates.astype(str),
            date_sensor["sensor"].astype(str),
            strict=True,
        )
    )
    expected_sensor_per_date_keys = {
        (sensor, date, model_id)
        for date, sensor in date_sensor_pairs
        for model_id in expected_model_ids
    }
    sensors = {sensor for _, sensor in date_sensor_pairs}
    expected_sensor_summary_keys = {
        (sensor, model_id)
        for sensor in sensors
        for model_id in expected_model_ids
    }
    hotspot_date_keys = {
        value[0]
        for value in _normalized_key_set(
            evaluation.loc[
                evaluation["relative_endpoint_coverage_pass"].astype(bool)
            ],
            ("target_date",),
        )
    }
    expected_hotspot_keys = {
        (date, model_id)
        for date in hotspot_date_keys
        for model_id in expected_model_ids
    }
    expected_sentinel_keys = {
        (stratum, model_id)
        for stratum in (
            "sentinel_complete",
            "sentinel_all_five_missing",
        )
        for model_id in expected_model_ids
    }
    expected_qa_keys = {
        ("overall", "<NA>"),
        *(("date", date) for date in inventory_dates),
    }
    expected_gate_keys = {
        ("median_per_date_spearman",),
        ("point_relative_mae_improvement",),
        ("uncertainty_supports_positive_improvement",),
        ("uncertainty_supports_full_threshold_improvement",),
    }
    exact_keys_ok = (
        blind_keys == target_keys
        and evaluation_keys == expected_evaluation_keys
        and _normalized_key_set(date_summary, ("target_date",))
        == {(date,) for date in inventory_dates}
        and _normalized_key_set(model_metrics, ("model_id",))
        == expected_model_keys
        and _normalized_key_set(
            per_date,
            ("target_date", "model_id"),
        )
        == expected_per_date_keys
        and _normalized_key_set(
            paired,
            ("target_date", "spatial_block"),
        )
        == expected_paired_keys
        and _normalized_key_set(gates, ("gate_id",))
        == expected_gate_keys
        and _normalized_key_set(
            hotspot_per_date,
            ("target_date", "model_id"),
        )
        == expected_hotspot_keys
        and _normalized_key_set(hotspot_summary, ("model_id",))
        == expected_model_keys
        and _normalized_key_set(
            sensor_per_date,
            ("sensor", "target_date", "model_id"),
        )
        == expected_sensor_per_date_keys
        and _normalized_key_set(
            sensor_summary,
            ("sensor", "model_id"),
        )
        == expected_sensor_summary_keys
        and _normalized_key_set(
            sentinel,
            ("sentinel_stratum", "model_id"),
        )
        == expected_sentinel_keys
        and _normalized_key_set(
            qa,
            ("summary_level", "target_date"),
        )
        == expected_qa_keys
    )
    evaluation_numeric = evaluation.loc[
        :,
        [
            "y_true",
            "y_pred_b1",
            "y_pred_m2",
            "b1_error_c",
            "m2_error_c",
            "b1_absolute_error_c",
            "m2_absolute_error_c",
        ],
    ].to_numpy(dtype=float)
    evaluation_arithmetic_ok = (
        np.isfinite(evaluation_numeric).all()
        and np.allclose(
            evaluation["b1_error_c"].to_numpy(dtype=float),
            evaluation["y_pred_b1"].to_numpy(dtype=float)
            - evaluation["y_true"].to_numpy(dtype=float),
            rtol=0.0,
            atol=1e-12,
        )
        and np.allclose(
            evaluation["m2_error_c"].to_numpy(dtype=float),
            evaluation["y_pred_m2"].to_numpy(dtype=float)
            - evaluation["y_true"].to_numpy(dtype=float),
            rtol=0.0,
            atol=1e-12,
        )
        and np.allclose(
            evaluation["b1_absolute_error_c"].to_numpy(dtype=float),
            np.abs(evaluation["b1_error_c"].to_numpy(dtype=float)),
            rtol=0.0,
            atol=1e-12,
        )
        and np.allclose(
            evaluation["m2_absolute_error_c"].to_numpy(dtype=float),
            np.abs(evaluation["m2_error_c"].to_numpy(dtype=float)),
            rtol=0.0,
            atol=1e-12,
        )
    )
    _assert_bootstrap_and_paired_output(
        bootstrap,
        paired=paired,
        evaluation=evaluation,
        model_metrics=model_metrics,
        config=config,
    )
    _assert_replayed_report_outputs(
        evaluation=evaluation,
        model_metrics=model_metrics,
        per_date=per_date,
        paired=paired,
        bootstrap=bootstrap,
        gates=gates,
        hotspot_per_date=hotspot_per_date,
        hotspot_summary=hotspot_summary,
        sensor_per_date=sensor_per_date,
        sensor_summary=sensor_summary,
        sentinel=sentinel,
        config=config,
    )
    cardinality_ok = (
        len(blind) == expected_keys
        and len(target) == expected_keys
        and len(date_summary) == expected_dates
        and date_summary["target_date"].nunique() == expected_dates
        and not contributions.empty
        and len(evaluation) == usable_rows
        and len(model_metrics) == 2
        and set(model_metrics["model_id"].astype(str)) == expected_model_ids
        and len(per_date) == 2 * usable_dates
        and len(sensor_per_date) == 2 * usable_dates
        and len(sensor_summary) == 2 * observed_sensor_count
        and not sensor_summary.duplicated(["sensor", "model_id"]).any()
        and len(hotspot_per_date) == 2 * hotspot_dates
        and len(hotspot_summary) == 2
        and len(gates) == 4
        and len(sentinel) == 4
        and len(qa) == expected_dates + 1
        and len(tract_map) == int(config.analysis["expected_tract_count"])
        and tract_map["tract_geoid"].nunique()
        == int(config.analysis["expected_tract_count"])
        and blind_geoids == target_geoids == map_geoids
        and len(blind_geoids) == int(config.analysis["expected_tract_count"])
        and target_block_counts.eq(1).all()
        and target_blocks == map_blocks
        and support_ok
        and exact_keys_ok
        and evaluation_arithmetic_ok
        and int(paired["row_count"].sum()) == usable_rows
        and not paired.duplicated(["target_date", "spatial_block"]).any()
        and int(bootstrap.get("tract_date_row_count", -1)) == usable_rows
        and int(bootstrap.get("independent_date_count", -1)) == usable_dates
        and int(bootstrap.get("independent_spatial_block_count", -1))
        == observed_block_count
    )
    if not cardinality_ok:
        raise FinalEvaluationProtocolError(
            "Staged final-evaluation table cardinalities violate the frozen contract."
        )


def _authenticate_output_commit(
    directory: Path,
    *,
    config: FinalEvaluationConfig,
    claim: Mapping[str, Any],
    claim_id: str,
    claim_commit: str,
    expected_prediction_output: Mapping[str, Any],
    deep_structured: bool = False,
) -> tuple[dict[str, Any], str]:
    column_contracts = _output_table_column_contracts()
    expected_geometry_contract = _claim_tract_geometry_contract(
        claim,
        config=config,
    )
    commit_path = directory / "EVALUATION_COMMIT.json"
    payload, commit = _read_committed(
        commit_path,
        label="final-evaluation output commit",
    )
    outputs = payload.get("output_files")
    safe_count_summary = payload.get("safe_count_summary")
    if (
        payload.get("schema_version") != 1
        or payload.get("algorithm_version") != COMPLETION_ALGORITHM_VERSION
        or payload.get("state") != "complete_staged_evaluation"
        or payload.get("claim_id") != claim_id
        or claim.get("claim_id") != claim_id
        or payload.get("claim_commit_sha256") != claim_commit
        or payload.get("final_test_year") != 2025
        or not isinstance(outputs, Mapping)
        or set(outputs) != set(EXPECTED_OUTPUT_FILES) - {"EVALUATION_COMMIT.json"}
        or not isinstance(safe_count_summary, Mapping)
    ):
        raise FinalEvaluationProtocolError(
            "Final-evaluation output commit contract is invalid."
        )
    entries = list(directory.iterdir())
    if (
        {path.name for path in entries} != set(EXPECTED_OUTPUT_FILES)
        or any(path.is_symlink() or not path.is_file() for path in entries)
    ):
        raise FinalEvaluationProtocolError(
            "Published final-evaluation set is not exact regular files."
        )
    blind_record = outputs.get("blind_predictions.parquet")
    if not isinstance(blind_record, Mapping):
        raise FinalEvaluationProtocolError(
            "Final output lacks its blind-prediction record."
        )
    _assert_prediction_output_binding(
        blind_record,
        expected_prediction_output,
    )
    for filename, record in outputs.items():
        if (
            not isinstance(record, Mapping)
            or record.get("path") != filename
        ):
            raise FinalEvaluationProtocolError(
                f"Final output record is invalid: {filename}"
            )
        path = directory / filename
        _verify_file_record(
            path,
            record,
            label=f"Final output {filename}",
        )
        if filename in OUTPUT_SEMANTIC_SORT_BY:
            sort_by = OUTPUT_SEMANTIC_SORT_BY[filename]
            if (
                not isinstance(record.get("rows"), int)
                or record.get("columns")
                != list(column_contracts[filename])
                or record.get("primary_key")
                != list(OUTPUT_PRIMARY_KEYS[filename])
                or record.get("semantic_sort_by") != list(sort_by)
                or not isinstance(record.get("schema_sha256"), str)
                or _SHA256.fullmatch(str(record.get("schema_sha256"))) is None
                or not isinstance(record.get("semantic_sha256"), str)
                or _SHA256.fullmatch(str(record.get("semantic_sha256"))) is None
                or (
                    filename == "blind_predictions.parquet"
                    and _SHA256.fullmatch(
                        str(record.get("key_semantic_sha256"))
                    )
                    is None
                )
            ):
                raise FinalEvaluationProtocolError(
                    f"Final output table provenance is invalid: {filename}"
                )
            if not deep_structured:
                continue
            frame = _read_output_table(path, filename=filename)
            observed = {
                "rows": int(len(frame)),
                "columns": frame.columns.tolist(),
                "schema_sha256": canonical_sha256(
                    [
                        (column, str(dtype))
                        for column, dtype in frame.dtypes.items()
                    ]
                ),
                "semantic_sha256": canonical_frame_sha256(
                    frame,
                    sort_by=list(sort_by),
                ),
            }
            if any(record.get(key) != value for key, value in observed.items()):
                raise FinalEvaluationProtocolError(
                    f"Final output semantic/schema lock failed: {filename}"
                )
        elif filename == "crossed_bootstrap.json":
            if (
                not isinstance(record.get("semantic_sha256"), str)
                or _SHA256.fullmatch(str(record.get("semantic_sha256"))) is None
            ):
                raise FinalEvaluationProtocolError(
                    "Final crossed-bootstrap semantic lock is invalid."
                )
            if not deep_structured:
                continue
            try:
                structured = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise FinalEvaluationProtocolError(
                    f"Final JSON output is invalid: {filename}"
                ) from error
            if record.get("semantic_sha256") != canonical_sha256(structured):
                raise FinalEvaluationProtocolError(
                    f"Final output semantic lock failed: {filename}"
                )
        elif filename in FIGURE_OUTPUT_CONTRACTS:
            contract = FIGURE_OUTPUT_CONTRACTS[filename]
            source_table = str(contract["source_table"])
            inspection = _inspect_figure_output(path, contract=contract)
            geometry_contract: dict[str, Any] = {}
            if filename == "observed_predicted_residual_maps.pdf":
                tract_contract = safe_count_summary.get(
                    "tract_choropleth"
                )
                if not isinstance(tract_contract, Mapping):
                    raise FinalEvaluationProtocolError(
                        "Final map geometry summary is missing."
                    )
                geometry_contract = {
                    "tract_manifest_sha256": tract_contract.get(
                        "tract_manifest_sha256"
                    ),
                    "primary_tract_file_sha256": tract_contract.get(
                        "primary_tract_file_sha256"
                    ),
                    "tract_count": tract_contract.get("tract_count"),
                    "crs": tract_contract.get("crs"),
                }
                if (
                    _SHA256.fullmatch(
                        str(
                            geometry_contract[
                                "tract_manifest_sha256"
                            ]
                        )
                    )
                    is None
                    or _SHA256.fullmatch(
                        str(
                            geometry_contract[
                                "primary_tract_file_sha256"
                            ]
                        )
                    )
                    is None
                    or geometry_contract["tract_count"]
                    != int(config.analysis["expected_tract_count"])
                    or not isinstance(geometry_contract["crs"], str)
                    or not geometry_contract["crs"]
                ):
                    raise FinalEvaluationProtocolError(
                        "Final map geometry summary is invalid."
                    )
                if geometry_contract != expected_geometry_contract:
                    raise FinalEvaluationProtocolError(
                        "Final map geometry differs from the consumption claim."
                    )
            expected_contract = {
                **contract,
                "source_table_semantic_sha256": outputs[source_table][
                    "semantic_sha256"
                ],
                **inspection,
                **geometry_contract,
            }
            if (
                any(record.get(key) != value for key, value in contract.items())
                or record.get("source_table_semantic_sha256")
                != expected_contract["source_table_semantic_sha256"]
                or any(
                    record.get(key) != value
                    for key, value in inspection.items()
                )
                or any(
                    record.get(key) != value
                    for key, value in geometry_contract.items()
                )
                or record.get("figure_contract_sha256")
                != canonical_sha256(expected_contract)
            ):
                raise FinalEvaluationProtocolError(
                    f"Final figure provenance contract failed: {filename}"
                )
        else:
            raise FinalEvaluationProtocolError(
                f"Final output has no provenance contract: {filename}"
            )
    if deep_structured:
        _assert_replayed_figure_outputs(
            directory,
            config=config,
            claim=claim,
            output_records=outputs,
        )
        _assert_staged_output_cardinalities(
            config,
            directory=directory,
        )
    return payload, commit


def _publish_completion(
    config: FinalEvaluationConfig,
    *,
    claim: Mapping[str, Any],
    claim_commit: str,
    output_commit: Mapping[str, Any],
    output_commit_sha256: str,
) -> dict[str, Any]:
    prediction_marker, prediction_commit, _ = (
        _authenticate_prediction_marker(
            config,
            claim=claim,
            claim_commit=claim_commit,
            read_frame=False,
        )
    )
    _, values_commit = _authenticate_values_opened(
        config,
        claim_id=str(claim["claim_id"]),
        claim_commit=claim_commit,
        predictions_commit=prediction_commit,
    )
    observed_output, observed_output_commit = _authenticate_output_commit(
        config.paths["final_output_directory"],
        config=config,
        claim=claim,
        claim_id=str(claim["claim_id"]),
        claim_commit=claim_commit,
        expected_prediction_output=prediction_marker["output"],
    )
    if (
        observed_output != output_commit
        or observed_output_commit != output_commit_sha256
        or observed_output.get("predictions_commit_sha256")
        != prediction_commit
        or observed_output.get("values_opened_commit_sha256")
        != values_commit
    ):
        raise FinalEvaluationProtocolError(
            "Final output changed before completion publication."
        )
    output_commit_path = (
        config.paths["final_output_directory"] / "EVALUATION_COMMIT.json"
    )
    payload = _committed_payload(
        {
            "schema_version": 1,
            "algorithm_version": COMPLETION_ALGORITHM_VERSION,
            "state": "complete_one_time_final_evaluation",
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "final_test_year": 2025,
            "claim_id": claim["claim_id"],
            "claim_commit_sha256": claim_commit,
            "values_opened_commit_sha256": output_commit[
                "values_opened_commit_sha256"
            ],
            "predictions_commit_sha256": output_commit[
                "predictions_commit_sha256"
            ],
            "output_directory": config.paths["final_output_directory"]
            .relative_to(config.root)
            .as_posix(),
            "output_commit_file_sha256": sha256_file(output_commit_path),
            "output_commit_sha256": output_commit_sha256,
            "exact_output_files": list(EXPECTED_OUTPUT_FILES),
            "completed": True,
        }
    )
    _exclusive_json(
        payload,
        config.paths["complete"],
        label="EVALUATION_COMPLETE.json",
    )
    return payload


def authenticate_completed_final_evaluation(
    config: FinalEvaluationConfig,
) -> dict[str, Any]:
    """Authenticate an existing completion without reopening target tables."""

    complete, _ = _read_committed(
        config.paths["complete"],
        label="final-evaluation completion",
    )
    claim, claim_commit = _read_committed(
        config.paths["claim"],
        label="final-evaluation consumption claim",
    )
    if (
        complete.get("schema_version") != 1
        or complete.get("algorithm_version") != COMPLETION_ALGORITHM_VERSION
        or complete.get("state") != "complete_one_time_final_evaluation"
        or complete.get("completed") is not True
        or complete.get("claim_id") != claim.get("claim_id")
        or complete.get("claim_commit_sha256") != claim_commit
    ):
        raise FinalEvaluationProtocolError(
            "Final-evaluation completion marker is invalid."
        )
    prediction_marker, prediction_commit, _ = _authenticate_prediction_marker(
        config,
        claim=claim,
        claim_commit=claim_commit,
        read_frame=False,
    )
    _, values_commit = _authenticate_values_opened(
        config,
        claim_id=str(claim["claim_id"]),
        claim_commit=claim_commit,
        predictions_commit=prediction_commit,
    )
    output, output_commit = _authenticate_output_commit(
        config.paths["final_output_directory"],
        config=config,
        claim=claim,
        claim_id=str(claim["claim_id"]),
        claim_commit=claim_commit,
        expected_prediction_output=prediction_marker["output"],
    )
    output_path = (
        config.paths["final_output_directory"] / "EVALUATION_COMMIT.json"
    )
    if (
        complete.get("output_commit_sha256") != output_commit
        or complete.get("output_commit_file_sha256") != sha256_file(output_path)
        or complete.get("values_opened_commit_sha256")
        != values_commit
        or complete.get("predictions_commit_sha256")
        != prediction_commit
        or output.get("values_opened_commit_sha256") != values_commit
        or output.get("predictions_commit_sha256") != prediction_commit
    ):
        raise FinalEvaluationProtocolError(
            "Completion and final output commitments disagree."
        )
    return complete


def _recover_promoted_output(
    config: FinalEvaluationConfig,
    *,
    claim: Mapping[str, Any],
    claim_commit: str,
) -> dict[str, Any]:
    if config.paths["staging_root"].exists():
        raise FinalEvaluationProtocolError(
            "Both staging and final evaluation directories exist."
        )
    prediction_marker, prediction_commit, _ = (
        _authenticate_prediction_marker(
            config,
            claim=claim,
            claim_commit=claim_commit,
            read_frame=False,
        )
    )
    _, values_commit = _authenticate_values_opened(
        config,
        claim_id=str(claim["claim_id"]),
        claim_commit=claim_commit,
        predictions_commit=prediction_commit,
    )
    output, output_commit = _authenticate_output_commit(
        config.paths["final_output_directory"],
        config=config,
        claim=claim,
        claim_id=str(claim["claim_id"]),
        claim_commit=claim_commit,
        expected_prediction_output=prediction_marker["output"],
        deep_structured=True,
    )
    if (
        output.get("predictions_commit_sha256") != prediction_commit
        or output.get("values_opened_commit_sha256") != values_commit
        or prediction_marker.get("claim_id") != claim.get("claim_id")
    ):
        raise FinalEvaluationProtocolError(
            "Promoted output does not bind the current prediction/value markers."
        )
    return _publish_completion(
        config,
        claim=claim,
        claim_commit=claim_commit,
        output_commit=output,
        output_commit_sha256=output_commit,
    )


def _recover_committed_staging(
    config: FinalEvaluationConfig,
    *,
    claim: Mapping[str, Any],
    claim_commit: str,
) -> dict[str, Any]:
    """Promote an already-committed staging directory without recomputation."""

    staging = config.paths["staging_root"]
    if not (staging / "EVALUATION_COMMIT.json").is_file():
        raise FinalEvaluationProtocolError(
            "Committed-staging recovery requires EVALUATION_COMMIT.json."
        )
    prediction_marker, prediction_commit, _ = (
        _authenticate_prediction_marker(
            config,
            claim=claim,
            claim_commit=claim_commit,
            read_frame=False,
        )
    )
    _, values_commit = _authenticate_values_opened(
        config,
        claim_id=str(claim["claim_id"]),
        claim_commit=claim_commit,
        predictions_commit=prediction_commit,
    )
    output, output_commit = _authenticate_output_commit(
        staging,
        config=config,
        claim=claim,
        claim_id=str(claim["claim_id"]),
        claim_commit=claim_commit,
        expected_prediction_output=prediction_marker["output"],
        deep_structured=True,
    )
    if (
        output.get("predictions_commit_sha256") != prediction_commit
        or output.get("values_opened_commit_sha256") != values_commit
        or prediction_marker.get("claim_id") != claim.get("claim_id")
    ):
        raise FinalEvaluationProtocolError(
            "Committed staging does not bind the current state markers."
        )
    final_directory = config.paths["final_output_directory"]
    if final_directory.exists():
        raise FinalEvaluationProtocolError(
            "Final output exists during committed-staging recovery."
        )
    staging.replace(final_directory)
    output, output_commit = _authenticate_output_commit(
        final_directory,
        config=config,
        claim=claim,
        claim_id=str(claim["claim_id"]),
        claim_commit=claim_commit,
        expected_prediction_output=prediction_marker["output"],
    )
    return _publish_completion(
        config,
        claim=claim,
        claim_commit=claim_commit,
        output_commit=output,
        output_commit_sha256=output_commit,
    )


def execute_locked_final_evaluation(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
) -> dict[str, Any]:
    """Run or same-claim resume the one-time final evaluation.

    The returned mapping contains only protocol state and count metadata.  Target
    values and performance metrics are never printed by this function.
    """

    config = load_final_evaluation_config(config_path)
    with FinalTestStateLock(
        config.root / DEFAULT_FINAL_TEST_STATE_LOCK_PATH
    ):
        _clean_owned_marker_partials(config)
        if config.paths["complete"].exists():
            return authenticate_completed_final_evaluation(config)

        readiness, readiness_commit = (
            authenticate_final_evaluation_readiness(config)
        )
        authorization, authorization_commit = _authenticate_authorization(
            config,
            readiness,
            readiness_commit,
        )
        unlock = _verify_unlock_transition(config, authorization)
        runtime_sha256, runtime_payload = code_runtime_fingerprint(
            project_root=config.root,
            relative_paths=PIPELINE_FILES,
            algorithm_version=ALGORITHM_VERSION,
        )
        if (
            runtime_sha256 != readiness["request"]["pipeline_sha256"]
            or runtime_payload != readiness["request"]["pipeline"]
            or _extended_runtime_record()
            != readiness["request"]["extended_runtime"]
        ):
            raise FinalEvaluationProtocolError(
                "Evaluation code/runtime changed after readiness."
            )
        claim_request = _claim_request(
            config,
            readiness=readiness,
            readiness_commit=readiness_commit,
            authorization_commit=authorization_commit,
            unlock=unlock,
        )
        claim, claim_commit = _create_or_authenticate_claim(
            config,
            request=claim_request,
        )

        if config.paths["final_output_directory"].exists():
            return _recover_promoted_output(
                config,
                claim=claim,
                claim_commit=claim_commit,
            )
        if (
            config.paths["staging_root"] / "EVALUATION_COMMIT.json"
        ).is_file():
            return _recover_committed_staging(
                config,
                claim=claim,
                claim_commit=claim_commit,
            )

        formal, current_formal_record = _formal_model_record(
            config,
            head=str(unlock["unlocked_git_commit"]),
        )
        if current_formal_record != readiness["formal_model_lock"]:
            raise FinalEvaluationProtocolError(
                "Formal model lock changed after readiness."
            )

        inventory, inventory_record = _default_inventory_authenticator(config)
        if inventory_record != readiness["request"]["landsat_inventory"]:
            raise FinalEvaluationProtocolError(
                "Landsat inventory changed after readiness."
            )
        predictions = _freeze_blind_predictions(
            config,
            readiness=readiness,
            formal=formal,
            claim=claim,
            claim_commit=claim_commit,
        )
        predictors = _load_predictors_after_claim(
            config,
            readiness=readiness,
            formal=formal,
        )
        callback = _values_opened_callback(
            config,
            claim=claim,
            claim_commit=claim_commit,
            predictions=predictions,
            readiness=readiness,
            formal=formal,
        )

        from la_heat.final_evaluation_targets import (
            build_final_targets_transaction,
        )

        research = load_config(config.paths["research_config"])
        target_artifacts = build_final_targets_transaction(
            inventory=inventory,
            config=research,
            expected_target_config_sha256=config.locks[
                "target_config_semantic_sha256"
            ],
            claim_id=str(claim["claim_id"]),
            staging_directory=config.paths["target_cache_directory"],
            values_opened_callback=callback,
        )
        _, values_commit = _authenticate_values_opened(
            config,
            claim_id=str(claim["claim_id"]),
            claim_commit=claim_commit,
            predictions_commit=str(
                predictions.marker["commit_sha256"]
            ),
        )
        safe_summary = _write_target_and_reports(
            config,
            target_artifacts=target_artifacts,
            predictions=predictions,
            predictors=predictors,
            inventory=inventory,
        )
        _clean_owned_staging_partials(config)
        replayed_marker, replayed_commit, _ = (
            _authenticate_and_replay_blind_predictions(
                config,
                readiness=readiness,
                formal=formal,
                claim=claim,
                claim_commit=claim_commit,
            )
        )
        _, values_commit = _authenticate_values_opened(
            config,
            claim_id=str(claim["claim_id"]),
            claim_commit=claim_commit,
            predictions_commit=replayed_commit,
        )
        if replayed_commit != predictions.marker["commit_sha256"]:
            raise FinalEvaluationProtocolError(
                "Blind-prediction commitment changed before output publication."
            )

        output_commit_path = (
            config.paths["staging_root"] / "EVALUATION_COMMIT.json"
        )
        if output_commit_path.exists():
            output_payload, output_commit = _authenticate_output_commit(
                config.paths["staging_root"],
                config=config,
                claim=claim,
                claim_id=str(claim["claim_id"]),
                claim_commit=claim_commit,
                expected_prediction_output=replayed_marker["output"],
                deep_structured=True,
            )
        else:
            records = _staged_output_records(
                config,
                expected_prediction_output=replayed_marker["output"],
                safe_count_summary=safe_summary,
            )
            output_payload = _committed_payload(
                {
                    "schema_version": 1,
                    "algorithm_version": COMPLETION_ALGORITHM_VERSION,
                    "state": "complete_staged_evaluation",
                    "generated_at_utc": datetime.now(UTC).isoformat(),
                    "final_test_year": 2025,
                    "claim_id": claim["claim_id"],
                    "claim_commit_sha256": claim_commit,
                    "predictions_commit_sha256": predictions.marker[
                        "commit_sha256"
                    ],
                    "values_opened_commit_sha256": values_commit,
                    "readiness_commit_sha256": readiness_commit,
                    "authorization_commit_sha256": authorization_commit,
                    "output_files": records,
                    "safe_count_summary": safe_summary,
                    "contains_target_values": True,
                    "models_refit_or_tuned": False,
                }
            )
            _exclusive_json(
                output_payload,
                output_commit_path,
                label="EVALUATION_COMMIT.json",
            )
            output_payload, output_commit = _authenticate_output_commit(
                config.paths["staging_root"],
                config=config,
                claim=claim,
                claim_id=str(claim["claim_id"]),
                claim_commit=claim_commit,
                expected_prediction_output=replayed_marker["output"],
                deep_structured=True,
            )

        final_directory = config.paths["final_output_directory"]
        if final_directory.exists():
            raise FinalEvaluationProtocolError(
                "Final evaluation output appeared during staging."
            )
        config.paths["staging_root"].replace(final_directory)
        output_payload, output_commit = _authenticate_output_commit(
            final_directory,
            config=config,
            claim=claim,
            claim_id=str(claim["claim_id"]),
            claim_commit=claim_commit,
            expected_prediction_output=replayed_marker["output"],
        )
        return _publish_completion(
            config,
            claim=claim,
            claim_commit=claim_commit,
            output_commit=output_payload,
            output_commit_sha256=output_commit,
        )
