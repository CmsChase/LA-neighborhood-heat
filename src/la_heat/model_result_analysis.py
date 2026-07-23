"""Authenticated initial analysis of the frozen development-model results.

This stage reads only the compiled development OOF predictions, their summary
metrics, and the compile provenance marker.  It does not read outer fragments,
fit models, unlock 2025, or resample individual tract-date rows.
"""

from __future__ import annotations

import json
import math
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from la_heat.model_run_compile import (
    OOF_PREDICTIONS_FILENAME,
    SUMMARY_METRIC_COLUMNS,
    SUMMARY_METRICS_FILENAME,
)
from la_heat.model_selection import MODEL_IDS
from la_heat.model_task_engine import OUTER_PREDICTION_COLUMNS
from la_heat.provenance import (
    atomic_csv,
    atomic_json,
    canonical_sha256,
    code_runtime_fingerprint,
    sha256_file,
)
from la_heat.validation_splits import FAMILIES

RESULT_ANALYSIS_SCHEMA_VERSION: Final = 1
RESULT_ANALYSIS_ALGORITHM_VERSION: Final = "initial-model-result-analysis-v1"
RESULT_ANALYSIS_STATE: Final = "frozen_development_result_analysis"
BOOTSTRAP_METHOD: Final = "crossed_date_spatial_block"
BOOTSTRAP_SAMPLING_UNIT: Final = "complete_clusters_only"
FROZEN_BOOTSTRAP_SEED: Final = 20_260_722
FROZEN_BOOTSTRAP_REPLICATES: Final = 5_000
FROZEN_CONFIDENCE_LEVEL: Final = 0.95
DEFAULT_RESULT_ANALYSIS_CONFIG: Final = Path("configs/result_analysis.toml")
POINT_COMPARISON_FILENAME: Final = "family_model_point_comparison.csv"
BOOTSTRAP_FILENAME: Final = "joint_m2_crossed_cluster_bootstrap.csv"
SUCCESS_GATES_FILENAME: Final = "protocol_success_gates.csv"
SUMMARY_FILENAME: Final = "model_results_initial_summary.json"
PROVENANCE_FILENAME: Final = "model_results_initial_provenance.json"
_EXPECTED_COUNTS: Final = {
    "independent_date_count": 65,
    "independent_spatial_block_count": 71,
    "tract_date_row_count": 63_403,
}
_PIPELINE_PATHS: Final = (
    "scripts/analyze_model_results.py",
    "src/la_heat/model_result_analysis.py",
    "src/la_heat/provenance.py",
)


class ModelResultAnalysisError(ValueError):
    """Raised when authenticated result analysis would violate its contract."""


@dataclass(frozen=True)
class ResultAnalysisConfig:
    """Validated frozen settings for the initial development-result analysis."""

    path: Path
    semantic_sha256: str
    evaluation_directory: Path
    output_directory: Path
    final_test_year: int
    final_test_locked: bool
    target_family: str
    target_model_id: str
    legal_baseline_model_ids: tuple[str, ...]
    primary_metric_column: str
    spearman_metric_column: str
    expected_independent_date_count: int
    expected_independent_spatial_block_count: int
    expected_tract_date_row_count: int
    bootstrap_method: str
    bootstrap_sampling_unit: str
    bootstrap_seed: int
    bootstrap_replicates: int
    confidence_level: float
    minimum_relative_mae_improvement_fraction: float
    minimum_median_per_date_spearman: float
    uncertainty_relative_ci_lower_must_exceed: float


@dataclass(frozen=True)
class AuthenticatedModelResults:
    """In-memory OOF and summary frames plus their verified input locks."""

    oof: pd.DataFrame
    summary: pd.DataFrame
    compile_provenance: dict[str, Any]
    input_authentication: dict[str, Any]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _exact_keys(payload: Mapping[str, Any], expected: set[str], *, name: str) -> None:
    observed = set(payload)
    if observed != expected:
        raise ModelResultAnalysisError(
            f"{name} keys must be exactly {sorted(expected)}; got {sorted(observed)}."
        )


