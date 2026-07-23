"""Authenticated analysis of frozen reduced-feature-set M2 refits.

The three fitted scenarios are descriptive reduced feature-set refits.  They are
not causal importance estimates and are not leave-one-feature-family-out fits.
The all-feature M2 surface is referenced from the already authenticated canonical
development OOF artifact; it is never refit by this stage.
"""

from __future__ import annotations

import json
import math
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from la_heat.model_result_analysis import (
    AuthenticatedModelResults,
    ResultAnalysisConfig,
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
from la_heat.validation_splits import FAMILIES

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "feature-ablation-analysis-v1"
STATE: Final = "frozen_development_diagnostics"
DEFAULT_CONFIG: Final = Path("configs/feature_ablation_analysis.toml")
ABLATION_COMPILE_ALGORITHM_VERSION: Final = "feature-ablation-outer-refit-v1"
ABLATION_OOF_FILENAME: Final = "feature_ablation_oof_predictions.parquet"
ABLATION_PROVENANCE_FILENAME: Final = "feature_ablation_compile_provenance.json"
METRICS_FILENAME: Final = "feature_ablation_metrics.csv"
BOOTSTRAP_FILENAME: Final = "feature_ablation_joint_crossed_bootstrap.csv"
SUMMARY_FILENAME: Final = "feature_ablation_analysis_summary.json"
PROVENANCE_FILENAME: Final = "feature_ablation_analysis_provenance.json"
BOOTSTRAP_METHOD: Final = "crossed_date_spatial_block"
BOOTSTRAP_SAMPLING_UNIT: Final = "complete_clusters_only"
FITTED_SCENARIOS: Final = (
    "calendar_weather",
    "calendar_land_use_geography",
    "calendar_satellite",
)
ALL_FEATURE_SCENARIO: Final = "all_features"
EXPECTED_ABLATION_RUN_ID: Final = (
    "5d1959708f99227f8d1f3be8be9327dc1bb4152a168f11d042b37a72a4ece3fb"
)
EXPECTED_SOURCE_RUN_ID: Final = (
    "eb2d09ce9592d5531b51e3e507634aa25f25ef1323376b056dd79fae948876f5"
)
FROZEN_BOOTSTRAP_SEED: Final = 20_260_726
FROZEN_BOOTSTRAP_REPLICATES: Final = 5_000
FROZEN_CONFIDENCE_LEVEL: Final = 0.95
ABLATION_OOF_COLUMNS: Final = (
    "tract_geoid",
    "target_date",
    "spatial_block",
    "ablation_id",
    "family",
    "fold_id",
    "model_id",
    "candidate_id",
    "y_true",
    "y_pred",
)
NORMALIZED_COLUMNS: Final = (
    "tract_geoid",
    "target_date",
    "spatial_block",
    "scenario_id",
    "family",
    "fold_id",
    "model_id",
    "candidate_id",
    "y_true",
    "y_pred",
)
SCENARIO_DESCRIPTIONS: Final = {
    "all_features": (
        "calendar + weather + land_use + geography + lagged satellite"
    ),
    "calendar_weather": "calendar + weather",
    "calendar_land_use_geography": "calendar + land_use + geography",
    "calendar_satellite": "calendar + lagged satellite",
}
PIPELINE_FILES: Final = (
    "scripts/analyze_feature_ablation.py",
    "src/la_heat/feature_ablation_analysis.py",
    "src/la_heat/model_result_analysis.py",
    "src/la_heat/provenance.py",
)


class FeatureAblationAnalysisError(ValueError):
    """Raised when the frozen ablation analysis cannot be authenticated."""


@dataclass(frozen=True, slots=True)
class FeatureAblationAnalysisConfig:
    """Validated settings for the post-run ablation analysis."""

    path: Path
    semantic_sha256: str
    result_analysis_config: Path
    compile_directory: Path
    output_directory: Path
    expected_run_id: str
    expected_source_run_id: str
    final_test_year: int
    final_test_locked: bool
    model_id: str
    split_families: tuple[str, ...]
    fitted_scenario_ids: tuple[str, ...]
    all_feature_scenario_id: str
    expected_rows_per_family: int
    expected_dates: int
    expected_blocks: int
    bootstrap_method: str
    bootstrap_sampling_unit: str
    bootstrap_seed: int
    bootstrap_replicates: int
    bootstrap_confidence_level: float
    relative_improvement_threshold_fraction: float

    @property
    def expected_fitted_rows(self) -> int:
        return (
            len(self.fitted_scenario_ids)
            * len(self.split_families)
            * self.expected_rows_per_family
        )


@dataclass(frozen=True, slots=True)
class AuthenticatedFeatureAblationInputs:
    """Normalized four-scenario OOF surfaces and immutable input records."""

    frame: pd.DataFrame
    fitted_oof: pd.DataFrame
    all_feature_oof: pd.DataFrame
    compile_provenance: dict[str, Any]
    input_authentication: dict[str, Any]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FeatureAblationAnalysisError(f"{label} must be a TOML/JSON object.")
    return value


def _exact_keys(value: object, expected: set[str], *, label: str) -> dict[str, Any]:
    result = _mapping(value, label=label)
    if set(result) != expected:
        raise FeatureAblationAnalysisError(
            f"{label} keys must be exactly {sorted(expected)}; got {sorted(result)}."
        )
    return result


def _integer(value: object, *, label: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise FeatureAblationAnalysisError(f"{label} must be an integer >= {minimum}.")
    return value


def _finite(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FeatureAblationAnalysisError(f"{label} must be numeric.")
    number = float(value)
    if not math.isfinite(number):
        raise FeatureAblationAnalysisError(f"{label} must be finite.")
    return number


def _sha256(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise FeatureAblationAnalysisError(f"{label} must be a lowercase SHA-256.")
    return value


def _resolve(value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise FeatureAblationAnalysisError(f"{label} must be a non-empty path string.")
    path = Path(value)
    return (path if path.is_absolute() else _project_root() / path).resolve()


def load_feature_ablation_analysis_config(
    path: str | Path = DEFAULT_CONFIG,
) -> FeatureAblationAnalysisConfig:
    """Load the frozen analysis configuration and reject contract drift."""

    config_path = Path(path).resolve()
    try:
        with config_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise FeatureAblationAnalysisError(
            f"Cannot read feature-ablation analysis configuration: {config_path}"
        ) from error
    _exact_keys(
        raw,
        {"schema_version", "algorithm_version", "state", "paths", "analysis", "bootstrap"},
        label="feature-ablation analysis configuration",
    )
    if raw["schema_version"] != SCHEMA_VERSION:
        raise FeatureAblationAnalysisError("Unsupported analysis schema version.")
    if raw["algorithm_version"] != ALGORITHM_VERSION or raw["state"] != STATE:
        raise FeatureAblationAnalysisError("Analysis algorithm or frozen state drifted.")
    paths = _exact_keys(
        raw["paths"],
        {
            "result_analysis_config",
            "feature_ablation_compile_directory",
            "output_directory",
        },
        label="paths",
    )
    analysis = _exact_keys(
        raw["analysis"],
        {
            "expected_run_id",
            "expected_source_run_id",
            "final_test_year",
            "final_test_locked",
            "model_id",
            "split_families",
            "fitted_scenario_ids",
            "all_feature_scenario_id",
            "expected_tract_date_row_count_per_family",
            "expected_independent_date_count",
            "expected_independent_spatial_block_count",
        },
        label="analysis",
    )
    bootstrap = _exact_keys(
        raw["bootstrap"],
        {
            "method",
            "sampling_unit",
            "seed",
            "replicates",
            "confidence_level",
            "relative_improvement_threshold_fraction",
        },
        label="bootstrap",
    )
    expected_run_id = _sha256(analysis["expected_run_id"], label="expected_run_id")
    expected_source_run_id = _sha256(
        analysis["expected_source_run_id"], label="expected_source_run_id"
    )
    if (
        expected_run_id != EXPECTED_ABLATION_RUN_ID
        or expected_source_run_id != EXPECTED_SOURCE_RUN_ID
    ):
        raise FeatureAblationAnalysisError("Frozen ablation or source run identity drifted.")
    families = analysis["split_families"]
    scenarios = analysis["fitted_scenario_ids"]
    if families != list(FAMILIES):
        raise FeatureAblationAnalysisError(
            "Split families must remain exactly temporal, spatial, and joint."
        )
    if scenarios != list(FITTED_SCENARIOS):
        raise FeatureAblationAnalysisError("Fitted reduced feature-set IDs drifted.")
    if (
        analysis["final_test_year"] != 2025
        or analysis["final_test_locked"] is not True
        or analysis["model_id"] != "M2"
        or analysis["all_feature_scenario_id"] != ALL_FEATURE_SCENARIO
    ):
        raise FeatureAblationAnalysisError(
            "The M2 development-only 2025 lock or scenario identity drifted."
        )
    expected_rows = _integer(
        analysis["expected_tract_date_row_count_per_family"],
        label="expected_tract_date_row_count_per_family",
    )
    expected_dates = _integer(
        analysis["expected_independent_date_count"],
        label="expected_independent_date_count",
    )
    expected_blocks = _integer(
        analysis["expected_independent_spatial_block_count"],
        label="expected_independent_spatial_block_count",
    )
    if (expected_rows, expected_dates, expected_blocks) != (63_403, 65, 71):
        raise FeatureAblationAnalysisError("Frozen development cardinalities drifted.")
    confidence = _finite(bootstrap["confidence_level"], label="confidence_level")
    threshold = _finite(
        bootstrap["relative_improvement_threshold_fraction"],
        label="relative_improvement_threshold_fraction",
    )
    seed = _integer(bootstrap["seed"], label="bootstrap.seed", minimum=0)
    replicates = _integer(bootstrap["replicates"], label="bootstrap.replicates")
    if (
        bootstrap["method"] != BOOTSTRAP_METHOD
        or bootstrap["sampling_unit"] != BOOTSTRAP_SAMPLING_UNIT
        or seed != FROZEN_BOOTSTRAP_SEED
        or replicates != FROZEN_BOOTSTRAP_REPLICATES
        or confidence != FROZEN_CONFIDENCE_LEVEL
        or threshold != 0.10
    ):
        raise FeatureAblationAnalysisError("Frozen crossed-bootstrap contract drifted.")
    return FeatureAblationAnalysisConfig(
        path=config_path,
        semantic_sha256=canonical_sha256(raw),
        result_analysis_config=_resolve(
            paths["result_analysis_config"], label="result_analysis_config"
        ),
        compile_directory=_resolve(
            paths["feature_ablation_compile_directory"],
            label="feature_ablation_compile_directory",
        ),
        output_directory=_resolve(paths["output_directory"], label="output_directory"),
        expected_run_id=expected_run_id,
        expected_source_run_id=expected_source_run_id,
        final_test_year=2025,
        final_test_locked=True,
        model_id="M2",
        split_families=tuple(families),
        fitted_scenario_ids=tuple(scenarios),
        all_feature_scenario_id=ALL_FEATURE_SCENARIO,
        expected_rows_per_family=expected_rows,
        expected_dates=expected_dates,
        expected_blocks=expected_blocks,
        bootstrap_method=BOOTSTRAP_METHOD,
        bootstrap_sampling_unit=BOOTSTRAP_SAMPLING_UNIT,
        bootstrap_seed=seed,
        bootstrap_replicates=replicates,
        bootstrap_confidence_level=confidence,
        relative_improvement_threshold_fraction=threshold,
    )


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FeatureAblationAnalysisError(f"Cannot read valid {label}: {path}") from error
    if not isinstance(payload, dict):
        raise FeatureAblationAnalysisError(f"{label} must be a JSON object.")
    return payload


def _verify_commit(payload: Mapping[str, Any], *, label: str) -> str:
    working = dict(payload)
    recorded = working.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(working) != recorded:
        raise FeatureAblationAnalysisError(f"{label} canonical commit is invalid.")
    return recorded


def _verify_parquet_before_read(
    path: Path,
    record_value: object,
    *,
    expected_rows: int,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Verify byte, row, and schema locks before loading any Parquet columns."""

    record = _mapping(record_value, label=ABLATION_OOF_FILENAME)
    expected_hash = _sha256(record.get("sha256"), label="ablation OOF sha256")
    expected_bytes = _integer(
        record.get("bytes"), label="ablation OOF bytes", minimum=0
    )
    recorded_rows = _integer(record.get("rows"), label="ablation OOF rows")
    expected_schema = _sha256(
        record.get("schema_sha256"), label="ablation OOF schema_sha256"
    )
    if recorded_rows != expected_rows:
        raise FeatureAblationAnalysisError(
            "Ablation OOF provenance does not lock the exact 3 scenario x 3 family row count."
        )
    if "path" in record and record["path"] != ABLATION_OOF_FILENAME:
        raise FeatureAblationAnalysisError("Ablation OOF provenance path is unsafe.")
    if "path_base" in record and record["path_base"] != "output_directory":
        raise FeatureAblationAnalysisError("Ablation OOF path base is unsafe.")
    if not path.is_file():
        raise FeatureAblationAnalysisError(
            "Compiled feature-ablation OOF is absent; analysis remains fail-closed."
        )
    if path.stat().st_size != expected_bytes or sha256_file(path) != expected_hash:
        raise FeatureAblationAnalysisError("Ablation OOF byte lock failed before Parquet read.")
    try:
        parquet = pq.ParquetFile(path)
        metadata_rows = parquet.metadata.num_rows
        schema_columns = tuple(parquet.schema_arrow.names)
        empty = parquet.schema_arrow.empty_table().to_pandas()
    except Exception as error:
        raise FeatureAblationAnalysisError("Ablation OOF Parquet metadata are invalid.") from error
    if metadata_rows != recorded_rows:
        raise FeatureAblationAnalysisError("Ablation OOF row metadata disagree with provenance.")
    if schema_columns != ABLATION_OOF_COLUMNS:
        raise FeatureAblationAnalysisError("Ablation OOF Parquet columns drifted.")
    observed_schema = canonical_sha256(
        [(column, str(dtype)) for column, dtype in empty.dtypes.items()]
    )
    if observed_schema != expected_schema:
        raise FeatureAblationAnalysisError(
            "Ablation OOF schema metadata disagree with provenance before data read."
        )
    frame = pd.read_parquet(path)
    observed = parquet_file_record(path, frame)
    if any(
        observed[key] != expected
        for key, expected in {
            "sha256": expected_hash,
            "bytes": expected_bytes,
            "rows": recorded_rows,
            "schema_sha256": expected_schema,
        }.items()
    ):
        raise FeatureAblationAnalysisError("Ablation OOF changed during authenticated read.")
    return record, frame


def _bit_exact_float64(left: pd.Series, right: pd.Series) -> bool:
    left_values = left.to_numpy(dtype=np.float64)
    right_values = right.to_numpy(dtype=np.float64)
    return np.array_equal(left_values.view(np.uint64), right_values.view(np.uint64))


def _validate_source_m2(
    authenticated: AuthenticatedModelResults,
    config: FeatureAblationAnalysisConfig,
) -> pd.DataFrame:
    provenance = authenticated.compile_provenance
    if provenance.get("run_id") != config.expected_source_run_id:
        raise FeatureAblationAnalysisError("Canonical M2 source run identity drifted.")
    source = authenticated.oof.loc[
        authenticated.oof["model_id"].eq(config.model_id),
        [
            "tract_geoid",
            "target_date",
            "spatial_block",
            "family",
            "fold_id",
            "model_id",
            "candidate_id",
            "y_true",
            "y_pred",
        ],
    ].copy()
    if (
        len(source) != len(config.split_families) * config.expected_rows_per_family
        or source.duplicated(["family", "tract_geoid", "target_date"]).any()
        or set(source["family"].astype(str)) != set(config.split_families)
        or source["target_date"].dt.year.ge(config.final_test_year).any()
    ):
        raise FeatureAblationAnalysisError("Canonical all-feature M2 OOF coverage drifted.")
    counts = source.groupby("family", observed=True).agg(
        rows=("tract_geoid", "size"),
        dates=("target_date", "nunique"),
        blocks=("spatial_block", "nunique"),
    )
    if (
        not counts["rows"].eq(config.expected_rows_per_family).all()
        or not counts["dates"].eq(config.expected_dates).all()
        or not counts["blocks"].eq(config.expected_blocks).all()
    ):
        raise FeatureAblationAnalysisError("Canonical M2 independent-unit counts drifted.")
    return source


def _validate_fitted_oof(
    frame: pd.DataFrame,
    source: pd.DataFrame,
    config: FeatureAblationAnalysisConfig,
) -> pd.DataFrame:
    if tuple(frame.columns) != ABLATION_OOF_COLUMNS:
        raise FeatureAblationAnalysisError("Fitted OOF columns drifted after Parquet read.")
    result = frame.copy()
    try:
        result["target_date"] = pd.to_datetime(result["target_date"], errors="raise")
        numeric = result[["y_true", "y_pred"]].apply(pd.to_numeric, errors="raise")
    except (TypeError, ValueError) as error:
        raise FeatureAblationAnalysisError(
            "Fitted OOF date or numeric values are invalid."
        ) from error
    result.loc[:, ["y_true", "y_pred"]] = numeric
    if result.isna().any().any() or not np.isfinite(numeric.to_numpy(float)).all():
        raise FeatureAblationAnalysisError("Fitted OOF contains missing or non-finite values.")
    if result["target_date"].dt.year.ge(config.final_test_year).any():
        raise FeatureAblationAnalysisError("Fitted OOF contains locked 2025+ observations.")
    if set(result["ablation_id"].astype(str)) != set(config.fitted_scenario_ids):
        raise FeatureAblationAnalysisError("Fitted reduced feature-set IDs are incomplete.")
    if set(result["family"].astype(str)) != set(config.split_families):
        raise FeatureAblationAnalysisError("Fitted split-family coverage is incomplete.")
    if not result["model_id"].eq(config.model_id).all():
        raise FeatureAblationAnalysisError("Fitted scenario contains a non-M2 model.")
    identity = ["ablation_id", "family", "tract_geoid", "target_date"]
    if result.duplicated(identity).any():
        raise FeatureAblationAnalysisError("Fitted OOF contains duplicate scenario/family keys.")
    expected_pairs = {
        (scenario, family)
        for scenario in config.fitted_scenario_ids
        for family in config.split_families
    }
    observed_pairs = set(
        result[["ablation_id", "family"]].itertuples(index=False, name=None)
    )
    if observed_pairs != expected_pairs or len(result) != config.expected_fitted_rows:
        raise FeatureAblationAnalysisError("Fitted OOF scenario x family coverage is incomplete.")
    counts = result.groupby(["ablation_id", "family"], observed=True).agg(
        rows=("tract_geoid", "size"),
        dates=("target_date", "nunique"),
        blocks=("spatial_block", "nunique"),
    )
    if (
        not counts["rows"].eq(config.expected_rows_per_family).all()
        or not counts["dates"].eq(config.expected_dates).all()
        or not counts["blocks"].eq(config.expected_blocks).all()
    ):
        raise FeatureAblationAnalysisError("Fitted OOF exact cardinalities drifted.")

    reference = source.rename(
        columns={
            "spatial_block": "source_spatial_block",
            "fold_id": "source_fold_id",
            "candidate_id": "source_candidate_id",
            "y_true": "source_y_true",
        }
    ).drop(columns=["model_id", "y_pred"])
    joined = result.merge(
        reference,
        on=["family", "tract_geoid", "target_date"],
        how="outer",
        indicator=True,
        sort=False,
        validate="many_to_one",
    )
    if not joined["_merge"].eq("both").all() or len(joined) != len(result):
        raise FeatureAblationAnalysisError(
            "Fitted OOF keys do not exactly match canonical M2 source keys."
        )
    for fitted_column, source_column, label in (
        ("spatial_block", "source_spatial_block", "spatial block"),
        ("fold_id", "source_fold_id", "outer fold"),
        ("candidate_id", "source_candidate_id", "selected candidate"),
    ):
        if not np.array_equal(
            joined[fitted_column].astype(str).to_numpy(),
            joined[source_column].astype(str).to_numpy(),
        ):
            raise FeatureAblationAnalysisError(
                f"Fitted OOF disagrees with the canonical source {label}."
            )
    if not _bit_exact_float64(joined["y_true"], joined["source_y_true"]):
        raise FeatureAblationAnalysisError(
            "Fitted OOF y_true values are not bit-exact with canonical M2."
        )
    return result


def authenticate_feature_ablation_inputs(
    config: FeatureAblationAnalysisConfig,
    *,
    result_analysis_config: ResultAnalysisConfig | None = None,
) -> AuthenticatedFeatureAblationInputs:
    """Authenticate canonical M2 plus the completed ablation artifact fail-closed."""

    source_config = (
        load_result_analysis_config(config.result_analysis_config)
        if result_analysis_config is None
        else result_analysis_config
    )
    if (
        source_config.final_test_year != config.final_test_year
        or source_config.final_test_locked is not True
        or source_config.expected_tract_date_row_count != config.expected_rows_per_family
        or source_config.expected_independent_date_count != config.expected_dates
        or source_config.expected_independent_spatial_block_count != config.expected_blocks
    ):
        raise FeatureAblationAnalysisError(
            "Canonical result-analysis configuration disagrees with ablation cardinalities."
        )
    authenticated_source = authenticate_model_results(source_config)
    source = _validate_source_m2(authenticated_source, config)

    provenance_path = config.compile_directory / ABLATION_PROVENANCE_FILENAME
    if not provenance_path.is_file():
        raise FeatureAblationAnalysisError(
            "Feature-ablation compile provenance is absent; "
            "the running queue is not analyzable yet."
        )
    provenance = _read_json(provenance_path, label="feature-ablation compile provenance")
    compile_commit = _verify_commit(provenance, label="feature-ablation compile provenance")
    required = {
        "schema_version": 1,
        "algorithm_version": ABLATION_COMPILE_ALGORITHM_VERSION,
        "run_id": config.expected_run_id,
        "source_run_id": config.expected_source_run_id,
        "final_test_year": config.final_test_year,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "fitted_ablation_count": len(config.fitted_scenario_ids),
        "outer_folds_per_ablation": 431,
        "fitted_fragment_count": 1_293,
        "split_families": list(config.split_families),
        "split_family_count": len(config.split_families),
        "fitted_oof_rows_per_ablation_family": config.expected_rows_per_family,
        "fitted_oof_rows_per_ablation": (
            len(config.split_families) * config.expected_rows_per_family
        ),
        "fitted_oof_row_count": config.expected_fitted_rows,
    }
    if any(provenance.get(key) != value for key, value in required.items()):
        raise FeatureAblationAnalysisError(
            "Feature-ablation provenance is not the exact complete frozen result set."
        )
    if provenance.get("context_run_id") != authenticated_source.compile_provenance.get(
        "context_run_id"
    ):
        raise FeatureAblationAnalysisError(
            "Feature-ablation context does not match canonical M2 context."
        )
    source_manifest_commit = _sha256(
        provenance.get("source_run_manifest_commit_sha256"),
        label="source_run_manifest_commit_sha256",
    )
    source_selection_lock = _sha256(
        provenance.get("source_selection_and_all_oof_lock_sha256"),
        label="source_selection_and_all_oof_lock_sha256",
    )
    fragments = provenance.get("input_fragments")
    if not isinstance(fragments, list) or len(fragments) != 1_293:
        raise FeatureAblationAnalysisError("Feature-ablation fragment manifest is incomplete.")
    compiler_fingerprint = _mapping(
        provenance.get("compiler_pipeline_fingerprint"),
        label="compiler_pipeline_fingerprint",
    )
    compiler_sha = _sha256(
        provenance.get("compiler_pipeline_sha256"),
        label="compiler_pipeline_sha256",
    )
    if (
        canonical_sha256(compiler_fingerprint) != compiler_sha
        or compiler_fingerprint.get("algorithm_version")
        != ABLATION_COMPILE_ALGORITHM_VERSION
    ):
        raise FeatureAblationAnalysisError("Ablation compiler fingerprint is invalid.")
    all_reference = _exact_keys(
        provenance.get("all_feature_reference"),
        {"path", "sha256", "model_id", "refit_performed"},
        label="all_feature_reference",
    )
    source_oof_hash = authenticated_source.input_authentication.get(
        "oof_predictions_sha256"
    )
    if (
        all_reference["model_id"] != config.model_id
        or all_reference["refit_performed"] is not False
        or all_reference["sha256"] != source_oof_hash
    ):
        raise FeatureAblationAnalysisError(
            "All-feature reference does not match authenticated canonical M2 OOF."
        )
    output_files = _mapping(provenance.get("output_files"), label="output_files")
    if set(output_files) != {ABLATION_OOF_FILENAME}:
        raise FeatureAblationAnalysisError("Feature-ablation output manifest is not exact.")
    oof_path = config.compile_directory / ABLATION_OOF_FILENAME
    record, fitted = _verify_parquet_before_read(
        oof_path,
        output_files[ABLATION_OOF_FILENAME],
        expected_rows=config.expected_fitted_rows,
    )
    fitted = _validate_fitted_oof(fitted, source, config)

    reduced = fitted.rename(columns={"ablation_id": "scenario_id"}).loc[
        :, NORMALIZED_COLUMNS
    ]
    all_features = source.copy()
    all_features.insert(3, "scenario_id", config.all_feature_scenario_id)
    all_features = all_features.loc[:, NORMALIZED_COLUMNS]
    normalized = pd.concat([all_features, reduced], ignore_index=True)
    input_authentication = {
        "canonical_model_result_authentication": authenticated_source.input_authentication,
        "canonical_model_compile_commit_sha256": authenticated_source.input_authentication[
            "compile_provenance_commit_sha256"
        ],
        "canonical_model_run_id": config.expected_source_run_id,
        "canonical_all_feature_oof_sha256": source_oof_hash,
        "feature_ablation_compile_provenance_path": provenance_path.as_posix(),
        "feature_ablation_compile_provenance_file_sha256": sha256_file(provenance_path),
        "feature_ablation_compile_commit_sha256": compile_commit,
        "feature_ablation_run_id": config.expected_run_id,
        "source_run_manifest_commit_sha256": source_manifest_commit,
        "source_selection_and_all_oof_lock_sha256": source_selection_lock,
        "compiler_pipeline_sha256": compiler_sha,
        "feature_ablation_oof_path": oof_path.as_posix(),
        "feature_ablation_oof_sha256": record["sha256"],
        "feature_ablation_oof_bytes": record["bytes"],
        "feature_ablation_oof_rows": record["rows"],
        "feature_ablation_oof_schema_sha256": record["schema_sha256"],
    }
    return AuthenticatedFeatureAblationInputs(
        frame=normalized,
        fitted_oof=fitted,
        all_feature_oof=all_features,
        compile_provenance=provenance,
        input_authentication=input_authentication,
    )


def _median_per_date_spearman(frame: pd.DataFrame) -> tuple[float, int, int]:
    values: list[float] = []
    undefined = 0
    for _, rows in frame.groupby("target_date", sort=True, observed=True):
        if len(rows) < 2 or rows["y_true"].nunique() < 2 or rows["y_pred"].nunique() < 2:
            undefined += 1
            continue
        correlation = rows["y_true"].rank(method="average").corr(
            rows["y_pred"].rank(method="average")
        )
        if correlation is None or not math.isfinite(float(correlation)):
            undefined += 1
        else:
            values.append(float(correlation))
    return (float(np.median(values)) if values else float("nan"), len(values), undefined)


def build_feature_ablation_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    """Compute date-aware descriptive metrics for every scenario and split family."""

    required = set(NORMALIZED_COLUMNS)
    if not required.issubset(frame.columns) or frame.empty:
        raise FeatureAblationAnalysisError("Normalized OOF frame is incomplete.")
    rows: list[dict[str, Any]] = []
    for (scenario_id, family), group in frame.groupby(
        ["scenario_id", "family"], sort=True, observed=True
    ):
        residual = group["y_pred"].to_numpy(float) - group["y_true"].to_numpy(float)
        scored = group.assign(_residual=residual)
        per_date = scored.groupby("target_date", sort=True, observed=True)["_residual"].agg(
            date_mae_c=lambda value: float(np.abs(value).mean()),
            date_rmse_c=lambda value: float(np.sqrt(np.square(value).mean())),
            date_bias_c="mean",
        )
        spearman, defined, undefined = _median_per_date_spearman(group)
        rows.append(
            {
                "scenario_id": str(scenario_id),
                "scenario_kind": (
                    "authenticated_all_feature_reference"
                    if scenario_id == ALL_FEATURE_SCENARIO
                    else "reduced_feature_set_refit"
                ),
                "feature_set_description": SCENARIO_DESCRIPTIONS[str(scenario_id)],
                "family": str(family),
                "tract_date_row_count": len(group),
                "independent_date_count": int(group["target_date"].nunique()),
                "independent_spatial_block_count": int(group["spatial_block"].nunique()),
                "date_macro_mae_c": float(per_date["date_mae_c"].mean()),
                "date_macro_rmse_c": float(per_date["date_rmse_c"].mean()),
                "date_macro_bias_c": float(per_date["date_bias_c"].mean()),
                "pooled_rmse_c": float(np.sqrt(np.square(residual).mean())),
                "median_per_date_spearman": spearman,
                "spearman_defined_date_count": defined,
                "spearman_undefined_date_count": undefined,
                "interpretation": "predictive_association_not_causal_importance",
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["family", "scenario_kind", "scenario_id"], kind="stable"
    ).reset_index(drop=True)


def _paired_joint_cells(
    frame: pd.DataFrame,
    *,
    reduced_scenario_id: str,
    all_feature_scenario_id: str = ALL_FEATURE_SCENARIO,
) -> pd.DataFrame:
    key = ["tract_geoid", "target_date"]
    columns = [*key, "spatial_block", "y_true", "y_pred"]
    joint = frame.loc[frame["family"].eq("joint")]
    reduced = joint.loc[joint["scenario_id"].eq(reduced_scenario_id), columns]
    all_features = joint.loc[joint["scenario_id"].eq(all_feature_scenario_id), columns]
    if reduced.empty or all_features.empty:
        raise FeatureAblationAnalysisError("Joint bootstrap is missing a paired scenario.")
    if reduced.duplicated(key).any() or all_features.duplicated(key).any():
        raise FeatureAblationAnalysisError("Joint bootstrap surfaces contain duplicate keys.")
    paired = reduced.merge(
        all_features,
        on=key,
        how="outer",
        suffixes=("_reduced", "_all_features"),
        indicator=True,
        sort=False,
        validate="one_to_one",
    )
    if not paired["_merge"].eq("both").all() or len(paired) != len(reduced):
        raise FeatureAblationAnalysisError("Joint bootstrap scenario keys are not paired.")
    if not np.array_equal(
        paired["spatial_block_reduced"].astype(str).to_numpy(),
        paired["spatial_block_all_features"].astype(str).to_numpy(),
    ) or not _bit_exact_float64(paired["y_true_reduced"], paired["y_true_all_features"]):
        raise FeatureAblationAnalysisError(
            "Joint bootstrap surfaces disagree on blocks or bit-exact truth."
        )
    paired["_reduced_absolute_error"] = np.abs(
        paired["y_pred_reduced"].to_numpy(float)
        - paired["y_true_reduced"].to_numpy(float)
    )
    paired["_all_features_absolute_error"] = np.abs(
        paired["y_pred_all_features"].to_numpy(float)
        - paired["y_true_all_features"].to_numpy(float)
    )
    return (
        paired.groupby(
            ["target_date", "spatial_block_reduced"], sort=True, observed=True
        )
        .agg(
            row_count=("tract_geoid", "size"),
            baseline_absolute_error_sum_c=("_reduced_absolute_error", "sum"),
            target_absolute_error_sum_c=("_all_features_absolute_error", "sum"),
        )
        .reset_index()
        .rename(columns={"spatial_block_reduced": "spatial_block"})
    )


def build_joint_feature_ablation_bootstrap(
    frame: pd.DataFrame,
    config: FeatureAblationAnalysisConfig,
) -> pd.DataFrame:
    """Compare all-feature M2 with each reduced refit using whole clusters only."""

    rows: list[dict[str, Any]] = []
    for index, scenario_id in enumerate(config.fitted_scenario_ids):
        cells = _paired_joint_cells(
            frame,
            reduced_scenario_id=scenario_id,
            all_feature_scenario_id=config.all_feature_scenario_id,
        )
        result = crossed_date_spatial_block_bootstrap(
            cells,
            seed=config.bootstrap_seed + index,
            replicates=config.bootstrap_replicates,
            confidence_level=config.bootstrap_confidence_level,
            method=config.bootstrap_method,
            sampling_unit=config.bootstrap_sampling_unit,
            probability_threshold_fraction=config.relative_improvement_threshold_fraction,
        )
        rows.append(
            {
                "family": "joint",
                "reduced_scenario_id": scenario_id,
                "reduced_feature_set_description": SCENARIO_DESCRIPTIONS[scenario_id],
                "all_feature_scenario_id": config.all_feature_scenario_id,
                "comparison_direction": (
                    "positive_means_all_features_lower_date_macro_mae_than_reduced_refit"
                ),
                "analysis_label": (
                    "reduced_feature_set_refit_comparison_not_causal_or_leave_one_family_out"
                ),
                **result,
            }
        )
    return pd.DataFrame(rows)


def _file_record(path: Path, *, rows: int | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": path.name,
        "path_base": "output_directory",
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }
    if rows is not None:
        record["rows"] = int(rows)
    return record


def _summary_result_rows(metrics: pd.DataFrame, bootstrap: pd.DataFrame) -> list[dict[str, Any]]:
    joint = metrics.loc[metrics["family"].eq("joint")].set_index("scenario_id")
    rows: list[dict[str, Any]] = []
    for comparison in bootstrap.to_dict("records"):
        reduced_id = str(comparison["reduced_scenario_id"])
        rows.append(
            {
                "reduced_scenario_id": reduced_id,
                "reduced_feature_set_description": SCENARIO_DESCRIPTIONS[reduced_id],
                "reduced_date_macro_mae_c": float(joint.loc[reduced_id, "date_macro_mae_c"]),
                "all_features_date_macro_mae_c": float(
                    joint.loc[ALL_FEATURE_SCENARIO, "date_macro_mae_c"]
                ),
                "absolute_mae_improvement_c": comparison["absolute_mae_improvement_c"],
                "relative_mae_improvement_fraction": comparison[
                    "relative_mae_improvement_fraction"
                ],
                "relative_mae_improvement_ci_lower_fraction": comparison[
                    "relative_mae_improvement_ci_lower_fraction"
                ],
                "relative_mae_improvement_ci_upper_fraction": comparison[
                    "relative_mae_improvement_ci_upper_fraction"
                ],
                "probability_improvement_gt_zero": comparison[
                    "probability_improvement_gt_zero"
                ],
                "probability_relative_improvement_gt_10_percent": comparison[
                    "probability_relative_improvement_gt_10_percent"
                ],
            }
        )
    return rows


def analyze_feature_ablation(
    config_path: str | Path = DEFAULT_CONFIG,
    *,
    output_directory: str | Path | None = None,
) -> dict[str, Any]:
    """Generate committed tables only after every input authenticates."""

    config = load_feature_ablation_analysis_config(config_path)
    authenticated = authenticate_feature_ablation_inputs(config)
    metrics = build_feature_ablation_metrics(authenticated.frame)
    bootstrap = build_joint_feature_ablation_bootstrap(authenticated.frame, config)
    output = (
        config.output_directory
        if output_directory is None
        else Path(output_directory).resolve()
    )
    output.mkdir(parents=True, exist_ok=True)
    metrics_path = output / METRICS_FILENAME
    bootstrap_path = output / BOOTSTRAP_FILENAME
    summary_path = output / SUMMARY_FILENAME
    atomic_csv(metrics, metrics_path)
    atomic_csv(bootstrap, bootstrap_path)
    summary: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "complete",
        "analysis_scope": "locked_2020_2024_development_oof_feature_set_diagnostic",
        "final_test_year": config.final_test_year,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "feature_ablation_run_id": config.expected_run_id,
        "canonical_model_run_id": config.expected_source_run_id,
        "model_id": config.model_id,
        "split_families": list(config.split_families),
        "fitted_scenario_ids": list(config.fitted_scenario_ids),
        "all_feature_scenario_id": config.all_feature_scenario_id,
        "scenario_count_including_reference": 4,
        "tract_date_row_count_per_family_scenario": config.expected_rows_per_family,
        "independent_date_count": config.expected_dates,
        "independent_spatial_block_count": config.expected_blocks,
        "joint_comparisons": _summary_result_rows(metrics, bootstrap),
        "interpretation": {
            "reduced_scenarios_are_refits": True,
            "all_feature_model_refit_performed": False,
            "causal_feature_importance": False,
            "leave_one_feature_family_out": False,
            "feature_importance_claim_allowed": False,
            "allowed_claim": "predictive_association_under_predeclared_reduced_feature_sets",
        },
        "input_authentication": authenticated.input_authentication,
    }
    summary["commit_sha256"] = canonical_sha256(summary)
    atomic_json(summary, summary_path)
    pipeline_sha, pipeline_fingerprint = code_runtime_fingerprint(
        project_root=_project_root(),
        relative_paths=PIPELINE_FILES,
        algorithm_version=ALGORITHM_VERSION,
    )
    output_files = {
        METRICS_FILENAME: _file_record(metrics_path, rows=len(metrics)),
        BOOTSTRAP_FILENAME: _file_record(bootstrap_path, rows=len(bootstrap)),
        SUMMARY_FILENAME: _file_record(summary_path),
    }
    provenance: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "complete",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "analysis_scope": "locked_2020_2024_development_oof_feature_set_diagnostic",
        "final_test_year": config.final_test_year,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "feature_ablation_run_id": config.expected_run_id,
        "canonical_model_run_id": config.expected_source_run_id,
        "analysis_config": {
            "path": config.path.as_posix(),
            "sha256": sha256_file(config.path),
            "semantic_sha256": config.semantic_sha256,
        },
        "input_authentication": authenticated.input_authentication,
        "pipeline_sha256": pipeline_sha,
        "pipeline_fingerprint": pipeline_fingerprint,
        "scientific_contract": {
            "final_test_unlocked": False,
            "models_fitted_by_analysis": False,
            "all_feature_reference_refit": False,
            "random_row_resampling_used": False,
            "complete_date_and_spatial_block_bootstrap_used": True,
            "fitted_scenarios_are_reduced_feature_set_refits": True,
            "causal_importance_interpretation_allowed": False,
            "leave_one_feature_family_out_interpretation_allowed": False,
        },
        "summary_commit_sha256": summary["commit_sha256"],
        "output_files": output_files,
    }
    provenance["commit_sha256"] = canonical_sha256(provenance)
    atomic_json(provenance, output / PROVENANCE_FILENAME)
    return provenance
