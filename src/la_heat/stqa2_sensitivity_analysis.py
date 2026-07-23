"""Authenticate and analyze the strict pixel-level ST_QA <= 2 K sensitivity."""

from __future__ import annotations

import copy
import json
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from la_heat.config import load_config
from la_heat.model_result_analysis import (
    ModelResultAnalysisError,
    aggregate_paired_date_block_errors,
    authenticate_model_results,
    crossed_date_spatial_block_bootstrap,
    load_result_analysis_config,
)
from la_heat.provenance import (
    atomic_csv,
    atomic_json,
    canonical_sha256,
    code_runtime_fingerprint,
    parquet_file_record,
    sha256_file,
)
from la_heat.stage_config import target_config_sha256

ALGORITHM_VERSION: Final = "stqa2-pixel-label-sensitivity-v2"
DATE_RETENTION_FILENAME: Final = "stqa2_date_retention.csv"
LABEL_SHIFT_FILENAME: Final = "stqa2_label_shift_by_date.csv"
MODEL_METRICS_FILENAME: Final = "stqa2_frozen_primary_oof_metrics.csv"
BOOTSTRAP_FILENAME: Final = "stqa2_frozen_primary_oof_bootstrap.csv"
SUMMARY_FILENAME: Final = "stqa2_sensitivity_summary.json"
PROVENANCE_FILENAME: Final = "stqa2_sensitivity_provenance.json"
_BASE_TARGET_FILES: Final = (
    "development_target_qa.parquet",
    "date_summary.parquet",
    "scene_contributions.parquet",
)
_MODEL_READY_FILENAME: Final = "development_targets_model_ready.parquet"


class Stqa2SensitivityError(RuntimeError):
    """Raised when strict-target sensitivity inputs or contracts are invalid."""


@dataclass(frozen=True)
class Stqa2SensitivityConfig:
    path: Path
    semantic_sha256: str
    primary_target_directory: Path
    strict_target_directory: Path
    primary_research_config: Path
    strict_research_config: Path
    result_analysis_config: Path
    evaluation_directory: Path
    output_directory: Path
    final_test_year: int
    expected_overpass_count: int
    expected_tract_count: int
    minimum_required_usable_dates: int
    strict_threshold_k: float
    family: str
    baseline_model_id: str
    target_model_id: str
    bootstrap_seed: int
    bootstrap_replicates: int
    confidence_level: float


