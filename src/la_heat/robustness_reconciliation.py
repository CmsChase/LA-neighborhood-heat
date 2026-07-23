"""Fail-closed reconciliation of authenticated development robustness evidence."""

from __future__ import annotations

import json
import math
import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pandas as pd

from la_heat.provenance import (
    atomic_csv,
    atomic_json,
    canonical_sha256,
    code_runtime_fingerprint,
    sha256_file,
)

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "development-robustness-reconciliation-v1"
FROZEN_STATE: Final = "frozen_development_reconciliation"
SUMMARY_FILENAME: Final = "robustness_reconciliation_summary.json"
EVIDENCE_FILENAME: Final = "robustness_evidence.csv"
PROVENANCE_FILENAME: Final = "robustness_reconciliation_provenance.json"
DEFAULT_CONFIG: Final = Path("configs/robustness_reconciliation.toml")

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_PIPELINE_FILES: Final = (
    "configs/robustness_reconciliation.toml",
    "scripts/reconcile_development_robustness.py",
    "src/la_heat/robustness_reconciliation.py",
    "src/la_heat/provenance.py",
)
_SOURCE_ALGORITHMS: Final = {
    "initial_results": "initial-model-result-analysis-v1",
    "endpoint": "model-endpoint-sensor-diagnostics-v1",
    "qa": "model-qa-diagnostics-v1",
    "residual_spatial": "residual-spatial-diagnostics-v1",
    "diagnostic_figures": "model-diagnostic-figures-v1",
    "feature_ablation": "feature-ablation-analysis-v1",
    "stqa2_sensitivity": "stqa2-pixel-label-sensitivity-v2",
}
_REQUIRED_OUTPUTS: Final = {
    "initial_results": frozenset(
        {
            "family_model_point_comparison.csv",
            "joint_m2_crossed_cluster_bootstrap.csv",
            "protocol_success_gates.csv",
            "model_results_initial_summary.json",
        }
    ),
    "endpoint": frozenset(
        {
            "hotspot_per_date.csv",
            "hotspot_summary.csv",
            "sensor_per_date_metrics.csv",
            "sensor_summary.csv",
            "sentinel_stratum_summary.csv",
            "model_endpoint_diagnostics_summary.json",
        }
    ),
    "qa": frozenset(
        {
            "qa_cohort_metrics.csv",
            "qa_cohort_improvement.csv",
            "qa_cohort_crossed_bootstrap.csv",
            "m2_worst_dates.csv",
            "m2_worst_tracts.csv",
            "model_qa_diagnostics_summary.json",
        }
    ),
    "residual_spatial": frozenset(
        {
            "joint_m2_b1_date_block_residuals.csv",
            "joint_m2_b1_morans_i_by_date.csv",
            "joint_m2_b1_morans_i_summary.csv",
            "joint_m2_b1_tract_residual_summary.csv",
            "joint_m2_b1_residual_diagnostics_map.png",
        }
    ),
    "diagnostic_figures": frozenset(
        {
            "joint_performance_overview.png",
            "qa_cohort_improvement_forest.png",
            "worst_date_errors.png",
            "fixed_date_lst_prediction_maps.png",
            "model_diagnostic_figures_summary.json",
        }
    ),
    "feature_ablation": frozenset(
        {
            "feature_ablation_metrics.csv",
            "feature_ablation_joint_crossed_bootstrap.csv",
            "feature_ablation_analysis_summary.json",
        }
    ),
    "stqa2_sensitivity": frozenset(
        {
            "stqa2_date_retention.csv",
            "stqa2_label_shift_by_date.csv",
            "stqa2_frozen_primary_oof_metrics.csv",
            "stqa2_frozen_primary_oof_bootstrap.csv",
            "stqa2_sensitivity_summary.json",
        }
    ),
}
_SUMMARY_FILENAMES: Final = {
    "initial_results": "model_results_initial_summary.json",
    "endpoint": "model_endpoint_diagnostics_summary.json",
    "qa": "model_qa_diagnostics_summary.json",
    "residual_spatial": "joint_m2_b1_morans_i_summary.csv",
    "diagnostic_figures": "model_diagnostic_figures_summary.json",
    "feature_ablation": "feature_ablation_analysis_summary.json",
    "stqa2_sensitivity": "stqa2_sensitivity_summary.json",
}


class RobustnessReconciliationError(RuntimeError):
    """An upstream artifact or scientific reconciliation contract failed."""


@dataclass(frozen=True)
class ReconciliationConfig:
    path: Path
    semantic_sha256: str
    source_paths: Mapping[str, Path]
    diagnostic_figure_output_directory: Path
    output_directory: Path
    final_test_year: int
    family: str
    baseline_model_id: str
    target_model_id: str
    expected_rows: int
    expected_dates: int
    expected_blocks: int
    expected_relative_dates: int
    expected_figure_count: int


