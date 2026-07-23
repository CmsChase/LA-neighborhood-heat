"""Generate one authenticated, development-only scientific report."""

from __future__ import annotations

import json
import math
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pandas as pd

from la_heat.provenance import (
    atomic_json,
    atomic_text,
    canonical_sha256,
    code_runtime_fingerprint,
    sha256_file,
)
from la_heat.robustness_reconciliation import (
    AuthenticatedSource,
    authenticate_robustness_inputs,
    load_reconciliation_config,
)

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "development-report-v1"
FROZEN_STATE: Final = "frozen_development_reporting"
DEFAULT_CONFIG: Final = Path("configs/development_report.toml")
REPORT_SCOPE: Final = "locked_2020_2024_development_report_only"


class DevelopmentReportError(RuntimeError):
    """Raised when report inputs or scientific boundaries are invalid."""


@dataclass(frozen=True)
class DevelopmentReportConfig:
    path: Path
    semantic_sha256: str
    paths: Mapping[str, Path]
    final_test_year: int
    development_years: tuple[int, ...]
    expected_rows: int
    expected_dates: int
    expected_blocks: int
    expected_relative_dates: int
    family: str
    baseline_model_id: str
    target_model_id: str
    prediction_origin: str
    latest_dynamic_offset_days: int


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DevelopmentReportError(f"{label} must be an object.")
    return value