@dataclass(frozen=True)
class TargetStage:
    progress: dict[str, Any]
    progress_sha256: str
    target_qa: pd.DataFrame
    date_summary: pd.DataFrame
    model_ready: pd.DataFrame
    file_records: dict[str, dict[str, Any]]
    state: str = "model_ready"
    model_ready_promoted: bool = True
    analysis_rows_derived_from_complete_qa: bool = False
    fixed_grid_lock_sha256: str = ""


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve(root: Path, value: object, *, name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise Stqa2SensitivityError(f"{name} must be a non-empty path.")
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _exact_keys(payload: dict[str, Any], expected: set[str], *, name: str) -> None:
    if set(payload) != expected:
        raise Stqa2SensitivityError(f"{name} keys are not frozen.")


def load_stqa2_sensitivity_config(
    path: str | Path = "configs/stqa2_sensitivity_analysis.toml",
    *,
    project_root: Path | None = None,
) -> Stqa2SensitivityConfig:
    root = _root() if project_root is None else project_root.resolve()
    config_path = _resolve(root, str(path), name="config")
    raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    _exact_keys(
        raw,
        {"schema_version", "algorithm_version", "paths", "analysis", "bootstrap"},
        name="config",
    )
    if raw["schema_version"] != 1 or raw["algorithm_version"] != ALGORITHM_VERSION:
        raise Stqa2SensitivityError("ST_QA sensitivity config identity is invalid.")
    paths, analysis, bootstrap = raw["paths"], raw["analysis"], raw["bootstrap"]
    if not all(isinstance(item, dict) for item in (paths, analysis, bootstrap)):
        raise Stqa2SensitivityError("ST_QA sensitivity config sections are invalid.")
    _exact_keys(
        paths,
        {
            "primary_target_directory",
            "strict_target_directory",
            "primary_research_config",
            "strict_research_config",
            "result_analysis_config",
            "evaluation_directory",
            "output_directory",
        },
        name="paths",
    )
    _exact_keys(
        analysis,
        {
            "final_test_year",
            "final_test_locked",
            "expected_overpass_count",
            "expected_tract_count",
            "minimum_required_usable_dates",
            "strict_st_uncertainty_threshold_k",
            "family",
            "baseline_model_id",
            "target_model_id",
        },
        name="analysis",
    )
    _exact_keys(bootstrap, {"seed", "replicates", "confidence_level"}, name="bootstrap")
    if analysis["final_test_year"] != 2025 or analysis["final_test_locked"] is not True:
        raise Stqa2SensitivityError("The 2025 final test must remain locked.")
    if (
        analysis["expected_overpass_count"] != 90
        or analysis["expected_tract_count"] != 1096
        or analysis["minimum_required_usable_dates"] != 30
        or float(analysis["strict_st_uncertainty_threshold_k"]) != 2.0
        or analysis["family"] != "joint"
        or analysis["baseline_model_id"] != "B1"
        or analysis["target_model_id"] != "M2"
    ):
        raise Stqa2SensitivityError("Frozen ST_QA sensitivity analysis settings drifted.")
    semantic = canonical_sha256(raw)
    return Stqa2SensitivityConfig(
        path=config_path,
        semantic_sha256=semantic,
        primary_target_directory=_resolve(
            root, paths["primary_target_directory"], name="primary target directory"
        ),
        strict_target_directory=_resolve(
            root, paths["strict_target_directory"], name="strict target directory"
        ),
        primary_research_config=_resolve(
            root, paths["primary_research_config"], name="primary research config"
        ),
        strict_research_config=_resolve(
            root, paths["strict_research_config"], name="strict research config"
        ),
        result_analysis_config=_resolve(
            root, paths["result_analysis_config"], name="result analysis config"
        ),
        evaluation_directory=_resolve(
            root, paths["evaluation_directory"], name="evaluation directory"
        ),
        output_directory=_resolve(root, paths["output_directory"], name="output directory"),
        final_test_year=2025,
        expected_overpass_count=90,
        expected_tract_count=1096,
        minimum_required_usable_dates=30,
        strict_threshold_k=2.0,
        family="joint",
        baseline_model_id="B1",
        target_model_id="M2",
        bootstrap_seed=int(bootstrap["seed"]),
        bootstrap_replicates=int(bootstrap["replicates"]),
        confidence_level=float(bootstrap["confidence_level"]),
    )


def validate_research_config_pair(config: Stqa2SensitivityConfig) -> dict[str, str]:
    primary_raw = tomllib.loads(config.primary_research_config.read_text(encoding="utf-8"))
    strict_raw = tomllib.loads(config.strict_research_config.read_text(encoding="utf-8"))
    expected = copy.deepcopy(primary_raw)
    expected["landsat"]["apply_st_uncertainty_threshold"] = True
    if strict_raw != expected:
        raise Stqa2SensitivityError(
            "Strict research config must differ only by enabling the ST_QA threshold."
        )
    if (
        primary_raw["landsat"]["apply_st_uncertainty_threshold"] is not False
        or strict_raw["landsat"]["apply_st_uncertainty_threshold"] is not True
        or float(strict_raw["landsat"]["maximum_st_uncertainty_kelvin"]) != 2.0
        or primary_raw["study"]["unlock_final_test"] is not False
        or strict_raw["study"]["unlock_final_test"] is not False
    ):
        raise Stqa2SensitivityError(
            "Research config pair violates the frozen sensitivity contract."
        )
    primary_config = load_config(config.primary_research_config)
    strict_config = load_config(config.strict_research_config)
    return {
        "primary_config_file_sha256": sha256_file(config.primary_research_config),
        "strict_config_file_sha256": sha256_file(config.strict_research_config),
        "primary_target_config_sha256": target_config_sha256(primary_config),
        "strict_target_config_sha256": target_config_sha256(strict_config),
    }


def _normalize_dates(frame: pd.DataFrame, *, name: str, final_test_year: int) -> pd.DataFrame:
    result = frame.copy()
    if "target_date" not in result:
        raise Stqa2SensitivityError(f"{name} lacks target_date.")
    result["target_date"] = pd.to_datetime(result["target_date"], errors="raise")
    if result["target_date"].dt.year.ge(final_test_year).any():
        raise Stqa2SensitivityError(f"{name} contains locked 2025+ rows.")
    return result


def _authenticate_fixed_grid_lock(
    directory: Path,
    *,
    progress: dict[str, Any],
    expected_target_config_sha256: str,
    require_config_file_sha256: str | None,
) -> str:
    path = directory / "fixed_grid_lock.json"
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise Stqa2SensitivityError(f"Target fixed-grid lock is unreadable: {directory}") from error
    if not isinstance(lock, dict):
        raise Stqa2SensitivityError("Target fixed-grid lock is not an object.")
    payload = lock.get("target_config_payload")
    fingerprint = lock.get("target_pipeline_fingerprint")
    if (
        not isinstance(payload, dict)
        or canonical_sha256(payload) != expected_target_config_sha256
        or lock.get("target_config_sha256") != expected_target_config_sha256
        or not isinstance(fingerprint, dict)
        or canonical_sha256(fingerprint) != lock.get("target_pipeline_sha256")
        or progress.get("target_pipeline_sha256") != lock.get("target_pipeline_sha256")
        or progress.get("target_config_sha256") != lock.get("target_config_sha256")
        or progress.get("grid_sha256") != lock.get("target_grid_identity_sha256")
        or not isinstance(lock.get("static_land_mask_sha256"), str)
        or not lock["static_land_mask_sha256"]
    ):
        raise Stqa2SensitivityError("Target fixed-grid/config/pipeline lock chain is invalid.")
    if require_config_file_sha256 is not None and (
        lock.get("research_config_file_sha256") != require_config_file_sha256
        or progress.get("research_config_file_sha256") != require_config_file_sha256
    ):
        raise Stqa2SensitivityError("Target fixed-grid lock does not authenticate its config file.")
    return sha256_file(path)


def authenticate_target_stage(
    directory: Path,
    *,
    expected_target_config_sha256: str,
    expected_overpass_count: int,
    expected_tract_count: int,
    final_test_year: int,
    require_config_file_sha256: str | None = None,
    allow_complete_gate_failed: bool = False,
) -> TargetStage:
    progress_path = directory / "build_progress.json"
    try:
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise Stqa2SensitivityError(f"Target progress is unreadable: {directory}") from error
    if not isinstance(progress, dict):
        raise Stqa2SensitivityError(f"Target progress is not an object: {directory}")
    state = progress.get("state")
    promoted = state == "model_ready"
    accepted_gate_failure = allow_complete_gate_failed and state == "complete_gate_failed"
    if (
        not (promoted or accepted_gate_failure)
        or progress.get("build_complete") is not True
        or progress.get("partial_outputs_only") is not False
        or progress.get("promoted_outputs_valid") is not promoted
        or progress.get("expected_overpass_count") != expected_overpass_count
        or progress.get("completed_overpass_count") != expected_overpass_count
        or progress.get("target_config_sha256") != expected_target_config_sha256
    ):
        raise Stqa2SensitivityError(f"Target stage is not an accepted complete build: {directory}")
    fixed_grid_lock_sha256 = _authenticate_fixed_grid_lock(
        directory,
        progress=progress,
        expected_target_config_sha256=expected_target_config_sha256,
        require_config_file_sha256=require_config_file_sha256,
    )
    records = progress.get("aggregate_outputs")
    required_files = (*_BASE_TARGET_FILES, *(() if not promoted else (_MODEL_READY_FILENAME,)))
    if not isinstance(records, dict) or set(records) != set(required_files):
        raise Stqa2SensitivityError("Target progress lacks required aggregate file locks.")
    frames: dict[str, pd.DataFrame] = {}
    observed_records: dict[str, dict[str, Any]] = {}
    for filename in required_files:
        record = records[filename]
        path = directory / filename
        if (
            not isinstance(record, dict)
            or not path.is_file()
            or path.stat().st_size != record.get("bytes")
            or sha256_file(path) != record.get("sha256")
        ):
            raise Stqa2SensitivityError(f"Target aggregate byte lock failed: {filename}")
        frame = pd.read_parquet(path)
        observed = parquet_file_record(path, frame)
        if any(observed[key] != record.get(key) for key in ("rows", "schema_sha256")):
            raise Stqa2SensitivityError(
                f"Target aggregate schema/cardinality lock failed: {filename}"
            )
        frames[filename] = _normalize_dates(frame, name=filename, final_test_year=final_test_year)
        observed_records[filename] = observed
    qa = frames[_BASE_TARGET_FILES[0]]
    dates = frames[_BASE_TARGET_FILES[1]]
    contributions = frames[_BASE_TARGET_FILES[2]]
    if (
        len(qa) != expected_overpass_count * expected_tract_count
        or len(dates) != expected_overpass_count
        or qa.duplicated(["tract_geoid", "target_date"]).any()
        or dates.duplicated(["target_date"]).any()
        or contributions.empty
    ):
        raise Stqa2SensitivityError("Target aggregate key cardinality is invalid.")
    derived = qa.loc[qa["date_usable"] & qa["target_available"]].copy()
    if promoted:
        ready = frames[_MODEL_READY_FILENAME]
        left = ready.sort_values(["target_date", "tract_geoid"], kind="stable").reset_index(
            drop=True
        )
        right = derived.sort_values(["target_date", "tract_geoid"], kind="stable").reset_index(
            drop=True
        )
        if (
            ready.duplicated(["tract_geoid", "target_date"]).any()
            or list(left.columns) != list(right.columns)
            or not left.equals(right)
        ):
            raise Stqa2SensitivityError(
                "Promoted model-ready targets do not exactly equal rows derived from complete QA."
            )
    else:
        ready = derived
    return TargetStage(
        progress=progress,
        progress_sha256=sha256_file(progress_path),
        target_qa=qa,
        date_summary=dates,
        model_ready=ready,
        file_records=observed_records,
        state=str(state),
        model_ready_promoted=promoted,
        analysis_rows_derived_from_complete_qa=not promoted,
        fixed_grid_lock_sha256=fixed_grid_lock_sha256,
    )


def validate_fixed_support(
    primary: TargetStage, strict: TargetStage, *, threshold_k: float
) -> pd.DataFrame:
    key = ["tract_geoid", "target_date"]
    invariant = [
        "overpass_id",
        "platform",
        "spatial_block",
        "rasterized_pixel_count",
        "footprint_pixel_count",
        "eligible_pixel_count_static",
        "eligible_pixel_identity_sha256",
        "footprint_fraction",
        "tract_manifest_sha256",
        "grid_sha256",
    ]
    joined = primary.target_qa[
        key + invariant + ["valid_pixel_count", "target_available", "date_usable"]
    ].merge(
        strict.target_qa[
            key
            + invariant
            + [
                "valid_pixel_count",
                "target_available",
                "date_usable",
                "p90_st_uncertainty_k",
            ]
        ],
        on=key,
        how="outer",
        suffixes=("_primary", "_strict"),
        indicator=True,
        validate="one_to_one",
    )
    if len(joined) != len(primary.target_qa) or not joined["_merge"].eq("both").all():
        raise Stqa2SensitivityError("Primary and strict target key universes differ.")
    integer_invariants = {
        "rasterized_pixel_count",
        "footprint_pixel_count",
        "eligible_pixel_count_static",
    }
    for column in invariant:
        left, right = joined[f"{column}_primary"], joined[f"{column}_strict"]
        if column in integer_invariants:
            equal = (
                left.notna().all()
                and right.notna().all()
                and np.array_equal(left.to_numpy(np.int64), right.to_numpy(np.int64))
            )
        elif pd.api.types.is_numeric_dtype(left):
            equal = np.isclose(left.to_numpy(float), right.to_numpy(float), equal_nan=True)
        else:
            equal = left.astype(str).to_numpy() == right.astype(str).to_numpy()
        if not bool(np.all(equal)):
            raise Stqa2SensitivityError(f"Fixed target support drifted: {column}")
    if (joined["valid_pixel_count_strict"] > joined["valid_pixel_count_primary"]).any():
        raise Stqa2SensitivityError("A strict valid-pixel count exceeds the primary count.")
    if (joined["target_available_strict"] & ~joined["target_available_primary"]).any():
        raise Stqa2SensitivityError("Strict target availability is not a subset of primary.")
    if (joined["date_usable_strict"] & ~joined["date_usable_primary"]).any():
        raise Stqa2SensitivityError("Strict usable dates are not a subset of primary.")
    available_p90 = joined.loc[joined["target_available_strict"], "p90_st_uncertainty_k"]
    if available_p90.isna().any() or available_p90.gt(threshold_k + 1e-9).any():
        raise Stqa2SensitivityError("Strict available labels contain ST_QA values above 2 K.")
    return joined


def build_date_retention(primary: TargetStage, strict: TargetStage) -> pd.DataFrame:
    columns = [
        "target_date",
        "overpass_id",
        "platform",
        "retained_tract_count",
        "retained_tract_fraction",
        "date_usable",
        "relative_endpoint_coverage_pass",
    ]
    result = primary.date_summary[columns].merge(
        strict.date_summary[columns],
        on=["target_date", "overpass_id", "platform"],
        how="outer",
        suffixes=("_primary", "_strict"),
        indicator=True,
        validate="one_to_one",
    )
    if not result["_merge"].eq("both").all():
        raise Stqa2SensitivityError("Primary and strict date manifests differ.")
    result = result.drop(columns="_merge")
    result["retained_tract_count_change"] = (
        result["retained_tract_count_strict"] - result["retained_tract_count_primary"]
    )
    result["retained_tract_fraction_change"] = (
        result["retained_tract_fraction_strict"] - result["retained_tract_fraction_primary"]
    )
    return result.sort_values("target_date", kind="stable").reset_index(drop=True)


def build_label_shift(primary: TargetStage, strict: TargetStage) -> pd.DataFrame:
    key = ["tract_geoid", "target_date"]
    columns = key + ["target_available", "target_lst_c"]
    joined = primary.target_qa[columns].merge(
        strict.target_qa[columns],
        on=key,
        how="inner",
        suffixes=("_primary", "_strict"),
        validate="one_to_one",
    )
    joined = joined.loc[
        joined["target_available_primary"] & joined["target_available_strict"]
    ].copy()
    joined["strict_minus_primary_lst_c"] = (
        joined["target_lst_c_strict"] - joined["target_lst_c_primary"]
    )
    rows: list[dict[str, Any]] = []
    for date, group in joined.groupby("target_date", sort=True):
        delta = group["strict_minus_primary_lst_c"].to_numpy(float)
        rho = group["target_lst_c_primary"].corr(group["target_lst_c_strict"], method="spearman")
        rows.append(
            {
                "target_date": date,
                "matched_label_count": len(group),
                "mean_strict_minus_primary_lst_c": float(np.mean(delta)),
                "median_strict_minus_primary_lst_c": float(np.median(delta)),
                "mean_absolute_label_shift_c": float(np.mean(np.abs(delta))),
                "rmse_label_shift_c": float(np.sqrt(np.mean(np.square(delta)))),
                "strict_vs_primary_spearman": None if pd.isna(rho) else float(rho),
            }
        )
    return pd.DataFrame(rows)


def _one_model_metrics(group: pd.DataFrame) -> dict[str, Any]:
    error = group["y_pred"].to_numpy(float) - group["y_true"].to_numpy(float)
    per_date = []
    for _, date_rows in group.groupby("target_date", sort=True):
        date_error = date_rows["y_pred"].to_numpy(float) - date_rows["y_true"].to_numpy(float)
        rho = date_rows["y_true"].corr(date_rows["y_pred"], method="spearman")
        per_date.append(
            (
                float(np.mean(np.abs(date_error))),
                float(np.sqrt(np.mean(np.square(date_error)))),
                float(np.mean(date_error)),
                None if pd.isna(rho) else float(rho),
            )
        )
    defined = [row[3] for row in per_date if row[3] is not None]
    return {
        "tract_date_row_count": len(group),
        "independent_date_count": group["target_date"].nunique(),
        "independent_spatial_block_count": group["spatial_block"].nunique(),
        "equal_date_weighted_mae_c": float(np.mean([row[0] for row in per_date])),
        "equal_date_weighted_rmse_c": float(np.mean([row[1] for row in per_date])),
        "pooled_rmse_c": float(np.sqrt(np.mean(np.square(error)))),
        "equal_date_weighted_mean_signed_error_c": float(np.mean([row[2] for row in per_date])),
        "median_per_date_spearman": None if not defined else float(np.median(defined)),
        "spearman_defined_date_count": len(defined),
        "spearman_undefined_date_count": len(per_date) - len(defined),
    }


def build_frozen_primary_oof_sensitivity(
    oof: pd.DataFrame,
    primary_model_ready: pd.DataFrame,
    strict_model_ready: pd.DataFrame,
    *,
    config: Stqa2SensitivityConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any] | None, str | None]:
    empty_metrics = pd.DataFrame()
    empty_predictions = pd.DataFrame()
    if strict_model_ready.empty:
        return empty_predictions, empty_metrics, None, "no_strict_analysis_labels"
    keys = ["tract_geoid", "target_date"]
    strict = strict_model_ready[keys + ["target_lst_c", "spatial_block"]].copy()
    primary = primary_model_ready[keys + ["target_lst_c"]].copy()
    strict["target_date"] = pd.to_datetime(strict["target_date"], errors="raise")
    primary["target_date"] = pd.to_datetime(primary["target_date"], errors="raise")
    focus = oof.loc[
        oof["family"].eq(config.family)
        & oof["model_id"].isin([config.baseline_model_id, config.target_model_id])
    ].copy()
    joined = focus.merge(
        primary, on=keys, how="inner", suffixes=("", "_primary"), validate="many_to_one"
    )
    if len(joined) != len(focus):
        raise Stqa2SensitivityError("Primary OOF keys do not match the primary target table.")
    if not np.array_equal(
        joined["y_true"].to_numpy(np.float64).view(np.uint64),
        joined["target_lst_c"].to_numpy(np.float64).view(np.uint64),
    ):
        raise Stqa2SensitivityError("Primary OOF truth differs from the primary target table.")
    joined = joined.drop(columns=["target_lst_c"]).merge(
        strict, on=keys, how="inner", suffixes=("", "_strict"), validate="many_to_one"
    )
    expected = len(strict) * 2
    if len(joined) != expected or joined.duplicated([*keys, "model_id"]).any():
        raise Stqa2SensitivityError("Strict frozen-OOF comparison is not exactly paired.")
    if not joined["spatial_block"].astype(str).equals(joined["spatial_block_strict"].astype(str)):
        raise Stqa2SensitivityError("Strict and OOF spatial blocks differ.")
    joined["primary_y_true"] = joined["y_true"]
    joined["y_true"] = joined["target_lst_c"]
    rows = []
    for model_id, group in joined.groupby("model_id", sort=True):
        rows.append({"family": config.family, "model_id": model_id, **_one_model_metrics(group)})
    metrics = pd.DataFrame(rows).sort_values("model_id", kind="stable").reset_index(drop=True)
    cells = aggregate_paired_date_block_errors(
        joined,
        family=config.family,
        target_model_id=config.target_model_id,
        baseline_model_id=config.baseline_model_id,
    )
    try:
        bootstrap = crossed_date_spatial_block_bootstrap(
            cells,
            seed=config.bootstrap_seed,
            replicates=config.bootstrap_replicates,
            confidence_level=config.confidence_level,
        )
    except ModelResultAnalysisError as error:
        if "contains no observations" not in str(error):
            raise
        reason = (
            "crossed_cluster_bootstrap_not_estimable_because_sparse_date_block_support_"
            "produced_an_empty_cluster_draw"
        )
        return joined, metrics, None, reason
    return joined, metrics, bootstrap, None