@dataclass(frozen=True)
class AuthenticatedSource:
    name: str
    provenance_path: Path
    provenance_file_sha256: str
    provenance_commit_sha256: str
    provenance: Mapping[str, Any]
    outputs: Mapping[str, Path]
    json_outputs: Mapping[str, Mapping[str, Any]]
    csv_outputs: Mapping[str, pd.DataFrame]
    compile_commit_sha256: str
    oof_predictions_sha256: str


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve(root: Path, value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise RobustnessReconciliationError(f"{label} must be a non-empty path.")
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RobustnessReconciliationError(f"{label} must be an object.")
    return value


def _sequence(value: object, *, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise RobustnessReconciliationError(f"{label} must be a list.")
    return value


def _number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RobustnessReconciliationError(f"{label} must be numeric.")
    result = float(value)
    if not math.isfinite(result):
        raise RobustnessReconciliationError(f"{label} must be finite.")
    return result


def _integer(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RobustnessReconciliationError(f"{label} must be an integer.")
    return value


def _sha(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise RobustnessReconciliationError(f"{label} must be a lowercase SHA-256.")
    return value


def load_reconciliation_config(
    path: str | Path = DEFAULT_CONFIG,
) -> ReconciliationConfig:
    root = _root()
    config_path = _resolve(root, str(path), label="reconciliation config")
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    if set(raw) != {"schema_version", "algorithm_version", "state", "paths", "analysis"}:
        raise RobustnessReconciliationError("Reconciliation config top-level schema drifted.")
    if (
        raw["schema_version"] != SCHEMA_VERSION
        or raw["algorithm_version"] != ALGORITHM_VERSION
        or raw["state"] != FROZEN_STATE
    ):
        raise RobustnessReconciliationError("Reconciliation config identity drifted.")
    paths = _mapping(raw["paths"], label="paths")
    expected_path_keys = {
        "initial_results_provenance",
        "endpoint_provenance",
        "qa_provenance",
        "residual_spatial_provenance",
        "diagnostic_figures_provenance",
        "diagnostic_figure_output_directory",
        "feature_ablation_provenance",
        "stqa2_sensitivity_provenance",
        "output_directory",
    }
    if set(paths) != expected_path_keys:
        raise RobustnessReconciliationError("Reconciliation path schema drifted.")
    analysis = _mapping(raw["analysis"], label="analysis")
    expected_analysis_keys = {
        "final_test_year",
        "final_test_locked",
        "family",
        "baseline_model_id",
        "target_model_id",
        "expected_tract_date_row_count",
        "expected_independent_date_count",
        "expected_independent_spatial_block_count",
        "expected_relative_endpoint_date_count",
        "expected_diagnostic_figure_count",
    }
    if set(analysis) != expected_analysis_keys:
        raise RobustnessReconciliationError("Reconciliation analysis schema drifted.")
    if analysis["final_test_year"] != 2025 or analysis["final_test_locked"] is not True:
        raise RobustnessReconciliationError("The final-test lock must remain fixed at 2025.")
    source_paths = {
        name: _resolve(root, paths[f"{name}_provenance"], label=f"{name} provenance")
        for name in (
            "initial_results",
            "endpoint",
            "qa",
            "residual_spatial",
            "diagnostic_figures",
            "feature_ablation",
            "stqa2_sensitivity",
        )
    }
    semantic = {
        "schema_version": raw["schema_version"],
        "algorithm_version": raw["algorithm_version"],
        "state": raw["state"],
        "paths": dict(paths),
        "analysis": dict(analysis),
    }
    return ReconciliationConfig(
        path=config_path,
        semantic_sha256=canonical_sha256(semantic),
        source_paths=source_paths,
        diagnostic_figure_output_directory=_resolve(
            root,
            paths["diagnostic_figure_output_directory"],
            label="diagnostic figure output directory",
        ),
        output_directory=_resolve(root, paths["output_directory"], label="output directory"),
        final_test_year=2025,
        family=str(analysis["family"]),
        baseline_model_id=str(analysis["baseline_model_id"]),
        target_model_id=str(analysis["target_model_id"]),
        expected_rows=_integer(
            analysis["expected_tract_date_row_count"], label="expected rows"
        ),
        expected_dates=_integer(
            analysis["expected_independent_date_count"], label="expected dates"
        ),
        expected_blocks=_integer(
            analysis["expected_independent_spatial_block_count"],
            label="expected blocks",
        ),
        expected_relative_dates=_integer(
            analysis["expected_relative_endpoint_date_count"],
            label="expected relative dates",
        ),
        expected_figure_count=_integer(
            analysis["expected_diagnostic_figure_count"],
            label="expected figure count",
        ),
    )


def _validate_content_commit(payload: Mapping[str, Any], *, label: str) -> str:
    working = dict(payload)
    recorded = _sha(working.pop("commit_sha256", None), label=f"{label} commit")
    if canonical_sha256(working) != recorded:
        raise RobustnessReconciliationError(f"{label} content commitment failed.")
    return recorded


def _reject_final_test_content(value: object, *, location: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{location}.{key}"
            if key in {"unlock_final_test", "final_test_unlocked"} and item is not False:
                raise RobustnessReconciliationError(f"Final-test unlock detected at {child}.")
            if key == "contains_final_test_year" and item is not False:
                raise RobustnessReconciliationError(f"2025 presence detected at {child}.")
            if key == "target_date" and isinstance(item, str):
                try:
                    year = pd.Timestamp(item).year
                except (TypeError, ValueError):
                    year = 0
                if year >= 2025:
                    raise RobustnessReconciliationError(f"2025 target date detected at {child}.")
            if key == "development_years" and isinstance(item, list):
                if any(isinstance(year, int) and year >= 2025 for year in item):
                    raise RobustnessReconciliationError(
                        f"2025 entered development years at {child}."
                    )
            _reject_final_test_content(item, location=child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_final_test_content(item, location=f"{location}[{index}]")


def _iter_file_records(
    value: Mapping[str, Any],
) -> Iterable[tuple[str, Mapping[str, Any]]]:
    for key, item in value.items():
        if not isinstance(item, Mapping):
            continue
        if {"path", "sha256", "bytes"}.issubset(item):
            yield str(key), item
        else:
            yield from _iter_file_records(item)


def _resolve_output_record(
    source_name: str,
    provenance_path: Path,
    record: Mapping[str, Any],
    config: ReconciliationConfig,
) -> Path:
    raw = record.get("path")
    if not isinstance(raw, str) or not raw:
        raise RobustnessReconciliationError(f"{source_name} output path is invalid.")
    path = Path(raw)
    if path.is_absolute():
        return path.resolve()
    base = record.get("path_base")
    if base == "figure_output_directory":
        if source_name != "diagnostic_figures":
            raise RobustnessReconciliationError("Unexpected figure output base.")
        return (config.diagnostic_figure_output_directory / path).resolve()
    if base not in {None, "output_directory", "table_output_directory"}:
        raise RobustnessReconciliationError(
            f"Unknown {source_name} output path base: {base!r}."
        )
    return (provenance_path.parent / path).resolve()


def _validate_csv_frame(
    frame: pd.DataFrame,
    record: Mapping[str, Any],
    *,
    label: str,
) -> None:
    if "rows" in record and len(frame) != _integer(record["rows"], label=f"{label} rows"):
        raise RobustnessReconciliationError(f"{label} row count drifted.")
    if "columns" in record:
        columns = _sequence(record["columns"], label=f"{label} columns")
        if frame.columns.tolist() != columns:
            raise RobustnessReconciliationError(f"{label} column order drifted.")
    # The producer's schema hash commits the in-memory pre-CSV dtypes.  CSV has no
    # dtype metadata, so a later read legitimately turns datetimes into strings and
    # nullable values into floats.  The exact file byte lock above authenticates the
    # serialized table; validate the schema commitment's form here, and enforce an
    # exact column contract whenever the producer supplied one.
    if "schema_sha256" in record:
        _sha(record["schema_sha256"], label=f"{label} schema SHA")
    if frame.columns.duplicated().any():
        raise RobustnessReconciliationError(f"{label} has duplicate CSV columns.")
    if "target_date" in frame:
        dates = pd.to_datetime(frame["target_date"], errors="raise")
        if dates.dt.year.ge(2025).any():
            raise RobustnessReconciliationError(f"{label} contains a 2025 target date.")
    for column in ("year", "held_out_year"):
        if column in frame:
            years = pd.to_numeric(frame[column], errors="coerce").dropna()
            if years.ge(2025).any():
                raise RobustnessReconciliationError(f"{label} contains 2025 rows.")


def _lineage_for_source(name: str, provenance: Mapping[str, Any]) -> tuple[str, str]:
    auth = _mapping(provenance.get("input_authentication"), label=f"{name} authentication")
    if name == "initial_results":
        compile_value = provenance.get("compile_provenance_commit_sha256")
        oof_value = auth.get("oof_predictions_sha256")
    elif name == "endpoint":
        compile_value = provenance.get("compile_provenance_commit_sha256")
        oof_value = auth.get("oof_predictions_sha256")
    elif name in {"qa", "residual_spatial", "diagnostic_figures"}:
        compile_value = auth.get("compile_provenance_commit_sha256")
        oof_value = auth.get("oof_predictions_sha256")
    elif name == "feature_ablation":
        compile_value = auth.get("canonical_model_compile_commit_sha256")
        oof_value = auth.get("canonical_all_feature_oof_sha256")
    elif name == "stqa2_sensitivity":
        compile_value = auth.get("model_compile_provenance_commit_sha256")
        oof_value = auth.get("model_oof_predictions_sha256")
    else:
        raise AssertionError(name)
    return (
        _sha(compile_value, label=f"{name} compile commitment"),
        _sha(oof_value, label=f"{name} OOF hash"),
    )


def authenticate_source(
    name: str,
    config: ReconciliationConfig,
) -> AuthenticatedSource:
    """Authenticate one complete provenance and every declared output before use."""

    path = config.source_paths[name]
    try:
        provenance = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RobustnessReconciliationError(f"Required upstream is missing: {name}.") from error
    except (OSError, json.JSONDecodeError) as error:
        raise RobustnessReconciliationError(f"Required upstream is unreadable: {name}.") from error
    provenance = _mapping(provenance, label=f"{name} provenance")
    commit = _validate_content_commit(provenance, label=f"{name} provenance")
    if (
        provenance.get("state") != "complete"
        or provenance.get("algorithm_version") != _SOURCE_ALGORITHMS[name]
        or provenance.get("final_test_year") != config.final_test_year
        or provenance.get("final_test_locked") is not True
        or provenance.get("contains_final_test_year") is not False
    ):
        raise RobustnessReconciliationError(f"{name} is not a locked complete development source.")
    _reject_final_test_content(provenance, location=name)
    outputs_manifest = _mapping(provenance.get("output_files"), label=f"{name} outputs")
    if name == "residual_spatial":
        if set(outputs_manifest) != {"tables", "figures"}:
            raise RobustnessReconciliationError("Residual output container schema drifted.")
        residual_tables = _mapping(
            outputs_manifest["tables"], label="residual table outputs"
        )
        residual_figures = _mapping(
            outputs_manifest["figures"], label="residual figure outputs"
        )
        if (
            set(residual_tables)
            != {name for name in _REQUIRED_OUTPUTS[name] if name.endswith(".csv")}
            or set(residual_figures)
            != {name for name in _REQUIRED_OUTPUTS[name] if name.endswith(".png")}
        ):
            raise RobustnessReconciliationError("Residual output groups are not exact.")
    records = list(_iter_file_records(outputs_manifest))
    if {filename for filename, _ in records} != _REQUIRED_OUTPUTS[name]:
        raise RobustnessReconciliationError(f"{name} output manifest is not exact.")

    paths: dict[str, Path] = {}
    json_outputs: dict[str, Mapping[str, Any]] = {}
    csv_outputs: dict[str, pd.DataFrame] = {}
    for filename, record in records:
        output = _resolve_output_record(name, path, record, config)
        if output.name != filename or filename in paths:
            raise RobustnessReconciliationError(f"{name} output names are ambiguous.")
        try:
            observed_bytes = output.stat().st_size
        except OSError as error:
            raise RobustnessReconciliationError(
                f"Required {name} output is missing: {filename}."
            ) from error
        expected_bytes = _integer(record.get("bytes"), label=f"{name}/{filename} bytes")
        expected_sha = _sha(record.get("sha256"), label=f"{name}/{filename} SHA")
        if observed_bytes != expected_bytes or sha256_file(output) != expected_sha:
            raise RobustnessReconciliationError(f"{name}/{filename} byte lock failed.")
        paths[filename] = output
        if output.suffix.lower() == ".json":
            try:
                payload = json.loads(output.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise RobustnessReconciliationError(
                    f"{name}/{filename} JSON is unreadable."
                ) from error
            payload = _mapping(payload, label=f"{name}/{filename}")
            _reject_final_test_content(payload, location=f"{name}/{filename}")
            json_outputs[filename] = payload
        elif output.suffix.lower() == ".csv":
            try:
                frame = pd.read_csv(output)
            except pd.errors.EmptyDataError:
                if record.get("rows") != 0:
                    raise RobustnessReconciliationError(
                        f"{name}/{filename} is unexpectedly empty."
                    ) from None
                frame = pd.DataFrame()
            _validate_csv_frame(frame, record, label=f"{name}/{filename}")
            csv_outputs[filename] = frame

    summary_name = _SUMMARY_FILENAMES[name]
    summary: Mapping[str, Any] | None = json_outputs.get(summary_name)
    if summary is not None:
        if (
            summary.get("algorithm_version") != _SOURCE_ALGORITHMS[name]
            or summary.get("final_test_year") != config.final_test_year
            or summary.get("final_test_locked") is not True
            or summary.get("contains_final_test_year") is not False
            or (name != "stqa2_sensitivity" and summary.get("state") != "complete")
        ):
            raise RobustnessReconciliationError(f"{name} summary identity drifted.")
        internal_commit = summary.get("commit_sha256")
        effective_summary_commit = (
            _validate_content_commit(summary, label=f"{name} summary")
            if internal_commit is not None
            else canonical_sha256(summary)
        )
        expected_summary_commit = provenance.get("summary_commit_sha256")
        if expected_summary_commit is not None and _sha(
            expected_summary_commit, label=f"{name} summary commitment"
        ) != effective_summary_commit:
            raise RobustnessReconciliationError(f"{name} summary commitment drifted.")

    compile_commit, oof_sha = _lineage_for_source(name, provenance)
    return AuthenticatedSource(
        name=name,
        provenance_path=path,
        provenance_file_sha256=sha256_file(path),
        provenance_commit_sha256=commit,
        provenance=provenance,
        outputs=paths,
        json_outputs=json_outputs,
        csv_outputs=csv_outputs,
        compile_commit_sha256=compile_commit,
        oof_predictions_sha256=oof_sha,
    )


def authenticate_robustness_inputs(
    config: ReconciliationConfig,
) -> Mapping[str, AuthenticatedSource]:
    """Authenticate all seven sources and their shared canonical model lineage."""

    sources = {
        name: authenticate_source(name, config)
        for name in _SOURCE_ALGORITHMS
    }
    compile_commits = {source.compile_commit_sha256 for source in sources.values()}
    oof_hashes = {source.oof_predictions_sha256 for source in sources.values()}
    if len(compile_commits) != 1 or len(oof_hashes) != 1:
        raise RobustnessReconciliationError("Robustness sources do not share one model lineage.")

    figures_auth = _mapping(
        sources["diagnostic_figures"].provenance.get("input_authentication"),
        label="diagnostic figure authentication",
    )
    upstream = _mapping(
        figures_auth.get("upstream_provenance"),
        label="diagnostic figure upstream provenance",
    )
    figure_names = {
        "initial": "initial_results",
        "endpoint": "endpoint",
        "qa": "qa",
        "residual_spatial": "residual_spatial",
    }
    if set(upstream) != set(figure_names):
        raise RobustnessReconciliationError("Diagnostic figure upstream manifest drifted.")
    for recorded_name, source_name in figure_names.items():
        record = _mapping(upstream[recorded_name], label=f"figure upstream {recorded_name}")
        source = sources[source_name]
        if (
            record.get("file_sha256") != source.provenance_file_sha256
            or record.get("commit_sha256") != source.provenance_commit_sha256
        ):
            raise RobustnessReconciliationError(
                f"Diagnostic figures do not authenticate current {source_name}."
            )
    return sources


def _require_common_counts(summary: Mapping[str, Any], config: ReconciliationConfig) -> None:
    checks = {
        "tract_date_row_count": config.expected_rows,
        "independent_date_count": config.expected_dates,
        "independent_spatial_block_count": config.expected_blocks,
    }
    for field, expected in checks.items():
        if summary.get(field) != expected:
            raise RobustnessReconciliationError(f"Development count drifted: {field}.")


def _cohort(summary: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    selected = _mapping(summary.get("selected_cohorts"), label="selected QA cohorts")
    return _mapping(selected.get(name), label=f"QA cohort {name}")


def _evidence_row(
    *,
    evidence_id: str,
    domain: str,
    estimate_name: str,
    value: object,
    unit: str,
    source: AuthenticatedSource,
    interpretation: str,
    ci_lower: object = None,
    ci_upper: object = None,
    rows: object = None,
    dates: object = None,
    blocks: object = None,
    baseline_model_id: object = None,
    target_model_id: object = None,
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "domain": domain,
        "estimate_name": estimate_name,
        "estimate_value": value,
        "unit": unit,
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "baseline_model_id": baseline_model_id,
        "target_model_id": target_model_id,
        "tract_date_row_count": rows,
        "independent_date_count": dates,
        "independent_spatial_block_count": blocks,
        "interpretation": interpretation,
        "source": source.name,
        "source_commit_sha256": source.provenance_commit_sha256,
    }


def build_reconciliation(
    sources: Mapping[str, AuthenticatedSource],
    config: ReconciliationConfig,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Extract a concise, non-causal development evidence reconciliation."""

    initial = sources["initial_results"].json_outputs[_SUMMARY_FILENAMES["initial_results"]]
    endpoint = sources["endpoint"].json_outputs[_SUMMARY_FILENAMES["endpoint"]]
    qa = sources["qa"].json_outputs[_SUMMARY_FILENAMES["qa"]]
    figures = sources["diagnostic_figures"].json_outputs[
        _SUMMARY_FILENAMES["diagnostic_figures"]
    ]
    ablation = sources["feature_ablation"].json_outputs[
        _SUMMARY_FILENAMES["feature_ablation"]
    ]
    stqa = sources["stqa2_sensitivity"].json_outputs[
        _SUMMARY_FILENAMES["stqa2_sensitivity"]
    ]
    # The endpoint producer reports support inside each endpoint/sensor stratum,
    # not as redundant top-level fields. Initial and QA summaries carry the
    # complete-cohort counts directly; endpoint support is checked below.
    for summary in (initial, qa):
        _require_common_counts(summary, config)
    if (
        endpoint.get("relative_endpoint", {}).get("gated_independent_date_count")
        != config.expected_relative_dates
    ):
        raise RobustnessReconciliationError("Relative-endpoint date count drifted.")
    if (
        ablation.get("tract_date_row_count_per_family_scenario") != config.expected_rows
        or ablation.get("independent_date_count") != config.expected_dates
        or ablation.get("independent_spatial_block_count") != config.expected_blocks
    ):
        raise RobustnessReconciliationError("Feature-ablation support drifted.")

    primary = _mapping(initial.get("primary_comparison"), label="primary comparison")
    if (
        primary.get("family") != config.family
        or primary.get("strongest_legal_baseline_model_id") != config.baseline_model_id
        or primary.get("target_model_id") != config.target_model_id
        or primary.get("random_row_sampling_used") is not False
    ):
        raise RobustnessReconciliationError("Primary comparison contract drifted.")
    qa_all = _cohort(qa, "all_rows")
    qa_all_bootstrap = _mapping(
        qa_all.get("crossed_bootstrap"), label="QA all-row bootstrap"
    )
    if (
        qa_all.get("baseline_model_id") != config.baseline_model_id
        or qa_all.get("target_model_id") != config.target_model_id
        or qa_all_bootstrap.get("random_row_sampling_used") is not False
        or qa_all_bootstrap.get("bootstrap_seed") == primary.get("bootstrap_seed")
    ):
        raise RobustnessReconciliationError("Primary and QA rerun distinction drifted.")

    qa_interpretation = _mapping(qa.get("qa_interpretation"), label="QA interpretation")
    if (
        qa_interpretation.get("st_qa_2k_cohort_is_tract_summary_filter") is not True
        or qa_interpretation.get("pixel_level_st_qa_hard_mask_reaggregated") is not False
        or qa_interpretation.get("tract_summary_filter_replaces_pixel_level_sensitivity")
        is not False
    ):
        raise RobustnessReconciliationError("Tract-summary ST_QA interpretation drifted.")
    if (
        stqa.get("strict_pixel_rule") != "ST_QA <= 2.0 K before tract aggregation"
        or stqa.get("frozen_primary_oof_refit_performed") is not False
        or stqa.get("fixed_support_invariant_pass") is not True
    ):
        raise RobustnessReconciliationError("Strict pixel-level ST_QA contract drifted.")
    ablation_interpretation = _mapping(
        ablation.get("interpretation"), label="feature-ablation interpretation"
    )
    if (
        ablation_interpretation.get("causal_feature_importance") is not False
        or ablation_interpretation.get("leave_one_feature_family_out") is not False
        or ablation_interpretation.get("feature_importance_claim_allowed") is not False
    ):
        raise RobustnessReconciliationError("Feature-ablation causal guard drifted.")

    endpoint_focus = _mapping(
        _mapping(endpoint.get("relative_endpoint"), label="relative endpoint").get(
            "focus_joint_models"
        ),
        label="endpoint focus models",
    )
    if set(endpoint_focus) != {config.baseline_model_id, config.target_model_id}:
        raise RobustnessReconciliationError("Endpoint focus models drifted.")
    sensor_rows = _sequence(endpoint.get("sensor_diagnostics"), label="sensor diagnostics")
    sensor_lookup: dict[tuple[str, str], Mapping[str, Any]] = {}
    for sensor_raw in sensor_rows:
        sensor = _mapping(sensor_raw, label="sensor diagnostic row")
        key = (str(sensor.get("model_id")), str(sensor.get("platform")))
        if key in sensor_lookup:
            raise RobustnessReconciliationError("Sensor diagnostics contain duplicate rows.")
        sensor_lookup[key] = sensor
    platforms = sorted(
        {
            platform
            for model_id, platform in sensor_lookup
            if model_id in {config.baseline_model_id, config.target_model_id}
        }
    )
    if len(platforms) != 2 or set(sensor_lookup) != {
        (model_id, platform)
        for model_id in (config.baseline_model_id, config.target_model_id)
        for platform in platforms
    }:
        raise RobustnessReconciliationError("Sensor-stratified focus comparison drifted.")
    sentinel_missing = _cohort(qa, "sentinel_all_five_missing")
    stqa_summary_cohort = _cohort(qa, "tract_median_st_qa_le_2k")

    moran = sources["residual_spatial"].csv_outputs[
        _SUMMARY_FILENAMES["residual_spatial"]
    ]
    required_moran = {
        "model_id",
        "mean_morans_i_across_dates",
        "median_morans_i_across_dates",
        "date_level_observation_count",
        "positive_morans_i_date_count",
        "multiple_testing_adjustment",
    }
    if not required_moran.issubset(moran) or set(moran["model_id"].astype(str)) != {
        config.baseline_model_id,
        config.target_model_id,
    }:
        raise RobustnessReconciliationError("Residual Moran summary schema drifted.")
    moran_lookup = moran.set_index("model_id")
    if (
        not moran_lookup["date_level_observation_count"].eq(config.expected_dates).all()
        or not moran_lookup["positive_morans_i_date_count"].eq(config.expected_dates).all()
        or not moran_lookup["multiple_testing_adjustment"]
        .eq("none_descriptive_diagnostic")
        .all()
    ):
        raise RobustnessReconciliationError("Residual clustering interpretation drifted.")

    figure_files = _mapping(figures.get("figure_files"), label="diagnostic figure files")
    if len(figure_files) != config.expected_figure_count:
        raise RobustnessReconciliationError("Diagnostic figure count drifted.")

    evidence: list[dict[str, Any]] = []
    primary_common = {
        "rows": primary.get("tract_date_row_count"),
        "dates": primary.get("independent_date_count"),
        "blocks": primary.get("independent_spatial_block_count"),
        "baseline_model_id": config.baseline_model_id,
        "target_model_id": config.target_model_id,
    }
    evidence.append(
        _evidence_row(
            evidence_id="primary_joint_relative_mae_improvement",
            domain="primary_performance",
            estimate_name="relative_mae_improvement",
            value=_number(
                primary.get("relative_mae_improvement_percent"), label="primary improvement"
            ),
            unit="percent",
            ci_lower=_number(
                primary.get("relative_mae_improvement_ci_lower_percent"),
                label="primary CI lower",
            ),
            ci_upper=_number(
                primary.get("relative_mae_improvement_ci_upper_percent"),
                label="primary CI upper",
            ),
            source=sources["initial_results"],
            interpretation="primary_predeclared_crossed_date_by_block_interval",
            **primary_common,
        )
    )
    evidence.append(
        _evidence_row(
            evidence_id="qa_all_rows_relative_mae_improvement_rerun",
            domain="qa_sensitivity",
            estimate_name="relative_mae_improvement",
            value=_number(
                qa_all_bootstrap.get("relative_mae_improvement_percent"),
                label="QA rerun improvement",
            ),
            unit="percent",
            ci_lower=_number(
                qa_all_bootstrap.get("relative_mae_improvement_ci_lower_percent"),
                label="QA rerun CI lower",
            ),
            ci_upper=_number(
                qa_all_bootstrap.get("relative_mae_improvement_ci_upper_percent"),
                label="QA rerun CI upper",
            ),
            rows=qa_all.get("tract_date_row_count"),
            dates=qa_all.get("independent_date_count"),
            blocks=qa_all.get("independent_spatial_block_count"),
            baseline_model_id=config.baseline_model_id,
            target_model_id=config.target_model_id,
            source=sources["qa"],
            interpretation="separate_fixed_seed_qa_rerun_not_the_primary_interval",
        )
    )
    for model_id in (config.baseline_model_id, config.target_model_id):
        values = _mapping(endpoint_focus[model_id], label=f"endpoint {model_id}")
        evidence.append(
            _evidence_row(
                evidence_id=f"hotspot_mean_per_date_ap_{model_id.lower()}",
                domain="relative_hotspot",
                estimate_name="mean_per_date_average_precision",
                value=_number(
                    values.get("mean_per_date_average_precision"),
                    label=f"{model_id} hotspot AP",
                ),
                unit="fraction",
                rows=values.get("tract_date_row_count"),
                dates=values.get("independent_date_count"),
                blocks=values.get("independent_spatial_block_count"),
                target_model_id=model_id,
                source=sources["endpoint"],
                interpretation="relative_endpoint_only_on_predeclared_coverage_gate_dates",
            )
        )
    for platform in platforms:
        baseline_sensor = sensor_lookup[(config.baseline_model_id, platform)]
        target_sensor = sensor_lookup[(config.target_model_id, platform)]
        baseline_mae = _number(
            baseline_sensor.get("equal_date_weighted_mae_c"),
            label=f"{platform} baseline MAE",
        )
        target_mae = _number(
            target_sensor.get("equal_date_weighted_mae_c"),
            label=f"{platform} target MAE",
        )
        if baseline_mae <= 0:
            raise RobustnessReconciliationError("Sensor baseline MAE must be positive.")
        evidence.append(
            _evidence_row(
                evidence_id=f"sensor_{platform}_relative_mae_improvement",
                domain="sensor_sensitivity",
                estimate_name="relative_mae_improvement",
                value=100.0 * (baseline_mae - target_mae) / baseline_mae,
                unit="percent",
                rows=target_sensor.get("tract_date_row_count"),
                dates=target_sensor.get("independent_date_count"),
                blocks=target_sensor.get("independent_spatial_block_count"),
                baseline_model_id=config.baseline_model_id,
                target_model_id=config.target_model_id,
                source=sources["endpoint"],
                interpretation="sensor_stratified_development_oof_association",
            )
        )
    for cohort_id, cohort, interpretation in (
        (
            "tract_summary_stqa_le2k_improvement",
            stqa_summary_cohort,
            "tract_median_summary_filter_not_pixel_level_reaggregation",
        ),
        (
            "sentinel_all_missing_improvement",
            sentinel_missing,
            "exploratory_sparse_group_wide_uncertainty",
        ),
    ):
        bootstrap = _mapping(cohort.get("crossed_bootstrap"), label=cohort_id)
        evidence.append(
            _evidence_row(
                evidence_id=cohort_id,
                domain="qa_sensitivity",
                estimate_name="relative_mae_improvement",
                value=_number(
                    bootstrap.get("relative_mae_improvement_percent"), label=cohort_id
                ),
                unit="percent",
                ci_lower=_number(
                    bootstrap.get("relative_mae_improvement_ci_lower_percent"),
                    label=f"{cohort_id} CI lower",
                ),
                ci_upper=_number(
                    bootstrap.get("relative_mae_improvement_ci_upper_percent"),
                    label=f"{cohort_id} CI upper",
                ),
                rows=cohort.get("tract_date_row_count"),
                dates=cohort.get("independent_date_count"),
                blocks=cohort.get("independent_spatial_block_count"),
                baseline_model_id=config.baseline_model_id,
                target_model_id=config.target_model_id,
                source=sources["qa"],
                interpretation=interpretation,
            )
        )
    for model_id in (config.baseline_model_id, config.target_model_id):
        row = moran_lookup.loc[model_id]
        evidence.append(
            _evidence_row(
                evidence_id=f"mean_date_morans_i_{model_id.lower()}",
                domain="residual_spatial",
                estimate_name="mean_morans_i_across_dates",
                value=_number(row["mean_morans_i_across_dates"], label="Moran's I"),
                unit="index",
                dates=row["date_level_observation_count"],
                target_model_id=model_id,
                source=sources["residual_spatial"],
                interpretation="exploratory_unadjusted_spatial_clustering_limitation",
            )
        )
    comparisons = _sequence(ablation.get("joint_comparisons"), label="ablation comparisons")
    if len(comparisons) != 3:
        raise RobustnessReconciliationError("Feature-ablation comparison count drifted.")
    for comparison_raw in comparisons:
        comparison = _mapping(comparison_raw, label="ablation comparison")
        scenario = str(comparison.get("reduced_scenario_id"))
        evidence.append(
            _evidence_row(
                evidence_id=f"ablation_all_features_vs_{scenario}",
                domain="feature_ablation",
                estimate_name="all_features_relative_mae_improvement_over_reduced_refit",
                value=100.0
                * _number(
                    comparison.get("relative_mae_improvement_fraction"),
                    label=f"ablation {scenario} improvement",
                ),
                unit="percent",
                ci_lower=100.0
                * _number(
                    comparison.get("relative_mae_improvement_ci_lower_fraction"),
                    label=f"ablation {scenario} CI lower",
                ),
                ci_upper=100.0
                * _number(
                    comparison.get("relative_mae_improvement_ci_upper_fraction"),
                    label=f"ablation {scenario} CI upper",
                ),
                rows=ablation.get("tract_date_row_count_per_family_scenario"),
                dates=ablation.get("independent_date_count"),
                blocks=ablation.get("independent_spatial_block_count"),
                target_model_id=config.target_model_id,
                source=sources["feature_ablation"],
                interpretation="predictive_association_not_causal_feature_importance",
            )
        )

    strict_bootstrap_raw = stqa.get("frozen_primary_oof_bootstrap")
    strict_estimable = stqa.get("frozen_primary_oof_sensitivity_estimable") is True
    strict_value = None
    strict_lower = None
    strict_upper = None
    strict_rows = stqa.get("strict_analysis_label_row_count")
    strict_dates = stqa.get("strict_usable_date_count")
    strict_blocks = None
    strict_interpretation = "strict_pixel_hard_mask_existing_oof_no_refit"
    if strict_estimable:
        strict_bootstrap = _mapping(strict_bootstrap_raw, label="strict STQA bootstrap")
        strict_value = _number(
            strict_bootstrap.get("relative_mae_improvement_percent"),
            label="strict STQA improvement",
        )
        strict_lower = _number(
            strict_bootstrap.get("relative_mae_improvement_ci_lower_percent"),
            label="strict STQA CI lower",
        )
        strict_upper = _number(
            strict_bootstrap.get("relative_mae_improvement_ci_upper_percent"),
            label="strict STQA CI upper",
        )
        strict_rows = strict_bootstrap.get("tract_date_row_count")
        strict_dates = strict_bootstrap.get("independent_date_count")
        strict_blocks = strict_bootstrap.get("independent_spatial_block_count")
    else:
        strict_interpretation = (
            "strict_pixel_hard_mask_sensitivity_not_estimable:"
            f"{stqa.get('frozen_primary_oof_bootstrap_not_estimable_reason')}"
        )
    evidence.append(
        _evidence_row(
            evidence_id="strict_pixel_stqa2_frozen_oof_improvement",
            domain="pixel_level_stqa_sensitivity",
            estimate_name="relative_mae_improvement",
            value=strict_value,
            unit="percent",
            ci_lower=strict_lower,
            ci_upper=strict_upper,
            rows=strict_rows,
            dates=strict_dates,
            blocks=strict_blocks,
            baseline_model_id=config.baseline_model_id,
            target_model_id=config.target_model_id,
            source=sources["stqa2_sensitivity"],
            interpretation=strict_interpretation,
        )
    )
    evidence.append(
        _evidence_row(
            evidence_id="diagnostic_figure_count",
            domain="reporting",
            estimate_name="authenticated_figure_count",
            value=len(figure_files),
            unit="count",
            source=sources["diagnostic_figures"],
            interpretation="development_diagnostic_figures_only_not_final_test",
        )
    )
    evidence_frame = pd.DataFrame(evidence)

    summary: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "complete",
        "analysis_scope": "locked_2020_2024_development_robustness_reconciliation",
        "final_test_year": config.final_test_year,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "tract_date_row_count": config.expected_rows,
        "independent_date_count": config.expected_dates,
        "independent_spatial_block_count": config.expected_blocks,
        "primary_comparison": dict(primary),
        "qa_all_rows_rerun": dict(qa_all_bootstrap),
        "relative_endpoint": dict(endpoint.get("relative_endpoint", {})),
        "sensor_diagnostics": endpoint.get("sensor_diagnostics"),
        "feature_ablation_joint_comparisons": comparisons,
        "strict_pixel_stqa2_sensitivity": {
            "strict_pixel_rule": stqa.get("strict_pixel_rule"),
            "strict_target_stage_state": stqa.get("strict_target_stage_state"),
            "strict_usable_date_count": stqa.get("strict_usable_date_count"),
            "minimum_required_usable_date_count": stqa.get(
                "minimum_required_usable_date_count"
            ),
            "strict_minimum_date_gate_pass": stqa.get("strict_minimum_date_gate_pass"),
            "frozen_primary_oof_sensitivity_estimable": strict_estimable,
            "frozen_primary_oof_bootstrap": strict_bootstrap_raw,
            "frozen_primary_oof_refit_performed": False,
        },
        "residual_spatial": {
            model_id: {
                "mean_morans_i_across_dates": float(
                    moran_lookup.loc[model_id, "mean_morans_i_across_dates"]
                ),
                "median_morans_i_across_dates": float(
                    moran_lookup.loc[model_id, "median_morans_i_across_dates"]
                ),
                "positive_morans_i_date_count": int(
                    moran_lookup.loc[model_id, "positive_morans_i_date_count"]
                ),
            }
            for model_id in (config.baseline_model_id, config.target_model_id)
        },
        "interpretive_contract": {
            "primary_ci_and_qa_rerun_are_distinct": True,
            "primary_bootstrap_seed": primary.get("bootstrap_seed"),
            "qa_rerun_bootstrap_seed": qa_all_bootstrap.get("bootstrap_seed"),
            "qa_tract_summary_stqa_is_not_pixel_hard_mask": True,
            "strict_stqa_rule_is_pixel_level_before_tract_aggregation": True,
            "feature_ablation_supports_predictive_association_not_causation": True,
            "sparse_groups_are_exploratory": True,
            "sentinel_missing_group": {
                "tract_date_row_count": sentinel_missing.get("tract_date_row_count"),
                "independent_date_count": sentinel_missing.get("independent_date_count"),
                "independent_spatial_block_count": sentinel_missing.get(
                    "independent_spatial_block_count"
                ),
            },
            "residual_spatial_clustering_remains_a_limitation": True,
            "moran_p_values_are_exploratory_and_unadjusted": True,
            "lst_is_surface_heat_hazard_proxy_not_human_exposure": True,
        },
        "robustness_readiness": {
            "all_required_upstreams_complete_and_authenticated": True,
            "development_evidence_ready_for_model_lock_review": True,
            "model_lock_created": False,
            "automatic_final_test_unlock_authorized": False,
        },
        "input_authentication": {
            "shared_model_compile_commit_sha256": next(
                iter({source.compile_commit_sha256 for source in sources.values()})
            ),
            "shared_oof_predictions_sha256": next(
                iter({source.oof_predictions_sha256 for source in sources.values()})
            ),
            "sources": {
                name: {
                    "provenance_path": source.provenance_path.as_posix(),
                    "provenance_file_sha256": source.provenance_file_sha256,
                    "provenance_commit_sha256": source.provenance_commit_sha256,
                    "authenticated_output_count": len(source.outputs),
                }
                for name, source in sources.items()
            },
        },
    }
    summary["commit_sha256"] = canonical_sha256(summary)
    return summary, evidence_frame


def _begin_output_transaction(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / PROVENANCE_FILENAME).unlink(missing_ok=True)


def _csv_record(path: Path, frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "path": path.name,
        "path_base": "output_directory",
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "rows": len(frame),
        "schema_sha256": canonical_sha256(
            [(str(column), str(dtype)) for column, dtype in frame.dtypes.items()]
        ),
    }


def _json_record(path: Path) -> dict[str, Any]:
    return {
        "path": path.name,
        "path_base": "output_directory",
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def reconcile_development_robustness(
    config_path: str | Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Authenticate, reconcile, and commit development evidence; never unlock 2025."""

    config = load_reconciliation_config(config_path)
    _begin_output_transaction(config.output_directory)
    sources = authenticate_robustness_inputs(config)
    summary, evidence = build_reconciliation(sources, config)
    summary_path = config.output_directory / SUMMARY_FILENAME
    evidence_path = config.output_directory / EVIDENCE_FILENAME
    atomic_json(summary, summary_path)
    atomic_csv(evidence, evidence_path)
    pipeline_sha, pipeline_fingerprint = code_runtime_fingerprint(
        project_root=_root(),
        relative_paths=_PIPELINE_FILES,
        algorithm_version=ALGORITHM_VERSION,
    )
    provenance: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "complete",
        "ready_for_development_robustness_interpretation": True,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "analysis_scope": "locked_2020_2024_development_robustness_reconciliation",
        "final_test_year": config.final_test_year,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "analysis_config": {
            "path": config.path.as_posix(),
            "sha256": sha256_file(config.path),
            "semantic_sha256": config.semantic_sha256,
        },
        "input_authentication": summary["input_authentication"],
        "scientific_contract": {
            "primary_and_qa_bootstrap_intervals_kept_distinct": True,
            "tract_summary_and_pixel_hard_mask_kept_distinct": True,
            "causal_feature_importance_claim_allowed": False,
            "sparse_group_claims_are_exploratory": True,
            "residual_spatial_clustering_reported_as_limitation": True,
            "models_fitted": False,
            "random_row_resampling_used": False,
            "final_test_unlocked": False,
            "automatic_final_test_unlock_authorized": False,
        },
        "pipeline_sha256": pipeline_sha,
        "pipeline_fingerprint": pipeline_fingerprint,
        "summary_commit_sha256": summary["commit_sha256"],
        "output_files": {
            SUMMARY_FILENAME: _json_record(summary_path),
            EVIDENCE_FILENAME: _csv_record(evidence_path, evidence),
        },
    }
    provenance["commit_sha256"] = canonical_sha256(provenance)
    atomic_json(provenance, config.output_directory / PROVENANCE_FILENAME)
    return provenance