def _sequence(value: object, *, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise DevelopmentReportError(f"{label} must be a list.")
    return value


def _number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DevelopmentReportError(f"{label} must be numeric.")
    result = float(value)
    if not math.isfinite(result):
        raise DevelopmentReportError(f"{label} must be finite.")
    return result


def _integer(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DevelopmentReportError(f"{label} must be an integer.")
    return value


def _resolve(root: Path, value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise DevelopmentReportError(f"{label} must be a non-empty path.")
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def load_development_report_config(
    path: str | Path = DEFAULT_CONFIG,
) -> DevelopmentReportConfig:
    """Load the exact frozen report contract."""

    root = _root()
    config_path = _resolve(root, str(path), label="report config")
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    if set(raw) != {"schema_version", "algorithm_version", "state", "paths", "analysis"}:
        raise DevelopmentReportError("Report config top-level schema drifted.")
    if (
        raw["schema_version"] != SCHEMA_VERSION
        or raw["algorithm_version"] != ALGORITHM_VERSION
        or raw["state"] != FROZEN_STATE
    ):
        raise DevelopmentReportError("Report config identity drifted.")
    paths = _mapping(raw["paths"], label="paths")
    expected_paths = {
        "research_config",
        "model_selection_config",
        "robustness_reconciliation_config",
        "literature_evidence",
        "initial_results_provenance",
        "initial_results_summary",
        "endpoint_provenance",
        "endpoint_summary",
        "qa_provenance",
        "qa_summary",
        "residual_spatial_provenance",
        "residual_spatial_summary",
        "diagnostic_figures_provenance",
        "diagnostic_figures_summary",
        "diagnostic_figure_output_directory",
        "feature_ablation_provenance",
        "feature_ablation_summary",
        "stqa2_sensitivity_provenance",
        "stqa2_sensitivity_summary",
        "robustness_reconciliation_provenance",
        "robustness_reconciliation_summary",
        "output_report",
        "output_provenance",
    }
    if set(paths) != expected_paths:
        raise DevelopmentReportError("Report path schema drifted.")
    analysis = _mapping(raw["analysis"], label="analysis")
    expected_analysis = {
        "final_test_year",
        "final_test_locked",
        "development_years",
        "expected_tract_date_row_count",
        "expected_independent_date_count",
        "expected_independent_spatial_block_count",
        "expected_relative_endpoint_date_count",
        "family",
        "baseline_model_id",
        "target_model_id",
        "prediction_origin_local_time",
        "latest_dynamic_predictor_offset_days",
    }
    if set(analysis) != expected_analysis:
        raise DevelopmentReportError("Report analysis schema drifted.")
    years = tuple(_sequence(analysis["development_years"], label="development years"))
    if (
        years != (2020, 2021, 2022, 2023, 2024)
        or analysis["final_test_year"] != 2025
        or analysis["final_test_locked"] is not True
        or analysis["prediction_origin_local_time"] != "00:00:00"
        or analysis["latest_dynamic_predictor_offset_days"] != -1
    ):
        raise PermissionError("Development reporting must keep the frozen 2025 lock.")
    semantic = {
        "schema_version": raw["schema_version"],
        "algorithm_version": raw["algorithm_version"],
        "state": raw["state"],
        "paths": dict(paths),
        "analysis": dict(analysis),
    }
    return DevelopmentReportConfig(
        path=config_path,
        semantic_sha256=canonical_sha256(semantic),
        paths={name: _resolve(root, value, label=name) for name, value in paths.items()},
        final_test_year=2025,
        development_years=years,
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
        family=str(analysis["family"]),
        baseline_model_id=str(analysis["baseline_model_id"]),
        target_model_id=str(analysis["target_model_id"]),
        prediction_origin="00:00:00",
        latest_dynamic_offset_days=-1,
    )


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DevelopmentReportError(f"Cannot read valid {label}: {path}") from error
    if not isinstance(payload, dict):
        raise DevelopmentReportError(f"{label} must be an object.")
    return payload


def _verify_commit(payload: Mapping[str, Any], *, label: str) -> str:
    working = dict(payload)
    recorded = working.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(working) != recorded:
        raise DevelopmentReportError(f"{label} content commitment is invalid.")
    return recorded


def _reject_final_test(value: object, *, location: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{location}.{key}"
            if key in {"unlock_final_test", "final_test_unlocked"} and item is not False:
                raise PermissionError(f"Final-test unlock detected at {child}.")
            if key == "contains_final_test_year" and item is not False:
                raise PermissionError(f"Final-test content detected at {child}.")
            _reject_final_test(item, location=child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_final_test(item, location=f"{location}[{index}]")


def _authenticate_reconciliation(
    config: DevelopmentReportConfig,
    sources: Mapping[str, AuthenticatedSource],
) -> tuple[dict[str, Any], pd.DataFrame, dict[str, Any]]:
    provenance_path = config.paths["robustness_reconciliation_provenance"]
    provenance = _read_json(provenance_path, label="reconciliation provenance")
    _verify_commit(provenance, label="reconciliation provenance")
    if (
        provenance.get("state") != "complete"
        or provenance.get("analysis_scope")
        != "locked_2020_2024_development_robustness_reconciliation"
        or provenance.get("final_test_year") != 2025
        or provenance.get("final_test_locked") is not True
        or provenance.get("contains_final_test_year") is not False
        or provenance.get("ready_for_development_robustness_interpretation") is not True
    ):
        raise DevelopmentReportError("Reconciliation is not complete and development-only.")
    _reject_final_test(provenance)
    records = _mapping(provenance.get("output_files"), label="reconciliation outputs")
    expected_outputs = {
        "robustness_reconciliation_summary.json",
        "robustness_evidence.csv",
    }
    if set(records) != expected_outputs:
        raise DevelopmentReportError("Reconciliation output set drifted.")
    resolved: dict[str, Path] = {}
    for name, raw_record in records.items():
        record = _mapping(raw_record, label=f"reconciliation/{name}")
        path = provenance_path.parent / name
        if (
            record.get("path") != name
            or not path.is_file()
            or record.get("sha256") != sha256_file(path)
            or record.get("bytes") != path.stat().st_size
        ):
            raise DevelopmentReportError(f"Reconciliation byte lock failed: {name}")
        resolved[name] = path
    summary = _read_json(
        resolved["robustness_reconciliation_summary.json"],
        label="reconciliation summary",
    )
    summary_commit = _verify_commit(summary, label="reconciliation summary")
    if provenance.get("summary_commit_sha256") != summary_commit:
        raise DevelopmentReportError("Reconciliation summary commitment drifted.")
    evidence = pd.read_csv(resolved["robustness_evidence.csv"])
    evidence_record = _mapping(
        records["robustness_evidence.csv"], label="reconciliation evidence"
    )
    if len(evidence) != evidence_record.get("rows") or evidence["evidence_id"].duplicated().any():
        raise DevelopmentReportError("Reconciliation evidence support drifted.")
    source_records = _mapping(
        _mapping(summary.get("input_authentication"), label="input authentication").get(
            "sources"
        ),
        label="source authentication",
    )
    if set(source_records) != set(sources):
        raise DevelopmentReportError("Reconciliation source set drifted.")
    for name, source in sources.items():
        record = _mapping(source_records[name], label=f"source/{name}")
        if (
            record.get("provenance_file_sha256") != source.provenance_file_sha256
            or record.get("provenance_commit_sha256") != source.provenance_commit_sha256
        ):
            raise DevelopmentReportError(f"Reconciliation source drifted: {name}")
    _reject_final_test(summary)
    return summary, evidence, provenance


def _authenticate_configs(config: DevelopmentReportConfig) -> dict[str, Any]:
    research_path = config.paths["research_config"]
    selection_path = config.paths["model_selection_config"]
    with research_path.open("rb") as handle:
        research = tomllib.load(handle)
    with selection_path.open("rb") as handle:
        selection = tomllib.load(handle)
    study = _mapping(research.get("study"), label="research study")
    if (
        study.get("final_test_year") != 2025
        or study.get("unlock_final_test") is not False
        or selection.get("final_test_year") != 2025
        or selection.get("unlock_final_test") is not False
        or tuple(selection.get("development_years", [])) != config.development_years
    ):
        raise PermissionError("Research or model-selection configuration unlocked 2025.")
    return {
        "research_config_sha256": sha256_file(research_path),
        "model_selection_config_sha256": sha256_file(selection_path),
        "literature_evidence_sha256": sha256_file(config.paths["literature_evidence"]),
    }


def _pct(value: object) -> str:
    return f"{_number(value, label='percent'):.1f}%"


def _f(value: object, digits: int = 3) -> str:
    return f"{_number(value, label='value'):.{digits}f}"


def _relative_image(report_path: Path, image_path: Path) -> str:
    return Path(os.path.relpath(image_path, report_path.parent)).as_posix()


def _render_report(
    config: DevelopmentReportConfig,
    sources: Mapping[str, AuthenticatedSource],
    reconciliation: Mapping[str, Any],
) -> str:
    initial = sources["initial_results"].json_outputs["model_results_initial_summary.json"]
    endpoint = sources["endpoint"].json_outputs["model_endpoint_diagnostics_summary.json"]
    qa = sources["qa"].json_outputs["model_qa_diagnostics_summary.json"]
    primary = _mapping(initial.get("primary_comparison"), label="primary comparison")
    gates = _mapping(initial.get("protocol_success_gates"), label="protocol gates")
    relative = _mapping(endpoint.get("relative_endpoint"), label="relative endpoint")
    focus = _mapping(relative.get("focus_joint_models"), label="focus models")
    strict = _mapping(
        reconciliation.get("strict_pixel_stqa2_sensitivity"), label="strict sensitivity"
    )
    ablations = _sequence(
        reconciliation.get("feature_ablation_joint_comparisons"), label="ablations"
    )
    residual = _mapping(reconciliation.get("residual_spatial"), label="residual")
    caveats = _mapping(qa.get("qa_interpretation"), label="QA interpretation")
    if (
        primary.get("tract_date_row_count") != config.expected_rows
        or primary.get("independent_date_count") != config.expected_dates
        or primary.get("independent_spatial_block_count") != config.expected_blocks
        or relative.get("gated_independent_date_count") != config.expected_relative_dates
        or caveats.get("st_qa_2k_cohort_is_tract_summary_filter") is not True
    ):
        raise DevelopmentReportError("Report support or QA interpretation drifted.")

    ablation_rows = []
    for raw in ablations:
        row = _mapping(raw, label="ablation row")
        ablation_rows.append(
            "| {description} | {reduced} | {all_features} | {improvement} "
            "({lower} to {upper}) |".format(
                description=str(row["reduced_feature_set_description"]),
                reduced=_f(row["reduced_date_macro_mae_c"]),
                all_features=_f(row["all_features_date_macro_mae_c"]),
                improvement=_pct(100.0 * _number(
                    row["relative_mae_improvement_fraction"], label="ablation improvement"
                )),
                lower=_pct(100.0 * _number(
                    row["relative_mae_improvement_ci_lower_fraction"], label="ablation lower"
                )),
                upper=_pct(100.0 * _number(
                    row["relative_mae_improvement_ci_upper_fraction"], label="ablation upper"
                )),
            )
        )
    strict_bootstrap = strict.get("frozen_primary_oof_bootstrap")
    if strict.get("frozen_primary_oof_sensitivity_estimable") is True:
        strict_values = _mapping(strict_bootstrap, label="strict bootstrap")
        strict_sentence = (
            f"On the {strict_values['independent_date_count']} retained dates, frozen primary "
            f"OOF predictions showed a {_pct(strict_values['relative_mae_improvement_percent'])} "
            "M2 improvement, but the crossed-cluster 95% interval "
            f"({_pct(strict_values['relative_mae_improvement_ci_lower_percent'])} to "
            f"{_pct(strict_values['relative_mae_improvement_ci_upper_percent'])}) crossed zero."
        )
    else:
        strict_sentence = "The frozen-OOF strict-label comparison was not estimable."

    report_path = config.paths["output_report"]
    image_names = (
        "joint_performance_overview.png",
        "qa_cohort_improvement_forest.png",
        "worst_date_errors.png",
        "fixed_date_lst_prediction_maps.png",
    )
    image_links = {
        name: _relative_image(report_path, sources["diagnostic_figures"].outputs[name])
        for name in image_names
    }
    residual_image = _relative_image(
        report_path,
        sources["residual_spatial"].outputs["joint_m2_b1_residual_diagnostics_map.png"],
    )
    return f"""# Predicting neighborhood-scale surface heat in Los Angeles

## Development-only result report

**Research question.** Can public weather, land-use/geography, and lagged
satellite-derived features predict neighborhood-level urban surface-heat risk?

This report covers only the locked 2020–2024 development evaluation. Calendar
year 2025 remains untouched and locked. The response is QA-filtered daytime
Landsat land-surface temperature (LST), a clear-sky surface-heat hazard proxy;
it is not air temperature, personal heat exposure, illness, or mortality.

## Main result

Across {config.expected_rows:,} legal tract-date observations, {config.expected_dates}
independent overpass dates, and {config.expected_blocks} spatial blocks, the strongest
legal baseline ({config.baseline_model_id}) had date-macro MAE
{_f(primary['baseline_point_mae_c'])} °C. The full nonlinear model
({config.target_model_id}) had MAE {_f(primary['target_model_point_mae_c'])} °C,
an improvement of {_f(primary['absolute_mae_improvement_c'])} °C or
{_pct(primary['relative_mae_improvement_percent'])}. The predeclared 5,000-draw
crossed date-by-block bootstrap interval was
{_pct(primary['relative_mae_improvement_ci_lower_percent'])} to
{_pct(primary['relative_mae_improvement_ci_upper_percent'])};
`P(improvement > 0)={_f(primary['probability_improvement_gt_zero'])}`.
Median per-date Spearman correlation was
{_f(gates['observed_median_per_date_spearman'])}. The required development gates
passed, while the stronger claim that the entire interval exceeds 10% did not.

![Joint development performance]({image_links['joint_performance_overview.png']})

## Design and leakage controls

- Prediction origin was 00:00 local time on the target date; dynamic observed
  predictors ended at day −1. This is a historical hindcast, not an operational
  weather forecast.
- Predictors comprised public land-use/geography, calendar, lagged Daymet weather,
  and Sentinel-2 composites. Landsat thermal data, target-derived fields,
  same-scene optical data, future data, and tract identifiers were prohibited.
- Validation held out whole years, fixed spatial blocks, and joint year × block
  combinations. Preprocessing and tuning were fitted inside the training fold.
- Confidence intervals resampled complete dates and complete spatial blocks, never
  individual tract-date rows.

## Relative hotspot endpoint

The exact top-20% hotspot endpoint was evaluated only on the
{config.expected_relative_dates} dates that passed the frozen spatial coverage gate.
Mean per-date average precision increased from
{_f(focus[config.baseline_model_id]['mean_per_date_average_precision'])} to
{_f(focus[config.target_model_id]['mean_per_date_average_precision'])}; exact top-20%
recall increased from
{_f(focus[config.baseline_model_id]['mean_per_date_recall_at_k'])} to
{_f(focus[config.target_model_id]['mean_per_date_recall_at_k'])}.

## Feature-set diagnostics

Each reduced feature set was refitted under the frozen grouped splits. Positive
values mean the full model performed better. These comparisons show predictive
association and do not identify causal effects or single-feature importance.

| Reduced refit | Reduced MAE (°C) | All-feature MAE (°C) | Full-model improvement (95% CI) |
|---|---:|---:|---:|
{chr(10).join(ablation_rows)}

## QA sensitivity and failure cases

The prespecified pixel-level `ST_QA ≤ 2 K` rebuild completed all 90 dates, but
only {strict['strict_usable_date_count']} passed the unchanged usable-date rule,
below the required {strict['minimum_required_usable_date_count']}. Therefore the
strict target was not promoted. {strict_sentence} The earlier tract-median
`ST_QA ≤ 2 K` cohort remains a separate summary diagnostic and is not a substitute
for this pixel-level rebuild.

![QA sensitivity intervals]({image_links['qa_cohort_improvement_forest.png']})

The all-five-Sentinel-missing group contains only 168 rows, 12 dates, and 29
blocks; its interval is too wide for a general conclusion. Several entire dates
also show large signed errors, demonstrating vulnerability to unusual overpass
conditions.

![Worst grouped-OOF dates]({image_links['worst_date_errors.png']})

## Spatial diagnostics

Mean date-level Moran's I decreased from
{_f(residual[config.baseline_model_id]['mean_morans_i_across_dates'])} for
{config.baseline_model_id} to
{_f(residual[config.target_model_id]['mean_morans_i_across_dates'])} for
{config.target_model_id}, but remained positive on all {config.expected_dates} dates.
The model reduces but does not remove strong spatial residual clustering. The
permutation p-values are exploratory and unadjusted.

![Spatial residual diagnostics]({residual_image})

## Fixed-date maps

These dates were fixed by the protocol rather than chosen after viewing model
performance. The October date failed the relative-endpoint coverage gate but
remains useful as a failure-case map.

![Observed, predicted, and residual maps]({image_links['fixed_date_lst_prediction_maps.png']})

## Conclusion

The locked development evidence supports the limited statement that public
weather, land-use/geography, and lagged optical-satellite features predict
clear-sky neighborhood-scale Landsat LST better than the strongest legal baseline
under joint spatiotemporal validation. It does not establish causation, human heat
exposure, health effects, or final-year generalization. The strict 2 K sensitivity
lost too many independent dates and its interval crossed zero, while residuals
remained spatially clustered; both materially limit the strength of the claim.

## Reproducibility and sources

Every value above is generated from authenticated tables under
`reports/tables/`; no report value was hand-edited. The source-to-claim literature
map is in [`docs/LITERATURE_EVIDENCE.md`](../docs/LITERATURE_EVIDENCE.md), including
USGS Landsat Collection 2 documentation, Sentinel-2 L2A documentation, Daymet V4
R1, structured cross-validation, Moran's I, and the LST-versus-exposure boundary.

**Current boundary:** `final_test_year=2025`, `final_test_locked=true`,
`final_test_used=false`.
"""


def generate_development_report(
    config_path: str | Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Authenticate every result, write the report, then commit its provenance."""

    config = load_development_report_config(config_path)
    report_path = config.paths["output_report"]
    provenance_path = config.paths["output_provenance"]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    provenance_path.unlink(missing_ok=True)
    report_path.unlink(missing_ok=True)

    config_locks = _authenticate_configs(config)
    reconciliation_config = load_reconciliation_config(
        config.paths["robustness_reconciliation_config"]
    )
    expected_source_paths = {
        name: config.paths[f"{name}_provenance"]
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
    if dict(reconciliation_config.source_paths) != expected_source_paths:
        raise DevelopmentReportError("Report and reconciliation source paths disagree.")
    if (
        reconciliation_config.diagnostic_figure_output_directory
        != config.paths["diagnostic_figure_output_directory"]
    ):
        raise DevelopmentReportError("Diagnostic figure directory drifted.")
    sources = authenticate_robustness_inputs(reconciliation_config)
    summary, _evidence, reconciliation_provenance = _authenticate_reconciliation(
        config, sources
    )
    if (
        summary.get("tract_date_row_count") != config.expected_rows
        or summary.get("independent_date_count") != config.expected_dates
        or summary.get("independent_spatial_block_count") != config.expected_blocks
        or _mapping(summary.get("robustness_readiness"), label="readiness").get(
            "development_evidence_ready_for_model_lock_review"
        )
        is not True
    ):
        raise DevelopmentReportError("Reconciled development readiness drifted.")
    text = _render_report(config, sources, summary)
    if "2025 remains untouched and locked" not in text or "human heat" not in text:
        raise DevelopmentReportError("Required interpretation boundaries are missing.")
    atomic_text(text, report_path)
    pipeline_sha, pipeline_fingerprint = code_runtime_fingerprint(
        project_root=_root(),
        relative_paths=(
            "configs/development_report.toml",
            "scripts/generate_development_report.py",
            "src/la_heat/development_report.py",
            "src/la_heat/provenance.py",
            "src/la_heat/robustness_reconciliation.py",
        ),
        algorithm_version=ALGORITHM_VERSION,
    )
    provenance: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "complete",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "analysis_scope": REPORT_SCOPE,
        "final_test_year": config.final_test_year,
        "final_test_locked": True,
        "final_test_used": False,
        "contains_final_test_year": False,
        "analysis_config": {
            "path": config.path.as_posix(),
            "sha256": sha256_file(config.path),
            "semantic_sha256": config.semantic_sha256,
        },
        "input_locks": {
            **config_locks,
            "reconciliation_provenance_sha256": sha256_file(
                config.paths["robustness_reconciliation_provenance"]
            ),
            "reconciliation_provenance_commit_sha256": reconciliation_provenance[
                "commit_sha256"
            ],
            "reconciliation_summary_commit_sha256": summary["commit_sha256"],
            "source_provenance_commits": {
                name: source.provenance_commit_sha256 for name, source in sources.items()
            },
        },
        "scientific_contract": {
            "historical_hindcast_not_operational_forecast": True,
            "lst_is_surface_heat_proxy_not_human_exposure": True,
            "predictive_association_not_causation": True,
            "independent_dates_and_blocks_reported": True,
            "strict_pixel_gate_failure_reported": True,
            "residual_spatial_clustering_reported": True,
            "final_test_unlocked": False,
        },
        "pipeline_sha256": pipeline_sha,
        "pipeline_fingerprint": pipeline_fingerprint,
        "output_files": {
            report_path.name: {
                "path": report_path.name,
                "path_base": "report_directory",
                "sha256": sha256_file(report_path),
                "bytes": report_path.stat().st_size,
            }
        },
    }
    provenance["commit_sha256"] = canonical_sha256(provenance)
    atomic_json(provenance, provenance_path)
    return provenance