def _integer(value: object, *, name: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ModelResultAnalysisError(f"{name} must be an integer >= {minimum}.")
    return value


def _finite_number(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelResultAnalysisError(f"{name} must be numeric.")
    number = float(value)
    if not math.isfinite(number):
        raise ModelResultAnalysisError(f"{name} must be finite.")
    return number


def _resolved_project_path(value: object, *, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ModelResultAnalysisError(f"{name} must be a non-empty path string.")
    path = Path(value)
    return (path if path.is_absolute() else _project_root() / path).resolve()


def load_result_analysis_config(
    path: str | Path = DEFAULT_RESULT_ANALYSIS_CONFIG,
) -> ResultAnalysisConfig:
    """Load the predeclared result analysis and reject any scientific drift."""

    config_path = Path(path).resolve()
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    _exact_keys(
        raw,
        {
            "schema_version",
            "algorithm_version",
            "state",
            "paths",
            "analysis",
            "bootstrap",
            "success_gates",
        },
        name="result-analysis configuration",
    )
    if raw["schema_version"] != RESULT_ANALYSIS_SCHEMA_VERSION:
        raise ModelResultAnalysisError("Unsupported result-analysis schema version.")
    if raw["algorithm_version"] != RESULT_ANALYSIS_ALGORITHM_VERSION:
        raise ModelResultAnalysisError("Result-analysis algorithm version drifted.")
    if raw["state"] != RESULT_ANALYSIS_STATE:
        raise ModelResultAnalysisError("Result-analysis configuration is not frozen.")

    paths = raw["paths"]
    analysis = raw["analysis"]
    bootstrap = raw["bootstrap"]
    gates = raw["success_gates"]
    if not all(isinstance(value, dict) for value in (paths, analysis, bootstrap, gates)):
        raise ModelResultAnalysisError("Result-analysis sections must be TOML tables.")
    _exact_keys(
        paths,
        {"evaluation_directory", "output_directory"},
        name="paths",
    )
    _exact_keys(
        analysis,
        {
            "final_test_year",
            "final_test_locked",
            "target_family",
            "target_model_id",
            "legal_baseline_model_ids",
            "primary_metric_column",
            "spearman_metric_column",
            "expected_independent_date_count",
            "expected_independent_spatial_block_count",
            "expected_tract_date_row_count",
        },
        name="analysis",
    )
    _exact_keys(
        bootstrap,
        {"method", "sampling_unit", "seed", "replicates", "confidence_level"},
        name="bootstrap",
    )
    _exact_keys(
        gates,
        {
            "minimum_relative_mae_improvement_fraction",
            "minimum_median_per_date_spearman",
            "uncertainty_relative_ci_lower_must_exceed",
        },
        name="success_gates",
    )

    final_test_year = _integer(analysis["final_test_year"], name="final_test_year")
    if final_test_year != 2025 or analysis["final_test_locked"] is not True:
        raise ModelResultAnalysisError("The 2025 final test must remain explicitly locked.")
    if analysis["target_family"] != "joint" or analysis["target_model_id"] != "M2":
        raise ModelResultAnalysisError("The initial comparison must remain joint/M2.")
    legal_baselines = analysis["legal_baseline_model_ids"]
    if legal_baselines != ["B0", "B1", "B2"]:
        raise ModelResultAnalysisError("Legal baselines must remain exactly B0, B1, and B2.")
    if analysis["primary_metric_column"] != "primary_equal_date_weighted_mae_c":
        raise ModelResultAnalysisError("The frozen primary MAE metric cannot change.")
    if analysis["spearman_metric_column"] != "median_per_date_spearman":
        raise ModelResultAnalysisError("The frozen Spearman metric cannot change.")

    counts = {
        "independent_date_count": _integer(
            analysis["expected_independent_date_count"],
            name="expected_independent_date_count",
        ),
        "independent_spatial_block_count": _integer(
            analysis["expected_independent_spatial_block_count"],
            name="expected_independent_spatial_block_count",
        ),
        "tract_date_row_count": _integer(
            analysis["expected_tract_date_row_count"],
            name="expected_tract_date_row_count",
        ),
    }
    if counts != _EXPECTED_COUNTS:
        raise ModelResultAnalysisError("Frozen development-result cardinalities drifted.")

    seed = _integer(bootstrap["seed"], name="bootstrap.seed", minimum=0)
    replicates = _integer(bootstrap["replicates"], name="bootstrap.replicates")
    confidence = _finite_number(
        bootstrap["confidence_level"], name="bootstrap.confidence_level"
    )
    if (
        bootstrap["method"] != BOOTSTRAP_METHOD
        or bootstrap["sampling_unit"] != BOOTSTRAP_SAMPLING_UNIT
        or seed != FROZEN_BOOTSTRAP_SEED
        or replicates != FROZEN_BOOTSTRAP_REPLICATES
        or confidence != FROZEN_CONFIDENCE_LEVEL
    ):
        raise ModelResultAnalysisError(
            "Bootstrap must remain the frozen 5,000-replicate crossed complete-cluster design."
        )

    minimum_improvement = _finite_number(
        gates["minimum_relative_mae_improvement_fraction"],
        name="minimum_relative_mae_improvement_fraction",
    )
    minimum_spearman = _finite_number(
        gates["minimum_median_per_date_spearman"],
        name="minimum_median_per_date_spearman",
    )
    uncertainty_floor = _finite_number(
        gates["uncertainty_relative_ci_lower_must_exceed"],
        name="uncertainty_relative_ci_lower_must_exceed",
    )
    if minimum_improvement != 0.10 or minimum_spearman != 0.50 or uncertainty_floor != 0.0:
        raise ModelResultAnalysisError("Predeclared protocol success thresholds drifted.")

    return ResultAnalysisConfig(
        path=config_path,
        semantic_sha256=canonical_sha256(raw),
        evaluation_directory=_resolved_project_path(
            paths["evaluation_directory"], name="paths.evaluation_directory"
        ),
        output_directory=_resolved_project_path(
            paths["output_directory"], name="paths.output_directory"
        ),
        final_test_year=final_test_year,
        final_test_locked=True,
        target_family=str(analysis["target_family"]),
        target_model_id=str(analysis["target_model_id"]),
        legal_baseline_model_ids=tuple(legal_baselines),
        primary_metric_column=str(analysis["primary_metric_column"]),
        spearman_metric_column=str(analysis["spearman_metric_column"]),
        expected_independent_date_count=counts["independent_date_count"],
        expected_independent_spatial_block_count=counts[
            "independent_spatial_block_count"
        ],
        expected_tract_date_row_count=counts["tract_date_row_count"],
        bootstrap_method=str(bootstrap["method"]),
        bootstrap_sampling_unit=str(bootstrap["sampling_unit"]),
        bootstrap_seed=seed,
        bootstrap_replicates=replicates,
        confidence_level=confidence,
        minimum_relative_mae_improvement_fraction=minimum_improvement,
        minimum_median_per_date_spearman=minimum_spearman,
        uncertainty_relative_ci_lower_must_exceed=uncertainty_floor,
    )


def _json_object(path: Path, *, name: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ModelResultAnalysisError(f"Cannot read valid {name}: {path}") from error
    if not isinstance(payload, dict):
        raise ModelResultAnalysisError(f"{name} must be a JSON object.")
    return payload


def _verify_compile_commit(payload: Mapping[str, Any]) -> str:
    working = dict(payload)
    recorded = working.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(working) != recorded:
        raise ModelResultAnalysisError("Compile provenance commit is invalid.")
    return recorded


def _verified_compiled_output(
    evaluation_directory: Path,
    output_files: object,
    filename: str,
) -> tuple[Path, dict[str, Any]]:
    if not isinstance(output_files, dict) or not isinstance(output_files.get(filename), dict):
        raise ModelResultAnalysisError(f"Compile provenance is missing {filename!r}.")
    record = dict(output_files[filename])
    if record.get("path_base") != "output_directory" or record.get("path") != filename:
        raise ModelResultAnalysisError(f"Unsafe or nonportable compile path for {filename!r}.")
    path = evaluation_directory / filename
    if not path.is_file():
        raise ModelResultAnalysisError(f"Authenticated input is missing: {path}")
    expected_bytes = _integer(record.get("bytes"), name=f"{filename}.bytes", minimum=0)
    expected_rows = _integer(record.get("rows"), name=f"{filename}.rows", minimum=0)
    expected_hash = record.get("sha256")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise ModelResultAnalysisError(f"{filename} has no valid SHA-256 lock.")
    if path.stat().st_size != expected_bytes or sha256_file(path) != expected_hash:
        raise ModelResultAnalysisError(f"{filename} byte lock does not match compile provenance.")
    record["rows"] = expected_rows
    return path, record


def _validate_oof(
    oof: pd.DataFrame,
    record: Mapping[str, Any],
    config: ResultAnalysisConfig,
) -> pd.DataFrame:
    if list(oof.columns) != list(OUTER_PREDICTION_COLUMNS):
        raise ModelResultAnalysisError("OOF prediction columns drifted from the compiled contract.")
    result = oof.copy()
    try:
        result["target_date"] = pd.to_datetime(result["target_date"], errors="raise")
    except (TypeError, ValueError) as error:
        raise ModelResultAnalysisError("OOF target dates are invalid.") from error
    if result.isna().any().any():
        raise ModelResultAnalysisError("OOF predictions contain missing values.")
    if (result["target_date"].dt.year >= config.final_test_year).any():
        raise ModelResultAnalysisError(
            f"Locked final-test year {config.final_test_year} or later appears in OOF results."
        )
    numeric = result[["y_true", "y_pred"]].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise ModelResultAnalysisError("OOF truth or predictions contain non-finite values.")
    result.loc[:, ["y_true", "y_pred"]] = numeric

    identity = ["family", "model_id", "tract_geoid", "target_date"]
    if result.duplicated(identity).any():
        raise ModelResultAnalysisError("OOF predictions duplicate a family/model/key.")
    expected_pairs = {(family, model_id) for family in FAMILIES for model_id in MODEL_IDS}
    observed_pairs = set(
        result.loc[:, ["family", "model_id"]].itertuples(index=False, name=None)
    )
    if observed_pairs != expected_pairs:
        raise ModelResultAnalysisError("OOF family/model coverage is incomplete.")
    group_sizes = result.groupby(["family", "model_id"], observed=True).size()
    if not group_sizes.eq(config.expected_tract_date_row_count).all():
        raise ModelResultAnalysisError("OOF family/model row cardinalities are incomplete.")

    key_blocks = result.loc[:, ["tract_geoid", "target_date", "spatial_block"]].drop_duplicates()
    if key_blocks.duplicated(["tract_geoid", "target_date"]).any():
        raise ModelResultAnalysisError("A tract-date key maps to multiple spatial blocks.")
    counts = {
        "rows": len(key_blocks),
        "dates": int(key_blocks["target_date"].nunique()),
        "blocks": int(key_blocks["spatial_block"].nunique()),
    }
    expected = {
        "rows": config.expected_tract_date_row_count,
        "dates": config.expected_independent_date_count,
        "blocks": config.expected_independent_spatial_block_count,
    }
    if counts != expected:
        raise ModelResultAnalysisError(f"OOF independent-unit counts drifted: {counts!r}.")
    if len(result) != record["rows"]:
        raise ModelResultAnalysisError("OOF row count disagrees with compile provenance.")
    schema_hash = canonical_sha256(
        [(column, str(dtype)) for column, dtype in result.dtypes.items()]
    )
    if record.get("schema_sha256") != schema_hash:
        raise ModelResultAnalysisError("OOF schema hash disagrees with compile provenance.")
    return result


def _validate_summary(
    summary: pd.DataFrame,
    record: Mapping[str, Any],
    oof: pd.DataFrame,
    config: ResultAnalysisConfig,
) -> pd.DataFrame:
    if list(summary.columns) != list(SUMMARY_METRIC_COLUMNS):
        raise ModelResultAnalysisError("Summary metric columns drifted from the compiled contract.")
    if len(summary) != record["rows"] or len(summary) != len(FAMILIES) * len(MODEL_IDS):
        raise ModelResultAnalysisError("Summary metric row cardinality is incomplete.")
    expected_pairs = {(family, model_id) for family in FAMILIES for model_id in MODEL_IDS}
    observed_pairs = set(
        summary.loc[:, ["family", "model_id"]].itertuples(index=False, name=None)
    )
    if observed_pairs != expected_pairs or summary.duplicated(["family", "model_id"]).any():
        raise ModelResultAnalysisError("Summary family/model coverage is incomplete or duplicated.")
    numeric_columns = [column for column in summary.columns if column not in {"family", "model_id"}]
    numeric = summary[numeric_columns].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise ModelResultAnalysisError("Summary metrics contain non-finite values.")
    result = summary.copy()
    result.loc[:, numeric_columns] = numeric
    if not result["row_count"].eq(config.expected_tract_date_row_count).all():
        raise ModelResultAnalysisError("Summary tract-date row counts drifted.")
    if not result["independent_date_count"].eq(config.expected_independent_date_count).all():
        raise ModelResultAnalysisError("Summary independent-date counts drifted.")
    if not result["independent_spatial_block_count"].eq(
        config.expected_independent_spatial_block_count
    ).all():
        raise ModelResultAnalysisError("Summary independent-block counts drifted.")

    check = oof.assign(_absolute_error=(oof["y_pred"] - oof["y_true"]).abs())
    recomputed = (
        check.groupby(["family", "model_id", "target_date"], observed=True)[
            "_absolute_error"
        ]
        .mean()
        .groupby(["family", "model_id"], observed=True)
        .mean()
    )
    observed = result.set_index(["family", "model_id"])[config.primary_metric_column]
    recomputed = recomputed.reindex(observed.index)
    if not np.allclose(
        observed.to_numpy(dtype=float),
        recomputed.to_numpy(dtype=float),
        rtol=1e-12,
        atol=1e-12,
    ):
        raise ModelResultAnalysisError("Summary primary MAE does not reproduce from OOF rows.")
    return result


def authenticate_model_results(
    config: ResultAnalysisConfig,
    evaluation_directory: str | Path | None = None,
) -> AuthenticatedModelResults:
    """Authenticate compile provenance and byte locks before reading OOF data."""

    directory = (
        config.evaluation_directory
        if evaluation_directory is None
        else Path(evaluation_directory).resolve()
    )
    provenance_path = directory / "model_run_compile_provenance.json"
    provenance = _json_object(provenance_path, name="compile provenance")
    commit = _verify_compile_commit(provenance)
    required = {
        "state": "complete",
        "ready_for_reporting": True,
        "final_test_year": config.final_test_year,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "context_row_count": config.expected_tract_date_row_count,
        "independent_date_count": config.expected_independent_date_count,
        "family_count": len(FAMILIES),
        "model_count": len(MODEL_IDS),
        "oof_prediction_row_count": (
            config.expected_tract_date_row_count * len(FAMILIES) * len(MODEL_IDS)
        ),
        "summary_metric_row_count": len(FAMILIES) * len(MODEL_IDS),
    }
    if any(provenance.get(key) != value for key, value in required.items()):
        raise ModelResultAnalysisError("Compile provenance is not a locked, complete result set.")

    output_files = provenance.get("output_files")
    oof_path, oof_record = _verified_compiled_output(
        directory, output_files, OOF_PREDICTIONS_FILENAME
    )
    summary_path, summary_record = _verified_compiled_output(
        directory, output_files, SUMMARY_METRICS_FILENAME
    )
    oof = _validate_oof(pd.read_parquet(oof_path), oof_record, config)
    summary = _validate_summary(pd.read_csv(summary_path), summary_record, oof, config)
    input_authentication = {
        "compile_provenance_path": provenance_path.as_posix(),
        "compile_provenance_file_sha256": sha256_file(provenance_path),
        "compile_provenance_commit_sha256": commit,
        "compile_run_id": provenance.get("run_id"),
        "oof_predictions_path": oof_path.as_posix(),
        "oof_predictions_sha256": oof_record["sha256"],
        "summary_metrics_path": summary_path.as_posix(),
        "summary_metrics_sha256": summary_record["sha256"],
    }
    return AuthenticatedModelResults(
        oof=oof,
        summary=summary,
        compile_provenance=provenance,
        input_authentication=input_authentication,
    )


def select_strongest_legal_baseline(
    summary: pd.DataFrame,
    *,
    family: str,
    legal_baseline_model_ids: Sequence[str] = ("B0", "B1", "B2"),
    primary_metric_column: str = "primary_equal_date_weighted_mae_c",
) -> str:
    """Select the legal baseline with minimum point MAE and a frozen tie break."""

    baselines = tuple(legal_baseline_model_ids)
    if baselines != ("B0", "B1", "B2"):
        raise ModelResultAnalysisError("Strong-baseline selection is restricted to B0-B2.")
    subset = summary.loc[
        summary["family"].eq(family) & summary["model_id"].isin(baselines),
        ["model_id", primary_metric_column],
    ].copy()
    if len(subset) != len(baselines) or set(subset["model_id"]) != set(baselines):
        raise ModelResultAnalysisError(f"Family {family!r} lacks all three legal baselines.")
    subset["_baseline_order"] = subset["model_id"].map(
        {model_id: index for index, model_id in enumerate(baselines)}
    )
    subset[primary_metric_column] = pd.to_numeric(
        subset[primary_metric_column], errors="raise"
    )
    if not np.isfinite(subset[primary_metric_column].to_numpy(dtype=float)).all():
        raise ModelResultAnalysisError("Baseline point MAE contains non-finite values.")
    selected = subset.sort_values(
        [primary_metric_column, "_baseline_order"], kind="stable"
    ).iloc[0]
    return str(selected["model_id"])


def _audit_columns(
    config: ResultAnalysisConfig,
    inputs: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "tract_date_row_count": config.expected_tract_date_row_count,
        "independent_date_count": config.expected_independent_date_count,
        "independent_spatial_block_count": config.expected_independent_spatial_block_count,
        "input_oof_sha256": inputs["oof_predictions_sha256"],
        "input_summary_metrics_sha256": inputs["summary_metrics_sha256"],
        "input_compile_provenance_file_sha256": inputs[
            "compile_provenance_file_sha256"
        ],
        "input_compile_provenance_commit_sha256": inputs[
            "compile_provenance_commit_sha256"
        ],
        "analysis_config_semantic_sha256": config.semantic_sha256,
    }


def build_point_comparison(
    summary: pd.DataFrame,
    config: ResultAnalysisConfig,
    input_authentication: Mapping[str, Any],
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Compare every model with the strongest legal baseline in its family."""

    strongest = {
        family: select_strongest_legal_baseline(
            summary,
            family=family,
            legal_baseline_model_ids=config.legal_baseline_model_ids,
            primary_metric_column=config.primary_metric_column,
        )
        for family in FAMILIES
    }
    family_order = {family: index for index, family in enumerate(FAMILIES)}
    model_order = {model_id: index for index, model_id in enumerate(MODEL_IDS)}
    result = summary.copy()
    result["_family_order"] = result["family"].map(family_order)
    result["_model_order"] = result["model_id"].map(model_order)
    result = result.sort_values(["_family_order", "_model_order"], kind="stable").drop(
        columns=["_family_order", "_model_order"]
    )
    result["model_role"] = np.where(
        result["model_id"].isin(config.legal_baseline_model_ids),
        "legal_baseline",
        "candidate_model",
    )
    result["strongest_legal_baseline_model_id"] = result["family"].map(strongest)
    lookup = summary.set_index(["family", "model_id"])[config.primary_metric_column]
    baseline_mae = np.array(
        [
            float(lookup.loc[(family, strongest[str(family)])])
            for family in result["family"]
        ],
        dtype=float,
    )
    model_mae = result[config.primary_metric_column].to_numpy(dtype=float)
    absolute = baseline_mae - model_mae
    relative = absolute / baseline_mae
    result["strongest_legal_baseline_primary_mae_c"] = baseline_mae
    result["absolute_mae_improvement_vs_strongest_baseline_c"] = absolute
    result["relative_mae_improvement_vs_strongest_baseline_fraction"] = relative
    result["relative_mae_improvement_vs_strongest_baseline_percent"] = relative * 100.0
    result["is_strongest_legal_baseline"] = [
        model_id == strongest[str(family)]
        for family, model_id in result.loc[:, ["family", "model_id"]].itertuples(
            index=False, name=None
        )
    ]
    result["selected_for_joint_bootstrap"] = result["family"].eq(
        config.target_family
    ) & (
        result["model_id"].eq(config.target_model_id)
        | result["is_strongest_legal_baseline"]
    )
    for column, value in _audit_columns(config, input_authentication).items():
        result[column] = value
    return result.reset_index(drop=True), strongest


def aggregate_paired_date_block_errors(
    oof: pd.DataFrame,
    *,
    family: str,
    target_model_id: str,
    baseline_model_id: str,
) -> pd.DataFrame:
    """Create paired date-by-block sufficient statistics before resampling."""

    required = {
        "tract_geoid",
        "target_date",
        "spatial_block",
        "family",
        "model_id",
        "y_true",
        "y_pred",
    }
    if not required.issubset(oof.columns):
        raise ModelResultAnalysisError("OOF data lack columns needed for paired aggregation.")
    if target_model_id == baseline_model_id:
        raise ModelResultAnalysisError("Target model and baseline must be different.")
    key = ["tract_geoid", "target_date"]
    keep = [*key, "spatial_block", "y_true", "y_pred"]
    target = oof.loc[
        oof["family"].eq(family) & oof["model_id"].eq(target_model_id), keep
    ].copy()
    baseline = oof.loc[
        oof["family"].eq(family) & oof["model_id"].eq(baseline_model_id), keep
    ].copy()
    if target.empty or baseline.empty:
        raise ModelResultAnalysisError("Paired comparison is missing a model surface.")
    if target.duplicated(key).any() or baseline.duplicated(key).any():
        raise ModelResultAnalysisError("Paired comparison surfaces contain duplicate keys.")
    joined = baseline.merge(
        target,
        on=key,
        how="outer",
        suffixes=("_baseline", "_target"),
        indicator=True,
        sort=False,
        validate="one_to_one",
    )
    if not joined["_merge"].eq("both").all() or len(joined) != len(target):
        raise ModelResultAnalysisError("Target and baseline OOF keys are not exactly paired.")
    if not joined["spatial_block_baseline"].astype(str).equals(
        joined["spatial_block_target"].astype(str)
    ):
        raise ModelResultAnalysisError("Paired models disagree on spatial-block identity.")
    baseline_truth = joined["y_true_baseline"].to_numpy(dtype=np.float64)
    target_truth = joined["y_true_target"].to_numpy(dtype=np.float64)
    if not np.array_equal(baseline_truth.view(np.uint64), target_truth.view(np.uint64)):
        raise ModelResultAnalysisError("Paired models disagree on bit-exact target values.")
    joined["baseline_absolute_error_c"] = np.abs(
        joined["y_pred_baseline"].to_numpy(dtype=float) - baseline_truth
    )
    joined["target_absolute_error_c"] = np.abs(
        joined["y_pred_target"].to_numpy(dtype=float) - target_truth
    )
    cells = (
        joined.groupby(
            ["target_date", "spatial_block_baseline"],
            observed=True,
            sort=True,
        )
        .agg(
            row_count=("tract_geoid", "size"),
            baseline_absolute_error_sum_c=("baseline_absolute_error_c", "sum"),
            target_absolute_error_sum_c=("target_absolute_error_c", "sum"),
        )
        .reset_index()
        .rename(columns={"spatial_block_baseline": "spatial_block"})
    )
    cells["baseline_cell_mae_c"] = (
        cells["baseline_absolute_error_sum_c"] / cells["row_count"]
    )
    cells["target_cell_mae_c"] = (
        cells["target_absolute_error_sum_c"] / cells["row_count"]
    )
    cells["paired_absolute_mae_improvement_c"] = (
        cells["baseline_cell_mae_c"] - cells["target_cell_mae_c"]
    )
    return cells


def _validate_cell_errors(cells: pd.DataFrame) -> pd.DataFrame:
    required = [
        "target_date",
        "spatial_block",
        "row_count",
        "baseline_absolute_error_sum_c",
        "target_absolute_error_sum_c",
    ]
    if not set(required).issubset(cells.columns):
        raise ModelResultAnalysisError("Crossed bootstrap requires date-by-block aggregates.")
    result = cells.loc[:, required].copy()
    if result.empty or result.duplicated(["target_date", "spatial_block"]).any():
        raise ModelResultAnalysisError(
            "Crossed bootstrap input must have one pre-aggregated row per date-by-block cell."
        )
    result["target_date"] = pd.to_datetime(result["target_date"], errors="raise")
    numeric = result[
        [
            "row_count",
            "baseline_absolute_error_sum_c",
            "target_absolute_error_sum_c",
        ]
    ].apply(pd.to_numeric, errors="raise")
    if (
        not np.isfinite(numeric.to_numpy(dtype=float)).all()
        or numeric["row_count"].le(0).any()
        or numeric[["baseline_absolute_error_sum_c", "target_absolute_error_sum_c"]]
        .lt(0)
        .any()
        .any()
    ):
        raise ModelResultAnalysisError("Date-by-block sufficient statistics are invalid.")
    result.loc[:, numeric.columns] = numeric
    return result


def crossed_date_spatial_block_bootstrap(
    cells: pd.DataFrame,
    *,
    seed: int,
    replicates: int,
    confidence_level: float,
    method: str = BOOTSTRAP_METHOD,
    sampling_unit: str = BOOTSTRAP_SAMPLING_UNIT,
    probability_threshold_fraction: float = 0.10,
) -> dict[str, Any]:
    """Run a paired crossed bootstrap over complete dates and complete blocks.

    Each replicate independently samples ``D`` whole dates and ``B`` whole
    spatial blocks with replacement.  Their multiplicities are crossed and
    applied to pre-aggregated cell sums for both models.  No tract-date row is
    ever selected independently.
    """

    if method != BOOTSTRAP_METHOD or sampling_unit != BOOTSTRAP_SAMPLING_UNIT:
        raise ModelResultAnalysisError("Random-row or non-crossed bootstrap is forbidden.")
    seed = _integer(seed, name="bootstrap seed", minimum=0)
    replicates = _integer(replicates, name="bootstrap replicates")
    confidence_level = _finite_number(confidence_level, name="confidence level")
    probability_threshold_fraction = _finite_number(
        probability_threshold_fraction, name="probability threshold"
    )
    if not 0 < confidence_level < 1:
        raise ModelResultAnalysisError("confidence_level must be strictly between zero and one.")
    if probability_threshold_fraction < 0:
        raise ModelResultAnalysisError("probability threshold cannot be negative.")

    frame = _validate_cell_errors(cells)
    dates = pd.Index(sorted(frame["target_date"].unique()))
    blocks = pd.Index(sorted(frame["spatial_block"].astype(str).unique()))
    date_index = {value: index for index, value in enumerate(dates)}
    block_index = {value: index for index, value in enumerate(blocks)}
    shape = (len(dates), len(blocks))
    counts = np.zeros(shape, dtype=float)
    baseline_sums = np.zeros(shape, dtype=float)
    target_sums = np.zeros(shape, dtype=float)
    for row in frame.itertuples(index=False):
        i = date_index[row.target_date]
        j = block_index[str(row.spatial_block)]
        counts[i, j] = float(row.row_count)
        baseline_sums[i, j] = float(row.baseline_absolute_error_sum_c)
        target_sums[i, j] = float(row.target_absolute_error_sum_c)

    date_denominators = counts.sum(axis=1)
    if np.any(date_denominators <= 0):
        raise ModelResultAnalysisError("Every independent date must contain observations.")
    point_baseline = float(np.mean(baseline_sums.sum(axis=1) / date_denominators))
    point_target = float(np.mean(target_sums.sum(axis=1) / date_denominators))
    point_absolute = point_baseline - point_target
    point_relative = point_absolute / point_baseline

    rng = np.random.default_rng(seed)
    date_weights = rng.multinomial(
        len(dates), np.full(len(dates), 1.0 / len(dates)), size=replicates
    ).astype(float)
    block_weights = rng.multinomial(
        len(blocks), np.full(len(blocks), 1.0 / len(blocks)), size=replicates
    ).astype(float)
    sampled_counts = np.einsum("db,rb->rd", counts, block_weights, optimize=True)
    sampled_baseline_sums = np.einsum(
        "db,rb->rd", baseline_sums, block_weights, optimize=True
    )
    sampled_target_sums = np.einsum(
        "db,rb->rd", target_sums, block_weights, optimize=True
    )
    valid = sampled_counts > 0
    valid_date_weights = date_weights * valid
    replicate_date_weight = valid_date_weights.sum(axis=1)
    if np.any(replicate_date_weight <= 0):
        raise ModelResultAnalysisError("A crossed bootstrap replicate contains no observations.")
    baseline_date_mae = np.divide(
        sampled_baseline_sums,
        sampled_counts,
        out=np.zeros_like(sampled_baseline_sums),
        where=valid,
    )
    target_date_mae = np.divide(
        sampled_target_sums,
        sampled_counts,
        out=np.zeros_like(sampled_target_sums),
        where=valid,
    )
    replicate_baseline = (
        (baseline_date_mae * valid_date_weights).sum(axis=1) / replicate_date_weight
    )
    replicate_target = (
        (target_date_mae * valid_date_weights).sum(axis=1) / replicate_date_weight
    )
    replicate_absolute = replicate_baseline - replicate_target
    replicate_relative = replicate_absolute / replicate_baseline
    alpha = (1.0 - confidence_level) / 2.0
    absolute_ci = np.quantile(replicate_absolute, [alpha, 1.0 - alpha], method="linear")
    relative_ci = np.quantile(replicate_relative, [alpha, 1.0 - alpha], method="linear")
    # ``valid`` concerns crossed cells, not omitted cluster draws.  It is retained
    # as an audit count because sparse date-by-block support can create a sampled
    # date with no observations after block resampling.
    zero_observation_date_draws = int(((date_weights > 0) & ~valid).sum())

    return {
        "bootstrap_method": BOOTSTRAP_METHOD,
        "bootstrap_sampling_unit": BOOTSTRAP_SAMPLING_UNIT,
        "bootstrap_estimand": "equal_date_weighted_mae_with_row_weighting_within_date",
        "date_block_cell_aggregation": "row_count_and_paired_absolute_error_sums",
        "complete_date_resampling": True,
        "complete_spatial_block_resampling": True,
        "date_and_block_draws_independent": True,
        "bootstrap_seed": seed,
        "bootstrap_replicates": replicates,
        "confidence_level": confidence_level,
        "percentile_interval_method": "linear",
        "paired_models_share_every_cluster_draw": True,
        "random_row_sampling_used": False,
        "date_block_cell_count": len(frame),
        "independent_date_count": len(dates),
        "independent_spatial_block_count": len(blocks),
        "tract_date_row_count": int(frame["row_count"].sum()),
        "zero_observation_sampled_date_draw_count": zero_observation_date_draws,
        "baseline_point_mae_c": point_baseline,
        "target_model_point_mae_c": point_target,
        "absolute_mae_improvement_c": point_absolute,
        "absolute_mae_improvement_ci_lower_c": float(absolute_ci[0]),
        "absolute_mae_improvement_ci_upper_c": float(absolute_ci[1]),
        "relative_mae_improvement_fraction": point_relative,
        "relative_mae_improvement_percent": point_relative * 100.0,
        "relative_mae_improvement_ci_lower_fraction": float(relative_ci[0]),
        "relative_mae_improvement_ci_upper_fraction": float(relative_ci[1]),
        "relative_mae_improvement_ci_lower_percent": float(relative_ci[0] * 100.0),
        "relative_mae_improvement_ci_upper_percent": float(relative_ci[1] * 100.0),
        "probability_improvement_gt_zero": float(np.mean(replicate_absolute > 0.0)),
        "probability_relative_improvement_gt_10_percent": float(
            np.mean(replicate_relative > probability_threshold_fraction)
        ),
    }


def build_protocol_success_gates(
    *,
    target_summary: Mapping[str, Any],
    bootstrap: Mapping[str, Any],
    config: ResultAnalysisConfig,
    input_authentication: Mapping[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Evaluate the predeclared point and uncertainty gates without moving them."""

    spearman = float(target_summary[config.spearman_metric_column])
    point_relative = float(bootstrap["relative_mae_improvement_fraction"])
    relative_ci_lower = float(bootstrap["relative_mae_improvement_ci_lower_fraction"])
    spearman_pass = spearman >= config.minimum_median_per_date_spearman
    point_pass = point_relative >= config.minimum_relative_mae_improvement_fraction
    uncertainty_pass = (
        relative_ci_lower > config.uncertainty_relative_ci_lower_must_exceed
    )
    ten_percent_ci_supported = (
        relative_ci_lower > config.minimum_relative_mae_improvement_fraction
    )
    overall = bool(spearman_pass and point_pass and uncertainty_pass)
    rows = [
        {
            "gate_id": "median_per_date_spearman",
            "observed_value": spearman,
            "threshold": config.minimum_median_per_date_spearman,
            "comparison": ">=",
            "passed": bool(spearman_pass),
            "required_for_protocol_success": True,
            "interpretation": "M2 joint median per-date Spearman must reach 0.50.",
        },
        {
            "gate_id": "point_relative_mae_improvement",
            "observed_value": point_relative,
            "threshold": config.minimum_relative_mae_improvement_fraction,
            "comparison": ">=",
            "passed": bool(point_pass),
            "required_for_protocol_success": True,
            "interpretation": "Point improvement over the strongest legal baseline must reach 10%.",
        },
        {
            "gate_id": "uncertainty_supports_positive_improvement",
            "observed_value": relative_ci_lower,
            "threshold": config.uncertainty_relative_ci_lower_must_exceed,
            "comparison": ">",
            "passed": bool(uncertainty_pass),
            "required_for_protocol_success": True,
            "interpretation": "The 95% relative-improvement CI lower bound must exceed zero.",
        },
        {
            "gate_id": "uncertainty_supports_full_ten_percent_improvement",
            "observed_value": relative_ci_lower,
            "threshold": config.minimum_relative_mae_improvement_fraction,
            "comparison": ">",
            "passed": bool(ten_percent_ci_supported),
            "required_for_protocol_success": False,
            "interpretation": (
                "Stronger descriptive check: whether the 95% CI itself clears 10%; "
                "this is reported separately and is not substituted for the protocol gate."
            ),
        },
    ]
    table = pd.DataFrame(rows)
    table["overall_protocol_success_gate_pass"] = overall
    for column, value in _audit_columns(config, input_authentication).items():
        table[column] = value
    status = {
        "minimum_median_per_date_spearman": config.minimum_median_per_date_spearman,
        "observed_median_per_date_spearman": spearman,
        "spearman_gate_pass": bool(spearman_pass),
        "minimum_point_relative_mae_improvement_fraction": (
            config.minimum_relative_mae_improvement_fraction
        ),
        "observed_point_relative_mae_improvement_fraction": point_relative,
        "point_improvement_gate_pass": bool(point_pass),
        "observed_relative_improvement_ci_lower_fraction": relative_ci_lower,
        "uncertainty_gate_definition": "relative_improvement_ci_lower_fraction > 0",
        "uncertainty_gate_pass": bool(uncertainty_pass),
        "ten_percent_threshold_ci_definition": (
            "relative_improvement_ci_lower_fraction > 0.10"
        ),
        "ten_percent_threshold_ci_supported": bool(ten_percent_ci_supported),
        "overall_protocol_success_gate_pass": overall,
    }
    return table, status


def _csv_file_record(path: Path, rows: int) -> dict[str, Any]:
    return {
        "path": path.name,
        "path_base": "output_directory",
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "rows": int(rows),
    }


def _json_file_record(path: Path) -> dict[str, Any]:
    return {
        "path": path.name,
        "path_base": "output_directory",
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def _summary_payload(
    *,
    config: ResultAnalysisConfig,
    authenticated: AuthenticatedModelResults,
    strongest: Mapping[str, str],
    baseline_model_id: str,
    bootstrap: Mapping[str, Any],
    gate_status: Mapping[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": RESULT_ANALYSIS_SCHEMA_VERSION,
        "algorithm_version": RESULT_ANALYSIS_ALGORITHM_VERSION,
        "state": "complete",
        "analysis_scope": "locked_2020_2024_development_oof_only",
        "final_test_year": config.final_test_year,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "tract_date_row_count": config.expected_tract_date_row_count,
        "independent_date_count": config.expected_independent_date_count,
        "independent_spatial_block_count": (
            config.expected_independent_spatial_block_count
        ),
        "input_authentication": dict(authenticated.input_authentication),
        "analysis_config_semantic_sha256": config.semantic_sha256,
        "strongest_legal_baseline_by_family": dict(strongest),
        "primary_comparison": {
            "family": config.target_family,
            "target_model_id": config.target_model_id,
            "strongest_legal_baseline_model_id": baseline_model_id,
            **dict(bootstrap),
        },
        "protocol_success_gates": dict(gate_status),
        "scientific_contract": {
            "primary_metric": config.primary_metric_column,
            "baseline_candidates": list(config.legal_baseline_model_ids),
            "baseline_selection": (
                "minimum point primary MAE within family; B0-B2 order breaks ties"
            ),
            "bootstrap_unit": "crossed complete dates and complete spatial blocks",
            "date_block_aggregation_precedes_bootstrap": True,
            "paired_models_share_cluster_draws": True,
            "random_row_bootstrap_used": False,
            "models_fitted": False,
            "outer_fragments_read": False,
            "final_test_unlocked": False,
        },
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def analyze_model_results(
    config_path: str | Path = DEFAULT_RESULT_ANALYSIS_CONFIG,
    *,
    evaluation_directory: str | Path | None = None,
    output_directory: str | Path | None = None,
) -> dict[str, Any]:
    """Generate initial authenticated model tables and a committed provenance marker."""

    config = load_result_analysis_config(config_path)
    authenticated = authenticate_model_results(config, evaluation_directory)
    point_table, strongest = build_point_comparison(
        authenticated.summary,
        config,
        authenticated.input_authentication,
    )
    baseline_model_id = strongest[config.target_family]
    cells = aggregate_paired_date_block_errors(
        authenticated.oof,
        family=config.target_family,
        target_model_id=config.target_model_id,
        baseline_model_id=baseline_model_id,
    )
    bootstrap = crossed_date_spatial_block_bootstrap(
        cells,
        seed=config.bootstrap_seed,
        replicates=config.bootstrap_replicates,
        confidence_level=config.confidence_level,
        method=config.bootstrap_method,
        sampling_unit=config.bootstrap_sampling_unit,
        probability_threshold_fraction=config.minimum_relative_mae_improvement_fraction,
    )
    expected_bootstrap_counts = {
        "tract_date_row_count": config.expected_tract_date_row_count,
        "independent_date_count": config.expected_independent_date_count,
        "independent_spatial_block_count": config.expected_independent_spatial_block_count,
    }
    if any(bootstrap[key] != value for key, value in expected_bootstrap_counts.items()):
        raise ModelResultAnalysisError("Bootstrap independent-unit counts drifted.")

    summary_index = authenticated.summary.set_index(["family", "model_id"])
    baseline_summary = float(
        summary_index.loc[(config.target_family, baseline_model_id), config.primary_metric_column]
    )
    target_summary = summary_index.loc[(config.target_family, config.target_model_id)]
    target_point = float(target_summary[config.primary_metric_column])
    if not (
        math.isclose(
            baseline_summary,
            float(bootstrap["baseline_point_mae_c"]),
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
        and math.isclose(
            target_point,
            float(bootstrap["target_model_point_mae_c"]),
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
    ):
        raise ModelResultAnalysisError("Bootstrap cells do not reproduce authenticated point MAE.")

    bootstrap_row = {
        "family": config.target_family,
        "target_model_id": config.target_model_id,
        "strongest_legal_baseline_model_id": baseline_model_id,
        **bootstrap,
        **_audit_columns(config, authenticated.input_authentication),
    }
    bootstrap_table = pd.DataFrame([bootstrap_row])
    gate_table, gate_status = build_protocol_success_gates(
        target_summary=target_summary.to_dict(),
        bootstrap=bootstrap,
        config=config,
        input_authentication=authenticated.input_authentication,
    )
    summary_payload = _summary_payload(
        config=config,
        authenticated=authenticated,
        strongest=strongest,
        baseline_model_id=baseline_model_id,
        bootstrap=bootstrap,
        gate_status=gate_status,
    )

    pipeline_sha256, pipeline_fingerprint = code_runtime_fingerprint(
        project_root=_project_root(),
        relative_paths=_PIPELINE_PATHS,
        algorithm_version=RESULT_ANALYSIS_ALGORITHM_VERSION,
    )
    output = (
        config.output_directory if output_directory is None else Path(output_directory).resolve()
    )
    output.mkdir(parents=True, exist_ok=True)
    point_path = output / POINT_COMPARISON_FILENAME
    bootstrap_path = output / BOOTSTRAP_FILENAME
    gates_path = output / SUCCESS_GATES_FILENAME
    summary_path = output / SUMMARY_FILENAME
    atomic_csv(point_table, point_path)
    atomic_csv(bootstrap_table, bootstrap_path)
    atomic_csv(gate_table, gates_path)
    atomic_json(summary_payload, summary_path)
    output_files = {
        POINT_COMPARISON_FILENAME: _csv_file_record(point_path, len(point_table)),
        BOOTSTRAP_FILENAME: _csv_file_record(bootstrap_path, len(bootstrap_table)),
        SUCCESS_GATES_FILENAME: _csv_file_record(gates_path, len(gate_table)),
        SUMMARY_FILENAME: _json_file_record(summary_path),
    }
    provenance: dict[str, Any] = {
        "schema_version": RESULT_ANALYSIS_SCHEMA_VERSION,
        "algorithm_version": RESULT_ANALYSIS_ALGORITHM_VERSION,
        "state": "complete",
        "ready_for_result_interpretation": True,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "analysis_scope": "locked_2020_2024_development_oof_only",
        "final_test_year": config.final_test_year,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "tract_date_row_count": config.expected_tract_date_row_count,
        "independent_date_count": config.expected_independent_date_count,
        "independent_spatial_block_count": (
            config.expected_independent_spatial_block_count
        ),
        "input_authentication": dict(authenticated.input_authentication),
        "compile_provenance_commit_sha256": authenticated.input_authentication[
            "compile_provenance_commit_sha256"
        ],
        "analysis_config": {
            "path": config.path.as_posix(),
            "file_sha256": sha256_file(config.path),
            "semantic_sha256": config.semantic_sha256,
        },
        "pipeline_sha256": pipeline_sha256,
        "pipeline_fingerprint": pipeline_fingerprint,
        "comparison": {
            "family": config.target_family,
            "target_model_id": config.target_model_id,
            "strongest_legal_baseline_model_id": baseline_model_id,
        },
        "bootstrap_contract": {
            "method": config.bootstrap_method,
            "sampling_unit": config.bootstrap_sampling_unit,
            "estimand": "equal_date_weighted_mae_with_row_weighting_within_date",
            "date_block_cell_aggregation": "row_count_and_paired_absolute_error_sums",
            "complete_date_resampling": True,
            "complete_spatial_block_resampling": True,
            "date_and_block_draws_independent": True,
            "seed": config.bootstrap_seed,
            "replicates": config.bootstrap_replicates,
            "confidence_level": config.confidence_level,
            "date_block_aggregation_precedes_bootstrap": True,
            "paired_models_share_every_cluster_draw": True,
            "random_row_sampling_used": False,
        },
        "protocol_success_gates": gate_status,
        "output_files": output_files,
    }
    provenance["commit_sha256"] = canonical_sha256(provenance)
    atomic_json(provenance, output / PROVENANCE_FILENAME)
    return provenance
