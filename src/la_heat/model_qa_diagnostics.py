"""Authenticated QA, missingness, and failure-case diagnostics for development OOF."""

from __future__ import annotations

import json
import math
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from la_heat.model_result_analysis import (
    BOOTSTRAP_METHOD,
    BOOTSTRAP_SAMPLING_UNIT,
    authenticate_model_results,
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

QA_DIAGNOSTIC_SCHEMA_VERSION: Final = 1
QA_DIAGNOSTIC_ALGORITHM_VERSION: Final = "model-qa-diagnostics-v1"
QA_DIAGNOSTIC_STATE: Final = "frozen_development_diagnostics"
DEFAULT_QA_DIAGNOSTIC_CONFIG: Final = Path("configs/model_qa_diagnostics.toml")
COHORT_METRICS_FILENAME: Final = "qa_cohort_metrics.csv"
COHORT_IMPROVEMENT_FILENAME: Final = "qa_cohort_improvement.csv"
COHORT_BOOTSTRAP_FILENAME: Final = "qa_cohort_crossed_bootstrap.csv"
WORST_DATES_FILENAME: Final = "m2_worst_dates.csv"
WORST_TRACTS_FILENAME: Final = "m2_worst_tracts.csv"
SUMMARY_FILENAME: Final = "model_qa_diagnostics_summary.json"
PROVENANCE_FILENAME: Final = "model_qa_diagnostics_provenance.json"
EXPECTED_SENTINEL_FEATURES: Final = (
    "sentinel_ndvi_lag60",
    "sentinel_evi_lag60",
    "sentinel_ndwi_lag60",
    "sentinel_ndbi_lag60",
    "sentinel_albedo_proxy_lag60",
)
PIPELINE_FILES: Final = (
    "scripts/analyze_model_qa.py",
    "src/la_heat/model_qa_diagnostics.py",
    "src/la_heat/model_result_analysis.py",
    "src/la_heat/provenance.py",
)


class ModelQADiagnosticError(ValueError):
    """Raised when a diagnostic input or frozen analysis contract is invalid."""


@dataclass(frozen=True, slots=True)
class ModelQADiagnosticConfig:
    path: Path
    semantic_sha256: str
    result_analysis_config: Path
    target_build_progress: Path
    model_ready_targets: Path
    date_summary: Path
    model_dataset_provenance: Path
    model_table: Path
    target_inventory_summary: Path
    scene_inventory: Path
    output_directory: Path
    final_test_year: int
    family: str
    baseline_model_id: str
    target_model_id: str
    expected_rows: int
    expected_dates: int
    expected_blocks: int
    st_uncertainty_threshold_k: float
    low_scene_cloud_threshold_percent: float
    valid_fraction_breaks: tuple[float, ...]
    cloud_distance_breaks_km: tuple[float, ...]
    failure_case_limit: int
    sentinel_feature_names: tuple[str, ...]
    bootstrap_method: str
    bootstrap_sampling_unit: str
    bootstrap_seed: int
    bootstrap_replicates: int
    bootstrap_confidence_level: float


@dataclass(frozen=True, slots=True)
class AuthenticatedQADiagnosticInputs:
    frame: pd.DataFrame
    input_records: dict[str, Any]
    compile_commit_sha256: str
    model_dataset_commit_sha256: str


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _exact_keys(value: object, expected: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        observed = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise ModelQADiagnosticError(
            f"{label} keys must be exactly {sorted(expected)}; got {observed}."
        )
    return value


def _resolve_path(value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ModelQADiagnosticError(f"{label} must be a nonempty path string.")
    path = Path(value)
    return (path if path.is_absolute() else _project_root() / path).resolve()


def _finite(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelQADiagnosticError(f"{label} must be numeric.")
    result = float(value)
    if not math.isfinite(result):
        raise ModelQADiagnosticError(f"{label} must be finite.")
    return result


def _integer(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ModelQADiagnosticError(f"{label} must be a positive integer.")
    return value


def _strict_breaks(value: object, *, label: str) -> tuple[float, ...]:
    if not isinstance(value, list) or len(value) < 2:
        raise ModelQADiagnosticError(f"{label} must contain at least two breaks.")
    result = tuple(_finite(item, label=label) for item in value)
    if any(right <= left for left, right in zip(result, result[1:], strict=False)):
        raise ModelQADiagnosticError(f"{label} must be strictly increasing.")
    return result


def load_model_qa_diagnostic_config(
    path: str | Path = DEFAULT_QA_DIAGNOSTIC_CONFIG,
) -> ModelQADiagnosticConfig:
    """Load and fail closed on drift in the post-score diagnostic contract."""

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
        },
        label="QA diagnostic config",
    )
    if (
        raw["schema_version"] != QA_DIAGNOSTIC_SCHEMA_VERSION
        or raw["algorithm_version"] != QA_DIAGNOSTIC_ALGORITHM_VERSION
        or raw["state"] != QA_DIAGNOSTIC_STATE
    ):
        raise ModelQADiagnosticError("QA diagnostic config identity drifted.")
    paths = _exact_keys(
        raw["paths"],
        {
            "result_analysis_config",
            "target_build_progress",
            "model_ready_targets",
            "date_summary",
            "model_dataset_provenance",
            "model_table",
            "target_inventory_summary",
            "scene_inventory",
            "output_directory",
        },
        label="paths",
    )
    analysis = _exact_keys(
        raw["analysis"],
        {
            "final_test_year",
            "final_test_locked",
            "family",
            "baseline_model_id",
            "target_model_id",
            "expected_tract_date_row_count",
            "expected_independent_date_count",
            "expected_independent_spatial_block_count",
            "st_uncertainty_threshold_k",
            "low_scene_cloud_threshold_percent",
            "valid_fraction_breaks",
            "cloud_distance_breaks_km",
            "failure_case_limit",
            "sentinel_feature_names",
        },
        label="analysis",
    )
    bootstrap = _exact_keys(
        raw["bootstrap"],
        {"method", "sampling_unit", "seed", "replicates", "confidence_level"},
        label="bootstrap",
    )
    if (
        analysis["final_test_year"] != 2025
        or analysis["final_test_locked"] is not True
        or analysis["family"] != "joint"
        or analysis["baseline_model_id"] != "B1"
        or analysis["target_model_id"] != "M2"
    ):
        raise ModelQADiagnosticError("The joint B1/M2 diagnostic or 2025 lock drifted.")
    counts = (
        _integer(analysis["expected_tract_date_row_count"], label="expected rows"),
        _integer(analysis["expected_independent_date_count"], label="expected dates"),
        _integer(
            analysis["expected_independent_spatial_block_count"],
            label="expected blocks",
        ),
    )
    if counts != (63_403, 65, 71):
        raise ModelQADiagnosticError("Development cardinalities drifted.")
    sentinel = analysis["sentinel_feature_names"]
    if not isinstance(sentinel, list) or tuple(sentinel) != EXPECTED_SENTINEL_FEATURES:
        raise ModelQADiagnosticError("Sentinel missingness feature set drifted.")
    valid_breaks = _strict_breaks(
        analysis["valid_fraction_breaks"], label="valid fraction breaks"
    )
    cloud_breaks = _strict_breaks(
        analysis["cloud_distance_breaks_km"], label="cloud distance breaks"
    )
    if valid_breaks != (0.60, 0.70, 0.80, 0.90, 1.0000001):
        raise ModelQADiagnosticError("Valid-fraction strata drifted.")
    if cloud_breaks != (1.0, 2.0, 5.0, 1_000_000.0):
        raise ModelQADiagnosticError("Cloud-distance strata drifted.")
    st_threshold = _finite(
        analysis["st_uncertainty_threshold_k"], label="ST uncertainty threshold"
    )
    cloud_threshold = _finite(
        analysis["low_scene_cloud_threshold_percent"],
        label="scene cloud threshold",
    )
    if st_threshold != 2.0 or cloud_threshold != 15.0:
        raise ModelQADiagnosticError("Predeclared QA sensitivity threshold drifted.")
    bootstrap_seed = _integer(bootstrap["seed"], label="bootstrap seed")
    bootstrap_replicates = _integer(
        bootstrap["replicates"], label="bootstrap replicates"
    )
    bootstrap_confidence = _finite(
        bootstrap["confidence_level"], label="bootstrap confidence level"
    )
    if (
        bootstrap["method"] != BOOTSTRAP_METHOD
        or bootstrap["sampling_unit"] != BOOTSTRAP_SAMPLING_UNIT
        or bootstrap_seed != 20_260_723
        or bootstrap_replicates != 5_000
        or bootstrap_confidence != 0.95
    ):
        raise ModelQADiagnosticError("Crossed QA bootstrap contract drifted.")
    return ModelQADiagnosticConfig(
        path=config_path,
        semantic_sha256=canonical_sha256(raw),
        result_analysis_config=_resolve_path(
            paths["result_analysis_config"], label="result analysis config"
        ),
        target_build_progress=_resolve_path(
            paths["target_build_progress"], label="target build progress"
        ),
        model_ready_targets=_resolve_path(
            paths["model_ready_targets"], label="model-ready targets"
        ),
        date_summary=_resolve_path(paths["date_summary"], label="date summary"),
        model_dataset_provenance=_resolve_path(
            paths["model_dataset_provenance"], label="model dataset provenance"
        ),
        model_table=_resolve_path(paths["model_table"], label="model table"),
        target_inventory_summary=_resolve_path(
            paths["target_inventory_summary"], label="inventory summary"
        ),
        scene_inventory=_resolve_path(
            paths["scene_inventory"], label="scene inventory"
        ),
        output_directory=_resolve_path(
            paths["output_directory"], label="output directory"
        ),
        final_test_year=2025,
        family="joint",
        baseline_model_id="B1",
        target_model_id="M2",
        expected_rows=counts[0],
        expected_dates=counts[1],
        expected_blocks=counts[2],
        st_uncertainty_threshold_k=st_threshold,
        low_scene_cloud_threshold_percent=cloud_threshold,
        valid_fraction_breaks=valid_breaks,
        cloud_distance_breaks_km=cloud_breaks,
        failure_case_limit=_integer(
            analysis["failure_case_limit"], label="failure-case limit"
        ),
        sentinel_feature_names=tuple(sentinel),
        bootstrap_method=str(bootstrap["method"]),
        bootstrap_sampling_unit=str(bootstrap["sampling_unit"]),
        bootstrap_seed=bootstrap_seed,
        bootstrap_replicates=bootstrap_replicates,
        bootstrap_confidence_level=bootstrap_confidence,
    )


def _read_json(path: Path, *, label: str) -> tuple[dict[str, Any], str]:
    before = sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ModelQADiagnosticError(f"Cannot read {label}: {path}") from error
    if sha256_file(path) != before or not isinstance(payload, dict):
        raise ModelQADiagnosticError(f"{label} changed or is not a JSON object.")
    return payload, before


def _verify_commit(payload: dict[str, Any], *, label: str) -> str:
    working = dict(payload)
    recorded = working.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(working) != recorded:
        raise ModelQADiagnosticError(f"{label} commit is invalid.")
    return recorded


def _verify_parquet(path: Path, record: object, *, label: str) -> pd.DataFrame:
    if not isinstance(record, dict):
        raise ModelQADiagnosticError(f"{label} lock is missing.")
    required = {"sha256", "bytes", "rows", "schema_sha256"}
    if not required.issubset(record):
        raise ModelQADiagnosticError(f"{label} lock is incomplete.")
    if (
        not path.is_file()
        or path.stat().st_size != int(record["bytes"])
        or sha256_file(path) != record["sha256"]
    ):
        raise ModelQADiagnosticError(f"{label} byte lock failed.")
    frame = pd.read_parquet(path)
    actual = parquet_file_record(path, frame)
    if any(actual[field] != record[field] for field in required):
        raise ModelQADiagnosticError(f"{label} row/schema lock failed.")
    return frame


def _bit_exact(left: pd.Series, right: pd.Series) -> bool:
    left_values = left.to_numpy(dtype=np.float64)
    right_values = right.to_numpy(dtype=np.float64)
    return np.array_equal(left_values.view(np.uint64), right_values.view(np.uint64))


def validate_development_diagnostic_frame(
    frame: pd.DataFrame,
    config: ModelQADiagnosticConfig,
) -> None:
    """Reject incomplete keys, nonfinite scores, or any locked final-test row."""

    required = {
        "tract_geoid",
        "target_date",
        "spatial_block",
        "y_true",
        "b1_y_pred",
        "m2_y_pred",
    }
    if not required.issubset(frame):
        raise ModelQADiagnosticError("Diagnostic frame lacks required OOF fields.")
    dates = pd.to_datetime(frame["target_date"], errors="raise")
    numeric = frame[["y_true", "b1_y_pred", "m2_y_pred"]].apply(
        pd.to_numeric, errors="raise"
    )
    if (
        len(frame) != config.expected_rows
        or frame.duplicated(["tract_geoid", "target_date"]).any()
        or dates.nunique() != config.expected_dates
        or frame["spatial_block"].nunique() != config.expected_blocks
        or dates.dt.year.ge(config.final_test_year).any()
        or not np.isfinite(numeric.to_numpy(dtype=float)).all()
    ):
        raise ModelQADiagnosticError(
            "Diagnostic frame cardinality, score, or final-test lock failed."
        )


def authenticate_qa_diagnostic_inputs(
    config: ModelQADiagnosticConfig,
) -> AuthenticatedQADiagnosticInputs:
    """Authenticate all score, target-QA, feature-missingness, and inventory inputs."""

    result_config = load_result_analysis_config(config.result_analysis_config)
    authenticated = authenticate_model_results(result_config)
    oof = authenticated.oof.loc[
        authenticated.oof["family"].eq(config.family)
        & authenticated.oof["model_id"].isin(
            [config.baseline_model_id, config.target_model_id]
        )
    ].copy()
    surfaces: dict[str, pd.DataFrame] = {}
    for model_id in (config.baseline_model_id, config.target_model_id):
        surface = oof.loc[
            oof["model_id"].eq(model_id),
            ["tract_geoid", "target_date", "spatial_block", "y_true", "y_pred"],
        ].copy()
        if len(surface) != config.expected_rows or surface.duplicated(
            ["tract_geoid", "target_date"]
        ).any():
            raise ModelQADiagnosticError(f"{model_id} OOF surface is incomplete.")
        surfaces[model_id] = surface
    paired = surfaces[config.baseline_model_id].merge(
        surfaces[config.target_model_id],
        on=["tract_geoid", "target_date"],
        how="outer",
        suffixes=("_baseline", "_target"),
        validate="one_to_one",
        indicator=True,
    )
    if (
        not paired["_merge"].eq("both").all()
        or not paired["spatial_block_baseline"].astype(str).equals(
            paired["spatial_block_target"].astype(str)
        )
        or not _bit_exact(paired["y_true_baseline"], paired["y_true_target"])
    ):
        raise ModelQADiagnosticError("B1 and M2 OOF surfaces are not exactly paired.")
    paired = paired.rename(
        columns={
            "spatial_block_baseline": "spatial_block",
            "y_true_baseline": "y_true",
            "y_pred_baseline": "b1_y_pred",
            "y_pred_target": "m2_y_pred",
        }
    ).drop(
        columns=["spatial_block_target", "y_true_target", "_merge"]
    )

    progress, progress_sha = _read_json(
        config.target_build_progress, label="target build progress"
    )
    if (
        progress.get("state") != "model_ready"
        or progress.get("promoted_outputs_valid") is not True
        or progress.get("build_complete") is not True
        or progress.get("usable_overpass_count") != config.expected_dates
    ):
        raise ModelQADiagnosticError("Target build is not the frozen model-ready state.")
    locks = progress.get("aggregate_outputs")
    if not isinstance(locks, dict):
        raise ModelQADiagnosticError("Target aggregate locks are missing.")
    targets = _verify_parquet(
        config.model_ready_targets,
        locks.get(config.model_ready_targets.name),
        label="model-ready targets",
    )
    dates = _verify_parquet(
        config.date_summary,
        locks.get(config.date_summary.name),
        label="date summary",
    )

    model_provenance, model_provenance_sha = _read_json(
        config.model_dataset_provenance, label="model dataset provenance"
    )
    model_commit = _verify_commit(model_provenance, label="Model dataset provenance")
    if (
        model_provenance.get("state") != "complete"
        or model_provenance.get("ready_for_modeling") is not True
        or model_provenance.get("final_test_unlocked") is not False
        or model_provenance.get("contains_final_test_year") is not False
        or model_provenance.get("row_count") != config.expected_rows
    ):
        raise ModelQADiagnosticError("Model dataset provenance is not locked development data.")
    output_locks = model_provenance.get("output_files")
    if not isinstance(output_locks, dict):
        raise ModelQADiagnosticError("Model dataset output locks are missing.")
    model_table = _verify_parquet(
        config.model_table,
        output_locks.get(config.model_table.name),
        label="development model table",
    )

    inventory, inventory_sha = _read_json(
        config.target_inventory_summary, label="target inventory summary"
    )
    if (
        inventory.get("final_test_year") != config.final_test_year
        or inventory.get("final_test_unlocked") is not False
        or inventory.get("scene_inventory_file_sha256")
        != sha256_file(config.scene_inventory)
    ):
        raise ModelQADiagnosticError("Target scene inventory lock failed.")
    scene_inventory = pd.read_csv(config.scene_inventory)
    required_scene_columns = {"local_date", "cloud_cover_percent"}
    if not required_scene_columns.issubset(scene_inventory):
        raise ModelQADiagnosticError("Scene inventory lacks cloud sensitivity fields.")
    scene_inventory["local_date"] = pd.to_datetime(
        scene_inventory["local_date"], errors="raise"
    )
    low_cloud_dates = set(
        scene_inventory.loc[
            pd.to_numeric(scene_inventory["cloud_cover_percent"], errors="raise")
            < config.low_scene_cloud_threshold_percent,
            "local_date",
        ]
    )

    target_columns = [
        "tract_geoid",
        "target_date",
        "spatial_block",
        "platform",
        "valid_fraction",
        "median_st_uncertainty_k",
        "median_cloud_distance_km",
        "relative_hotspot_top20",
        "target_lst_c",
    ]
    if not set(target_columns).issubset(targets):
        raise ModelQADiagnosticError("Model-ready target QA schema is incomplete.")
    targets = targets.loc[:, target_columns].copy()
    targets["target_date"] = pd.to_datetime(targets["target_date"], errors="raise")
    if (
        len(targets) != config.expected_rows
        or targets.duplicated(["tract_geoid", "target_date"]).any()
        or targets["target_date"].dt.year.ge(config.final_test_year).any()
    ):
        raise ModelQADiagnosticError("Model-ready target keys or 2025 lock failed.")
    date_fields = dates.loc[
        :, ["target_date", "retained_tract_fraction", "relative_endpoint_coverage_pass"]
    ].copy()
    date_fields["target_date"] = pd.to_datetime(date_fields["target_date"], errors="raise")
    date_fields = date_fields.loc[
        date_fields["target_date"].isin(targets["target_date"].unique())
    ]
    if len(date_fields) != config.expected_dates:
        raise ModelQADiagnosticError("Usable-date QA metadata are incomplete.")

    sentinel = model_table.loc[
        :, ["tract_geoid", "target_date", "target_lst_c", *config.sentinel_feature_names]
    ].copy()
    sentinel["target_date"] = pd.to_datetime(sentinel["target_date"], errors="raise")
    missing_count = sentinel.loc[:, config.sentinel_feature_names].isna().sum(axis=1)
    if not missing_count.isin([0, len(config.sentinel_feature_names)]).all():
        raise ModelQADiagnosticError("Sentinel missingness is not all-five-or-none.")
    sentinel["sentinel_availability"] = np.where(
        missing_count.eq(0), "complete", "all_five_missing"
    )
    sentinel = sentinel.drop(columns=list(config.sentinel_feature_names))

    joined = paired.merge(
        targets,
        on=["tract_geoid", "target_date", "spatial_block"],
        how="outer",
        validate="one_to_one",
        indicator="_target_merge",
    ).merge(
        sentinel,
        on=["tract_geoid", "target_date"],
        how="outer",
        validate="one_to_one",
        indicator="_model_merge",
        suffixes=("", "_model"),
    ).merge(date_fields, on="target_date", how="left", validate="many_to_one")
    if (
        not joined["_target_merge"].eq("both").all()
        or not joined["_model_merge"].eq("both").all()
        or len(joined) != config.expected_rows
        or not _bit_exact(joined["y_true"], joined["target_lst_c"])
        or not _bit_exact(joined["y_true"], joined["target_lst_c_model"])
    ):
        raise ModelQADiagnosticError("OOF, target-QA, and model-table keys/truth disagree.")
    joined = joined.drop(
        columns=["_target_merge", "_model_merge", "target_lst_c", "target_lst_c_model"]
    )
    joined["low_scene_cloud_cohort"] = np.where(
        joined["target_date"].isin(low_cloud_dates),
        "any_scene_cloud_lt_15pct",
        "no_scene_cloud_lt_15pct",
    )
    joined["relative_endpoint_cohort"] = np.where(
        joined["relative_hotspot_top20"].notna(),
        "relative_label_available",
        "relative_label_unavailable",
    )
    joined["st_qa_2k_cohort"] = np.where(
        joined["median_st_uncertainty_k"].le(config.st_uncertainty_threshold_k),
        "tract_median_le_2k",
        "tract_median_gt_2k",
    )
    joined["b1_residual_c"] = joined["b1_y_pred"] - joined["y_true"]
    joined["m2_residual_c"] = joined["m2_y_pred"] - joined["y_true"]
    validate_development_diagnostic_frame(joined, config)
    input_records = {
        "compile_provenance_commit_sha256": authenticated.input_authentication[
            "compile_provenance_commit_sha256"
        ],
        "oof_predictions_sha256": authenticated.input_authentication[
            "oof_predictions_sha256"
        ],
        "target_build_progress_sha256": progress_sha,
        "model_ready_targets_sha256": sha256_file(config.model_ready_targets),
        "date_summary_sha256": sha256_file(config.date_summary),
        "model_dataset_provenance_sha256": model_provenance_sha,
        "model_dataset_commit_sha256": model_commit,
        "model_table_sha256": sha256_file(config.model_table),
        "target_inventory_summary_sha256": inventory_sha,
        "scene_inventory_sha256": sha256_file(config.scene_inventory),
    }
    return AuthenticatedQADiagnosticInputs(
        frame=joined,
        input_records=input_records,
        compile_commit_sha256=str(
            authenticated.input_authentication["compile_provenance_commit_sha256"]
        ),
        model_dataset_commit_sha256=model_commit,
    )


def _interval_labels(breaks: tuple[float, ...], *, decimals: int) -> list[str]:
    labels = []
    for index, (left, right) in enumerate(zip(breaks, breaks[1:], strict=False)):
        if index == len(breaks) - 2 and right >= 1_000_000:
            labels.append(f"[{left:.{decimals}f},inf)")
        elif index == len(breaks) - 2 and right > 1.0:
            labels.append(f"[{left:.{decimals}f},1.00]")
        else:
            labels.append(f"[{left:.{decimals}f},{right:.{decimals}f})")
    return labels


def build_qa_cohorts(
    frame: pd.DataFrame,
    config: ModelQADiagnosticConfig,
) -> pd.DataFrame:
    """Expand each tract-date row into frozen, descriptive QA cohort memberships."""

    working = frame.copy()
    working["valid_fraction_cohort"] = pd.cut(
        working["valid_fraction"],
        bins=config.valid_fraction_breaks,
        labels=_interval_labels(config.valid_fraction_breaks, decimals=2),
        right=False,
        include_lowest=True,
    ).astype("string")
    working["cloud_distance_cohort"] = pd.cut(
        working["median_cloud_distance_km"],
        bins=config.cloud_distance_breaks_km,
        labels=_interval_labels(config.cloud_distance_breaks_km, decimals=1),
        right=False,
        include_lowest=True,
    ).astype("string")
    if working[["valid_fraction_cohort", "cloud_distance_cohort"]].isna().any().any():
        raise ModelQADiagnosticError("A model row fell outside frozen QA cohort breaks.")
    memberships = [("all", pd.Series("all_rows", index=working.index))]
    memberships.extend(
        [
            ("st_qa_2k", working["st_qa_2k_cohort"]),
            ("valid_fraction", working["valid_fraction_cohort"]),
            ("median_cloud_distance_km", working["cloud_distance_cohort"]),
            ("sentinel_availability", working["sentinel_availability"]),
            ("scene_cloud_metadata", working["low_scene_cloud_cohort"]),
            ("relative_endpoint_gate", working["relative_endpoint_cohort"]),
        ]
    )
    pieces = []
    for dimension, labels in memberships:
        piece = working.copy()
        piece["cohort_dimension"] = dimension
        piece["cohort_label"] = labels.astype(str)
        pieces.append(piece)
    return pd.concat(pieces, ignore_index=True)


def _median_per_date_spearman(
    frame: pd.DataFrame,
    *,
    prediction_column: str,
) -> tuple[float, int, int]:
    values: list[float] = []
    undefined = 0
    for _, date_rows in frame.groupby("target_date", sort=True, observed=True):
        if (
            len(date_rows) < 2
            or date_rows["y_true"].nunique() < 2
            or date_rows[prediction_column].nunique() < 2
        ):
            undefined += 1
            continue
        correlation = date_rows["y_true"].rank(method="average").corr(
            date_rows[prediction_column].rank(method="average")
        )
        if correlation is None or not math.isfinite(float(correlation)):
            undefined += 1
        else:
            values.append(float(correlation))
    return (
        float(np.median(values)) if values else float("nan"),
        len(values),
        undefined,
    )


def build_qa_cohort_metrics(
    memberships: pd.DataFrame,
    config: ModelQADiagnosticConfig,
) -> pd.DataFrame:
    """Calculate date-aware metrics inside every frozen descriptive cohort."""

    rows: list[dict[str, Any]] = []
    prediction_columns = {
        config.baseline_model_id: "b1_y_pred",
        config.target_model_id: "m2_y_pred",
    }
    for (dimension, label), cohort in memberships.groupby(
        ["cohort_dimension", "cohort_label"], sort=True, observed=True
    ):
        per_date_rows = cohort.groupby("target_date", sort=True, observed=True).size()
        for model_id, prediction_column in prediction_columns.items():
            scored = cohort.assign(
                _residual=cohort[prediction_column] - cohort["y_true"]
            )
            per_date = scored.groupby("target_date", sort=True, observed=True)[
                "_residual"
            ].agg(
                date_mae_c=lambda value: float(np.abs(value).mean()),
                date_rmse_c=lambda value: float(np.sqrt(np.square(value).mean())),
                date_bias_c="mean",
            )
            spearman, defined, undefined = _median_per_date_spearman(
                cohort, prediction_column=prediction_column
            )
            residual = scored["_residual"].to_numpy(dtype=float)
            rows.append(
                {
                    "cohort_dimension": str(dimension),
                    "cohort_label": str(label),
                    "model_id": model_id,
                    "tract_date_row_count": len(cohort),
                    "independent_date_count": int(cohort["target_date"].nunique()),
                    "independent_spatial_block_count": int(
                        cohort["spatial_block"].nunique()
                    ),
                    "minimum_rows_per_date": int(per_date_rows.min()),
                    "median_rows_per_date": float(per_date_rows.median()),
                    "maximum_rows_per_date": int(per_date_rows.max()),
                    "primary_equal_date_weighted_mae_c": float(
                        per_date["date_mae_c"].mean()
                    ),
                    "equal_date_weighted_rmse_c": float(
                        per_date["date_rmse_c"].mean()
                    ),
                    "pooled_rmse_c": float(np.sqrt(np.square(residual).mean())),
                    "equal_date_weighted_bias_c": float(
                        per_date["date_bias_c"].mean()
                    ),
                    "median_per_date_spearman": spearman,
                    "spearman_defined_date_count": defined,
                    "spearman_undefined_date_count": undefined,
                }
            )
    result = pd.DataFrame(rows)
    return result.sort_values(
        ["cohort_dimension", "cohort_label", "model_id"], kind="stable"
    ).reset_index(drop=True)


def build_qa_cohort_improvement(
    metrics: pd.DataFrame,
    config: ModelQADiagnosticConfig,
) -> pd.DataFrame:
    """Pair the M2 and B1 cohort metrics without treating rows as independent."""

    key = ["cohort_dimension", "cohort_label"]
    baseline = metrics.loc[
        metrics["model_id"].eq(config.baseline_model_id)
    ].set_index(key)
    target = metrics.loc[metrics["model_id"].eq(config.target_model_id)].set_index(key)
    if not baseline.index.equals(target.index):
        raise ModelQADiagnosticError("Cohort metric surfaces are not paired.")
    baseline_mae = baseline["primary_equal_date_weighted_mae_c"].to_numpy(float)
    target_mae = target["primary_equal_date_weighted_mae_c"].to_numpy(float)
    result = pd.DataFrame(
        {
            "cohort_dimension": [value[0] for value in baseline.index],
            "cohort_label": [value[1] for value in baseline.index],
            "baseline_model_id": config.baseline_model_id,
            "target_model_id": config.target_model_id,
            "tract_date_row_count": baseline["tract_date_row_count"].to_numpy(int),
            "independent_date_count": baseline["independent_date_count"].to_numpy(int),
            "independent_spatial_block_count": baseline[
                "independent_spatial_block_count"
            ].to_numpy(int),
            "baseline_primary_mae_c": baseline_mae,
            "target_primary_mae_c": target_mae,
            "absolute_mae_improvement_c": baseline_mae - target_mae,
            "relative_mae_improvement_fraction": (baseline_mae - target_mae)
            / baseline_mae,
        }
    )
    result["relative_mae_improvement_percent"] = (
        result["relative_mae_improvement_fraction"] * 100.0
    )
    return result


def build_qa_cohort_bootstrap(
    memberships: pd.DataFrame,
    config: ModelQADiagnosticConfig,
) -> pd.DataFrame:
    """Apply paired complete-date × complete-block resampling within each cohort."""

    rows: list[dict[str, Any]] = []
    grouped = memberships.groupby(
        ["cohort_dimension", "cohort_label"], sort=True, observed=True
    )
    for index, ((dimension, label), cohort) in enumerate(grouped):
        cells = (
            cohort.assign(
                _b1_absolute_error=(cohort["b1_y_pred"] - cohort["y_true"]).abs(),
                _m2_absolute_error=(cohort["m2_y_pred"] - cohort["y_true"]).abs(),
            )
            .groupby(["target_date", "spatial_block"], sort=True, observed=True)
            .agg(
                row_count=("tract_geoid", "size"),
                baseline_absolute_error_sum_c=("_b1_absolute_error", "sum"),
                target_absolute_error_sum_c=("_m2_absolute_error", "sum"),
            )
            .reset_index()
        )
        seed = config.bootstrap_seed + index
        result = _crossed_cohort_bootstrap(
            cells,
            seed=seed,
            replicates=config.bootstrap_replicates,
            confidence_level=config.bootstrap_confidence_level,
        )
        rows.append(
            {
                "cohort_dimension": str(dimension),
                "cohort_label": str(label),
                "baseline_model_id": config.baseline_model_id,
                "target_model_id": config.target_model_id,
                **result,
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["cohort_dimension", "cohort_label"], kind="stable"
    ).reset_index(drop=True)


def _crossed_cohort_bootstrap(
    cells: pd.DataFrame,
    *,
    seed: int,
    replicates: int,
    confidence_level: float,
) -> dict[str, Any]:
    """Cross dates and blocks, redrawing only wholly empty sparse-cohort replicates."""

    required = {
        "target_date",
        "spatial_block",
        "row_count",
        "baseline_absolute_error_sum_c",
        "target_absolute_error_sum_c",
    }
    if (
        not required.issubset(cells)
        or cells.empty
        or cells.duplicated(["target_date", "spatial_block"]).any()
    ):
        raise ModelQADiagnosticError("Cohort bootstrap requires unique date-block cells.")
    dates = pd.Index(sorted(pd.to_datetime(cells["target_date"]).unique()))
    blocks = pd.Index(sorted(cells["spatial_block"].astype(str).unique()))
    date_index = {value: index for index, value in enumerate(dates)}
    block_index = {value: index for index, value in enumerate(blocks)}
    shape = (len(dates), len(blocks))
    counts = np.zeros(shape, dtype=float)
    baseline_sums = np.zeros(shape, dtype=float)
    target_sums = np.zeros(shape, dtype=float)
    for row in cells.itertuples(index=False):
        i = date_index[pd.Timestamp(row.target_date)]
        j = block_index[str(row.spatial_block)]
        counts[i, j] = float(row.row_count)
        baseline_sums[i, j] = float(row.baseline_absolute_error_sum_c)
        target_sums[i, j] = float(row.target_absolute_error_sum_c)
    if (
        np.any(counts < 0)
        or np.any(baseline_sums < 0)
        or np.any(target_sums < 0)
        or np.any(counts.sum(axis=1) <= 0)
    ):
        raise ModelQADiagnosticError("Cohort bootstrap sufficient statistics are invalid.")
    date_denominators = counts.sum(axis=1)
    point_baseline = float(np.mean(baseline_sums.sum(axis=1) / date_denominators))
    point_target = float(np.mean(target_sums.sum(axis=1) / date_denominators))
    if point_baseline <= 0:
        raise ModelQADiagnosticError("Cohort baseline MAE must be positive.")
    rng = np.random.default_rng(seed)
    absolute_values: list[float] = []
    relative_values: list[float] = []
    empty_redraws = 0
    zero_observation_date_draws = 0
    maximum_draws = replicates * 1_000
    draws = 0
    while len(absolute_values) < replicates:
        draws += 1
        if draws > maximum_draws:
            raise ModelQADiagnosticError(
                "Sparse cohort bootstrap could not obtain enough nonempty crossed draws."
            )
        date_weights = rng.multinomial(
            len(dates), np.full(len(dates), 1.0 / len(dates))
        ).astype(float)
        block_weights = rng.multinomial(
            len(blocks), np.full(len(blocks), 1.0 / len(blocks))
        ).astype(float)
        sampled_counts = counts @ block_weights
        valid = sampled_counts > 0
        weights = date_weights * valid
        denominator = float(weights.sum())
        if denominator <= 0:
            empty_redraws += 1
            continue
        zero_observation_date_draws += int(((date_weights > 0) & ~valid).sum())
        sampled_baseline = baseline_sums @ block_weights
        sampled_target = target_sums @ block_weights
        baseline_date_mae = np.divide(
            sampled_baseline,
            sampled_counts,
            out=np.zeros_like(sampled_baseline),
            where=valid,
        )
        target_date_mae = np.divide(
            sampled_target,
            sampled_counts,
            out=np.zeros_like(sampled_target),
            where=valid,
        )
        baseline_mae = float(np.dot(baseline_date_mae, weights) / denominator)
        target_mae = float(np.dot(target_date_mae, weights) / denominator)
        if baseline_mae <= 0:
            empty_redraws += 1
            continue
        absolute = baseline_mae - target_mae
        absolute_values.append(absolute)
        relative_values.append(absolute / baseline_mae)
    absolute_array = np.asarray(absolute_values, dtype=float)
    relative_array = np.asarray(relative_values, dtype=float)
    alpha = (1.0 - confidence_level) / 2.0
    absolute_ci = np.quantile(absolute_array, [alpha, 1.0 - alpha], method="linear")
    relative_ci = np.quantile(relative_array, [alpha, 1.0 - alpha], method="linear")
    point_absolute = point_baseline - point_target
    point_relative = point_absolute / point_baseline
    return {
        "bootstrap_method": BOOTSTRAP_METHOD,
        "bootstrap_sampling_unit": BOOTSTRAP_SAMPLING_UNIT,
        "bootstrap_estimand": "equal_date_weighted_mae_with_row_weighting_within_date",
        "complete_date_resampling": True,
        "complete_spatial_block_resampling": True,
        "date_and_block_draws_independent": True,
        "bootstrap_seed": seed,
        "bootstrap_replicates": replicates,
        "confidence_level": confidence_level,
        "percentile_interval_method": "linear",
        "paired_models_share_every_cluster_draw": True,
        "random_row_sampling_used": False,
        "empty_crossed_replicate_redraw_count": empty_redraws,
        "zero_observation_sampled_date_draw_count": zero_observation_date_draws,
        "date_block_cell_count": len(cells),
        "independent_date_count": len(dates),
        "independent_spatial_block_count": len(blocks),
        "tract_date_row_count": int(cells["row_count"].sum()),
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
        "probability_improvement_gt_zero": float(np.mean(absolute_array > 0.0)),
        "probability_relative_improvement_gt_10_percent": float(
            np.mean(relative_array > 0.10)
        ),
    }


def build_failure_case_tables(
    frame: pd.DataFrame,
    config: ModelQADiagnosticConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rank dates and tracts by authenticated joint-M2 error for limitation review."""

    scored = frame.assign(
        b1_absolute_error_c=np.abs(frame["b1_residual_c"]),
        m2_absolute_error_c=np.abs(frame["m2_residual_c"]),
    )
    date_rows = []
    for target_date, group in scored.groupby("target_date", sort=True, observed=True):
        b1_mae = float(group["b1_absolute_error_c"].mean())
        m2_mae = float(group["m2_absolute_error_c"].mean())
        date_rows.append(
            {
                "target_date": target_date,
                "platform": str(group["platform"].iloc[0]),
                "tract_date_row_count": len(group),
                "independent_spatial_block_count": int(group["spatial_block"].nunique()),
                "b1_mae_c": b1_mae,
                "m2_mae_c": m2_mae,
                "m2_minus_b1_mae_c": m2_mae - b1_mae,
                "m2_relative_improvement_fraction": (b1_mae - m2_mae) / b1_mae,
                "m2_bias_c": float(group["m2_residual_c"].mean()),
                "m2_underprediction_fraction": float(group["m2_residual_c"].lt(0).mean()),
                "median_st_uncertainty_k": float(
                    group["median_st_uncertainty_k"].median()
                ),
                "median_valid_fraction": float(group["valid_fraction"].median()),
                "retained_tract_fraction": float(group["retained_tract_fraction"].iloc[0]),
                "sentinel_missing_row_count": int(
                    group["sentinel_availability"].eq("all_five_missing").sum()
                ),
                "relative_endpoint_coverage_pass": bool(
                    group["relative_endpoint_coverage_pass"].iloc[0]
                ),
            }
        )
    worst_dates = (
        pd.DataFrame(date_rows)
        .sort_values(["m2_mae_c", "target_date"], ascending=[False, True], kind="stable")
        .head(config.failure_case_limit)
        .reset_index(drop=True)
    )
    tract_rows = []
    for geoid, group in scored.groupby("tract_geoid", sort=True, observed=True):
        b1_mae = float(group["b1_absolute_error_c"].mean())
        m2_mae = float(group["m2_absolute_error_c"].mean())
        tract_rows.append(
            {
                "tract_geoid": str(geoid),
                "tract_date_row_count": len(group),
                "independent_date_count": int(group["target_date"].nunique()),
                "b1_mean_absolute_error_c": b1_mae,
                "m2_mean_absolute_error_c": m2_mae,
                "m2_minus_b1_mean_absolute_error_c": m2_mae - b1_mae,
                "m2_relative_improvement_fraction": (b1_mae - m2_mae) / b1_mae,
                "m2_mean_residual_c": float(group["m2_residual_c"].mean()),
                "m2_underprediction_fraction": float(group["m2_residual_c"].lt(0).mean()),
                "median_st_uncertainty_k": float(
                    group["median_st_uncertainty_k"].median()
                ),
                "median_valid_fraction": float(group["valid_fraction"].median()),
                "sentinel_missing_row_fraction": float(
                    group["sentinel_availability"].eq("all_five_missing").mean()
                ),
            }
        )
    worst_tracts = (
        pd.DataFrame(tract_rows)
        .sort_values(
            ["m2_mean_absolute_error_c", "tract_geoid"],
            ascending=[False, True],
            kind="stable",
        )
        .head(config.failure_case_limit)
        .reset_index(drop=True)
    )
    return worst_dates, worst_tracts


def _csv_record(path: Path, rows: int) -> dict[str, Any]:
    return {
        "path": path.name,
        "path_base": "output_directory",
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "rows": int(rows),
    }


def _json_record(path: Path) -> dict[str, Any]:
    return {
        "path": path.name,
        "path_base": "output_directory",
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def _cohort_lookup(improvement: pd.DataFrame, dimension: str, label: str) -> dict[str, Any]:
    row = improvement.loc[
        improvement["cohort_dimension"].eq(dimension)
        & improvement["cohort_label"].eq(label)
    ]
    if len(row) != 1:
        raise ModelQADiagnosticError(f"Missing diagnostic cohort: {dimension}/{label}")
    return row.iloc[0].to_dict()


def _combined_cohort_lookup(
    improvement: pd.DataFrame,
    bootstrap: pd.DataFrame,
    dimension: str,
    label: str,
) -> dict[str, Any]:
    result = _cohort_lookup(improvement, dimension, label)
    interval = _cohort_lookup(bootstrap, dimension, label)
    result["crossed_bootstrap"] = interval
    return result


def analyze_model_qa(
    config_path: str | Path = DEFAULT_QA_DIAGNOSTIC_CONFIG,
    *,
    output_directory: str | Path | None = None,
) -> dict[str, Any]:
    """Generate authenticated QA/missingness diagnostics without fitting a model."""

    config = load_model_qa_diagnostic_config(config_path)
    authenticated = authenticate_qa_diagnostic_inputs(config)
    memberships = build_qa_cohorts(authenticated.frame, config)
    metrics = build_qa_cohort_metrics(memberships, config)
    improvement = build_qa_cohort_improvement(metrics, config)
    bootstrap = build_qa_cohort_bootstrap(memberships, config)
    worst_dates, worst_tracts = build_failure_case_tables(authenticated.frame, config)
    output = (
        config.output_directory
        if output_directory is None
        else Path(output_directory).resolve()
    )
    output.mkdir(parents=True, exist_ok=True)
    metrics_path = output / COHORT_METRICS_FILENAME
    improvement_path = output / COHORT_IMPROVEMENT_FILENAME
    bootstrap_path = output / COHORT_BOOTSTRAP_FILENAME
    worst_dates_path = output / WORST_DATES_FILENAME
    worst_tracts_path = output / WORST_TRACTS_FILENAME
    summary_path = output / SUMMARY_FILENAME
    atomic_csv(metrics, metrics_path)
    atomic_csv(improvement, improvement_path)
    atomic_csv(bootstrap, bootstrap_path)
    atomic_csv(worst_dates, worst_dates_path)
    atomic_csv(worst_tracts, worst_tracts_path)
    summary: dict[str, Any] = {
        "schema_version": QA_DIAGNOSTIC_SCHEMA_VERSION,
        "algorithm_version": QA_DIAGNOSTIC_ALGORITHM_VERSION,
        "state": "complete",
        "analysis_scope": "locked_2020_2024_development_joint_oof_only",
        "final_test_year": config.final_test_year,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "tract_date_row_count": config.expected_rows,
        "independent_date_count": config.expected_dates,
        "independent_spatial_block_count": config.expected_blocks,
        "comparison": {
            "family": config.family,
            "baseline_model_id": config.baseline_model_id,
            "target_model_id": config.target_model_id,
        },
        "selected_cohorts": {
            "all_rows": _combined_cohort_lookup(
                improvement, bootstrap, "all", "all_rows"
            ),
            "tract_median_st_qa_le_2k": _combined_cohort_lookup(
                improvement, bootstrap, "st_qa_2k", "tract_median_le_2k"
            ),
            "tract_median_st_qa_gt_2k": _combined_cohort_lookup(
                improvement, bootstrap, "st_qa_2k", "tract_median_gt_2k"
            ),
            "sentinel_complete": _combined_cohort_lookup(
                improvement, bootstrap, "sentinel_availability", "complete"
            ),
            "sentinel_all_five_missing": _combined_cohort_lookup(
                improvement,
                bootstrap,
                "sentinel_availability",
                "all_five_missing",
            ),
            "low_scene_cloud_metadata": _combined_cohort_lookup(
                improvement,
                bootstrap,
                "scene_cloud_metadata",
                "any_scene_cloud_lt_15pct",
            ),
        },
        "qa_interpretation": {
            "st_qa_2k_cohort_is_tract_summary_filter": True,
            "pixel_level_st_qa_hard_mask_reaggregated": False,
            "tract_summary_filter_replaces_pixel_level_sensitivity": False,
            "scene_cloud_is_metadata_only": True,
            "qa_fields_used_as_predictors": False,
        },
        "model_lock_readiness": {
            "ready": False,
            "blocker": (
                "The prespecified full pixel-level ST_QA hard-mask sensitivity has "
                "not yet been rebuilt; these tract-summary cohorts are diagnostic only."
            ),
        },
        "input_authentication": authenticated.input_records,
    }
    summary["commit_sha256"] = canonical_sha256(summary)
    atomic_json(summary, summary_path)
    pipeline_sha, pipeline_fingerprint = code_runtime_fingerprint(
        project_root=_project_root(),
        relative_paths=PIPELINE_FILES,
        algorithm_version=QA_DIAGNOSTIC_ALGORITHM_VERSION,
    )
    output_files = {
        COHORT_METRICS_FILENAME: _csv_record(metrics_path, len(metrics)),
        COHORT_IMPROVEMENT_FILENAME: _csv_record(improvement_path, len(improvement)),
        COHORT_BOOTSTRAP_FILENAME: _csv_record(bootstrap_path, len(bootstrap)),
        WORST_DATES_FILENAME: _csv_record(worst_dates_path, len(worst_dates)),
        WORST_TRACTS_FILENAME: _csv_record(worst_tracts_path, len(worst_tracts)),
        SUMMARY_FILENAME: _json_record(summary_path),
    }
    provenance: dict[str, Any] = {
        "schema_version": QA_DIAGNOSTIC_SCHEMA_VERSION,
        "algorithm_version": QA_DIAGNOSTIC_ALGORITHM_VERSION,
        "state": "complete",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "analysis_scope": "locked_2020_2024_development_joint_oof_only",
        "final_test_year": config.final_test_year,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "tract_date_row_count": config.expected_rows,
        "independent_date_count": config.expected_dates,
        "independent_spatial_block_count": config.expected_blocks,
        "analysis_config": {
            "path": config.path.as_posix(),
            "sha256": sha256_file(config.path),
            "semantic_sha256": config.semantic_sha256,
        },
        "input_authentication": authenticated.input_records,
        "pipeline_sha256": pipeline_sha,
        "pipeline_fingerprint": pipeline_fingerprint,
        "scientific_contract": {
            "models_fitted": False,
            "random_row_resampling_used": False,
            "complete_date_and_spatial_block_bootstrap_used": True,
            "qa_or_missingness_fields_used_as_predictors": False,
            "diagnostic_association_not_causal": True,
            "st_qa_2k_is_tract_summary_cohort_not_pixel_reaggregation": True,
            "final_test_unlocked": False,
        },
        "summary_commit_sha256": summary["commit_sha256"],
        "output_files": output_files,
    }
    provenance["commit_sha256"] = canonical_sha256(provenance)
    atomic_json(provenance, output / PROVENANCE_FILENAME)
    return provenance