def _csv_record(path: Path, frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "path": path.name,
        "path_base": "output_directory",
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "rows": len(frame),
        "schema_sha256": canonical_sha256([(c, str(t)) for c, t in frame.dtypes.items()]),
    }


def _begin_output_transaction(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / PROVENANCE_FILENAME).unlink(missing_ok=True)


def analyze_stqa2_sensitivity(
    *,
    config_path: str | Path = "configs/stqa2_sensitivity_analysis.toml",
) -> dict[str, Any]:
    config = load_stqa2_sensitivity_config(config_path)
    config_locks = validate_research_config_pair(config)
    primary = authenticate_target_stage(
        config.primary_target_directory,
        expected_target_config_sha256=config_locks["primary_target_config_sha256"],
        expected_overpass_count=config.expected_overpass_count,
        expected_tract_count=config.expected_tract_count,
        final_test_year=config.final_test_year,
    )
    strict = authenticate_target_stage(
        config.strict_target_directory,
        expected_target_config_sha256=config_locks["strict_target_config_sha256"],
        expected_overpass_count=config.expected_overpass_count,
        expected_tract_count=config.expected_tract_count,
        final_test_year=config.final_test_year,
        require_config_file_sha256=config_locks["strict_config_file_sha256"],
        allow_complete_gate_failed=True,
    )
    validate_fixed_support(primary, strict, threshold_k=config.strict_threshold_k)
    retention = build_date_retention(primary, strict)
    label_shift = build_label_shift(primary, strict)
    result_config = load_result_analysis_config(config.result_analysis_config)
    authenticated = authenticate_model_results(result_config, config.evaluation_directory)
    _, metrics, bootstrap, bootstrap_not_estimable_reason = build_frozen_primary_oof_sensitivity(
        authenticated.oof,
        primary.model_ready,
        strict.model_ready,
        config=config,
    )
    bootstrap_table = pd.DataFrame([] if bootstrap is None else [bootstrap])
    strict_usable_dates = int(strict.date_summary["date_usable"].sum())
    if strict_usable_dates != int(strict.progress["usable_overpass_count"]):
        raise Stqa2SensitivityError("Strict usable-date count disagrees with build progress.")
    minimum_gate = strict_usable_dates >= config.minimum_required_usable_dates
    if (
        strict.progress.get("usable_date_gate_pass") is not minimum_gate
        or strict.model_ready_promoted is not minimum_gate
    ):
        raise Stqa2SensitivityError("Strict target state disagrees with the frozen date gate.")
    delta = (
        label_shift["mean_absolute_label_shift_c"]
        if not label_shift.empty
        else pd.Series(dtype=float)
    )
    summary: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "analysis_scope": "locked_2020_2024_strict_pixel_stqa2_label_sensitivity",
        "final_test_year": 2025,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "strict_pixel_rule": "ST_QA <= 2.0 K before tract aggregation",
        "primary_pixel_rule": "no ST_QA hard threshold",
        "fixed_support_invariant_pass": True,
        "expected_overpass_count": config.expected_overpass_count,
        "primary_usable_date_count": int(primary.date_summary["date_usable"].sum()),
        "strict_usable_date_count": strict_usable_dates,
        "minimum_required_usable_date_count": config.minimum_required_usable_dates,
        "strict_minimum_date_gate_pass": minimum_gate,
        "primary_model_ready_row_count": len(primary.model_ready),
        "strict_target_stage_state": strict.state,
        "strict_promoted_model_ready_row_count": (
            len(strict.model_ready) if strict.model_ready_promoted else 0
        ),
        "strict_analysis_label_row_count": len(strict.model_ready),
        "strict_analysis_labels_derived_from_complete_qa": (
            strict.analysis_rows_derived_from_complete_qa
        ),
        "matched_label_date_count": len(label_shift),
        "median_per_date_mean_absolute_label_shift_c": None
        if delta.empty
        else float(delta.median()),
        "frozen_primary_oof_sensitivity_estimable": bootstrap is not None,
        "frozen_primary_oof_bootstrap_not_estimable_reason": (bootstrap_not_estimable_reason),
        "frozen_primary_oof_refit_performed": False,
        "frozen_primary_oof_interpretation": (
            "Existing primary-label OOF predictions scored against strict reaggregated labels; "
            "this is not a strict-label model refit."
        ),
        "frozen_primary_oof_bootstrap": bootstrap,
        "model_lock_readiness": {
            "strict_pixel_sensitivity_complete": True,
            "strict_minimum_date_gate_pass": minimum_gate,
            "decision_deferred_to_full_robustness_reconciliation": True,
        },
        "input_authentication": {
            **config_locks,
            "primary_progress_sha256": primary.progress_sha256,
            "strict_progress_sha256": strict.progress_sha256,
            "primary_fixed_grid_lock_sha256": primary.fixed_grid_lock_sha256,
            "strict_fixed_grid_lock_sha256": strict.fixed_grid_lock_sha256,
            "model_compile_provenance_commit_sha256": authenticated.input_authentication[
                "compile_provenance_commit_sha256"
            ],
            "model_oof_predictions_sha256": authenticated.input_authentication[
                "oof_predictions_sha256"
            ],
        },
    }
    output = config.output_directory
    _begin_output_transaction(output)
    tables = {
        DATE_RETENTION_FILENAME: retention,
        LABEL_SHIFT_FILENAME: label_shift,
        MODEL_METRICS_FILENAME: metrics,
        BOOTSTRAP_FILENAME: bootstrap_table,
    }
    for filename, frame in tables.items():
        atomic_csv(frame, output / filename)
    atomic_json(summary, output / SUMMARY_FILENAME)
    pipeline_sha256, pipeline_fingerprint = code_runtime_fingerprint(
        project_root=_root(),
        relative_paths=(
            "configs/stqa2_sensitivity_analysis.toml",
            "scripts/analyze_stqa2_sensitivity.py",
            "src/la_heat/stqa2_sensitivity_analysis.py",
            "src/la_heat/model_result_analysis.py",
            "src/la_heat/provenance.py",
        ),
        algorithm_version=ALGORITHM_VERSION,
    )
    provenance: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "complete",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "final_test_year": 2025,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "analysis_config": {
            "path": config.path.as_posix(),
            "sha256": sha256_file(config.path),
            "semantic_sha256": config.semantic_sha256,
        },
        "input_authentication": summary["input_authentication"],
        "pipeline_sha256": pipeline_sha256,
        "pipeline_fingerprint": pipeline_fingerprint,
        "scientific_contract": {
            "strict_threshold_applied_at_pixel_level": True,
            "fixed_eligible_land_denominator": True,
            "complete_gate_failed_strict_qa_is_accepted_but_not_promoted": True,
            "strict_analysis_rows_derived_from_complete_qa": (
                strict.analysis_rows_derived_from_complete_qa
            ),
            "primary_oof_predictions_refit": False,
            "random_row_resampling_used": False,
            "complete_date_and_spatial_block_bootstrap_used_when_estimable": bootstrap is not None,
            "final_test_unlocked": False,
        },
        "summary_commit_sha256": canonical_sha256(summary),
        "output_files": {
            **{
                filename: _csv_record(output / filename, frame)
                for filename, frame in tables.items()
            },
            SUMMARY_FILENAME: {
                "path": SUMMARY_FILENAME,
                "path_base": "output_directory",
                "sha256": sha256_file(output / SUMMARY_FILENAME),
                "bytes": (output / SUMMARY_FILENAME).stat().st_size,
            },
        },
    }
    provenance["commit_sha256"] = canonical_sha256(provenance)
    atomic_json(provenance, output / PROVENANCE_FILENAME)
    return provenance
