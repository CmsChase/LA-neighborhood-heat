from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from la_heat.provenance import canonical_sha256, sha256_file
from la_heat.robustness_reconciliation import (
    _REQUIRED_OUTPUTS,
    _SOURCE_ALGORITHMS,
    EVIDENCE_FILENAME,
    PROVENANCE_FILENAME,
    SUMMARY_FILENAME,
    RobustnessReconciliationError,
    _validate_csv_frame,
    reconcile_development_robustness,
)

COMPILE_COMMIT = "a" * 64
OOF_SHA = "b" * 64


def test_csv_byte_lock_schema_accepts_datetime_reinference() -> None:
    frame = pd.DataFrame({"target_date": ["2024-06-01"], "usable": [True]})
    record = {"rows": 1, "schema_sha256": "c" * 64}

    _validate_csv_frame(frame, record, label="synthetic")


def _committed(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(result)
    return result


def _common_summary(name: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": _SOURCE_ALGORITHMS[name],
        "final_test_year": 2025,
        "final_test_locked": True,
        "contains_final_test_year": False,
    }
    if name != "stqa2_sensitivity":
        result["state"] = "complete"
    return result


def _bootstrap(seed: int, *, rows: int = 63_403, dates: int = 65) -> dict[str, Any]:
    return {
        "bootstrap_seed": seed,
        "bootstrap_replicates": 5_000,
        "random_row_sampling_used": False,
        "relative_mae_improvement_percent": 16.0,
        "relative_mae_improvement_ci_lower_percent": 4.0,
        "relative_mae_improvement_ci_upper_percent": 27.0,
        "relative_mae_improvement_fraction": 0.16,
        "relative_mae_improvement_ci_lower_fraction": 0.04,
        "relative_mae_improvement_ci_upper_fraction": 0.27,
        "tract_date_row_count": rows,
        "independent_date_count": dates,
        "independent_spatial_block_count": 71,
    }


def _summary(name: str, *, inject_2025: bool = False) -> dict[str, Any]:
    summary = _common_summary(name)
    if name in {"initial_results", "qa"}:
        summary.update(
            {
                "tract_date_row_count": 63_403,
                "independent_date_count": 65,
                "independent_spatial_block_count": 71,
            }
        )
    if name == "initial_results":
        summary["primary_comparison"] = {
            "family": "joint",
            "strongest_legal_baseline_model_id": "B1",
            "target_model_id": "M2",
            **_bootstrap(20_260_722),
        }
    elif name == "endpoint":
        summary["relative_endpoint"] = {
            "gated_independent_date_count": 34,
            "focus_joint_models": {
                model: {
                    "tract_date_row_count": 36_139,
                    "independent_date_count": 34,
                    "independent_spatial_block_count": 71,
                    "mean_per_date_average_precision": value,
                }
                for model, value in (("B1", 0.40), ("M2", 0.67))
            },
        }
        summary["sensor_diagnostics"] = [
            {
                "model_id": model,
                "platform": platform,
                "equal_date_weighted_mae_c": mae,
                "tract_date_row_count": rows,
                "independent_date_count": dates,
                "independent_spatial_block_count": 71,
            }
            for model, platform, mae, rows, dates in (
                ("B1", "landsat-8", 2.40, 40_578, 41),
                ("M2", "landsat-8", 2.05, 40_578, 41),
                ("B1", "landsat-9", 2.70, 22_825, 24),
                ("M2", "landsat-9", 2.20, 22_825, 24),
            )
        ]
    elif name == "qa":
        summary["selected_cohorts"] = {
            "all_rows": {
                "baseline_model_id": "B1",
                "target_model_id": "M2",
                "tract_date_row_count": 63_403,
                "independent_date_count": 65,
                "independent_spatial_block_count": 71,
                "crossed_bootstrap": _bootstrap(20_260_723),
            },
            "tract_median_st_qa_le_2k": {
                "tract_date_row_count": 19_548,
                "independent_date_count": 53,
                "independent_spatial_block_count": 71,
                "crossed_bootstrap": _bootstrap(20_260_724, rows=19_548, dates=53),
            },
            "sentinel_all_five_missing": {
                "tract_date_row_count": 168,
                "independent_date_count": 12,
                "independent_spatial_block_count": 29,
                "crossed_bootstrap": {
                    **_bootstrap(20_260_725, rows=168, dates=12),
                    "relative_mae_improvement_ci_lower_percent": -59.0,
                    "relative_mae_improvement_ci_upper_percent": 53.0,
                },
            },
        }
        summary["qa_interpretation"] = {
            "st_qa_2k_cohort_is_tract_summary_filter": True,
            "pixel_level_st_qa_hard_mask_reaggregated": False,
            "tract_summary_filter_replaces_pixel_level_sensitivity": False,
        }
    elif name == "diagnostic_figures":
        summary["figure_files"] = {
            filename: {"sha256": "c" * 64}
            for filename in _REQUIRED_OUTPUTS[name]
            if filename.endswith(".png")
        }
    elif name == "feature_ablation":
        summary.update(
            {
                "tract_date_row_count_per_family_scenario": 63_403,
                "independent_date_count": 65,
                "independent_spatial_block_count": 71,
                "joint_comparisons": [
                    {
                        "reduced_scenario_id": scenario,
                        "relative_mae_improvement_fraction": 0.05,
                        "relative_mae_improvement_ci_lower_fraction": -0.01,
                        "relative_mae_improvement_ci_upper_fraction": 0.12,
                    }
                    for scenario in (
                        "calendar_weather",
                        "calendar_land_use_geography",
                        "calendar_satellite",
                    )
                ],
                "interpretation": {
                    "causal_feature_importance": False,
                    "leave_one_feature_family_out": False,
                    "feature_importance_claim_allowed": False,
                },
            }
        )
    elif name == "stqa2_sensitivity":
        summary.update(
            {
                "strict_pixel_rule": "ST_QA <= 2.0 K before tract aggregation",
                "fixed_support_invariant_pass": True,
                "strict_target_stage_state": "model_ready",
                "strict_usable_date_count": 31,
                "minimum_required_usable_date_count": 30,
                "strict_minimum_date_gate_pass": True,
                "strict_analysis_label_row_count": 20_000,
                "frozen_primary_oof_sensitivity_estimable": True,
                "frozen_primary_oof_bootstrap_not_estimable_reason": None,
                "frozen_primary_oof_refit_performed": False,
                "frozen_primary_oof_bootstrap": _bootstrap(
                    20_260_725, rows=20_000, dates=31
                ),
            }
        )
    if inject_2025:
        summary["target_date"] = "2025-07-01"
    if name in {"initial_results", "qa", "diagnostic_figures", "feature_ablation"}:
        summary = _committed(summary)
    return summary


def _generic_frame(filename: str) -> pd.DataFrame:
    if filename == "joint_m2_b1_morans_i_summary.csv":
        return pd.DataFrame(
            [
                {
                    "model_id": model,
                    "mean_morans_i_across_dates": mean,
                    "median_morans_i_across_dates": median,
                    "date_level_observation_count": 65,
                    "positive_morans_i_date_count": 65,
                    "multiple_testing_adjustment": "none_descriptive_diagnostic",
                }
                for model, mean, median in (("B1", 0.64, 0.67), ("M2", 0.57, 0.58))
            ]
        )
    return pd.DataFrame({"target_date": ["2024-07-01"], "value": [1.0]})


def _output_record(path: Path, *, rows: int | None = None, path_base: str) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": path.name,
        "path_base": path_base,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }
    if rows is not None:
        record["rows"] = rows
    return record


def _input_auth(name: str) -> dict[str, Any]:
    if name == "feature_ablation":
        return {
            "canonical_model_compile_commit_sha256": COMPILE_COMMIT,
            "canonical_all_feature_oof_sha256": OOF_SHA,
        }
    if name == "stqa2_sensitivity":
        return {
            "model_compile_provenance_commit_sha256": COMPILE_COMMIT,
            "model_oof_predictions_sha256": OOF_SHA,
        }
    return {
        "compile_provenance_commit_sha256": COMPILE_COMMIT,
        "oof_predictions_sha256": OOF_SHA,
    }


def _write_source(
    root: Path,
    name: str,
    *,
    figure_directory: Path,
    figure_upstream: dict[str, Any] | None = None,
    inject_2025: bool = False,
) -> tuple[Path, dict[str, Any]]:
    directory = root / name
    directory.mkdir(parents=True)
    summary_filename = {
        "initial_results": "model_results_initial_summary.json",
        "endpoint": "model_endpoint_diagnostics_summary.json",
        "qa": "model_qa_diagnostics_summary.json",
        "diagnostic_figures": "model_diagnostic_figures_summary.json",
        "feature_ablation": "feature_ablation_analysis_summary.json",
        "stqa2_sensitivity": "stqa2_sensitivity_summary.json",
    }.get(name)
    outputs: dict[str, dict[str, Any]] = {}
    summary_payload = _summary(name, inject_2025=inject_2025) if summary_filename else None
    for filename in _REQUIRED_OUTPUTS[name]:
        destination = (
            figure_directory / filename
            if name == "diagnostic_figures" and filename.endswith(".png")
            else directory / filename
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if filename == summary_filename:
            assert summary_payload is not None
            destination.write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")
            outputs[filename] = _output_record(
                destination, path_base="table_output_directory"
            )
        elif filename.endswith(".csv"):
            frame = _generic_frame(filename)
            frame.to_csv(destination, index=False)
            outputs[filename] = _output_record(
                destination, rows=len(frame), path_base="output_directory"
            )
        else:
            destination.write_bytes(f"synthetic-{name}-{filename}".encode())
            outputs[filename] = _output_record(
                destination,
                path_base=(
                    "figure_output_directory"
                    if name == "diagnostic_figures"
                    else "output_directory"
                ),
            )
    manifest: dict[str, Any]
    if name == "residual_spatial":
        manifest = {
            "tables": {key: value for key, value in outputs.items() if key.endswith(".csv")},
            "figures": {key: value for key, value in outputs.items() if key.endswith(".png")},
        }
    else:
        manifest = outputs
    authentication = _input_auth(name)
    if name == "diagnostic_figures":
        authentication["upstream_provenance"] = figure_upstream
    provenance: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": _SOURCE_ALGORITHMS[name],
        "state": "complete",
        "analysis_scope": "locked_2020_2024_development_only",
        "final_test_year": 2025,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "input_authentication": authentication,
        "output_files": manifest,
    }
    if name in {"initial_results", "endpoint"}:
        provenance["compile_provenance_commit_sha256"] = COMPILE_COMMIT
    if name in {"qa", "diagnostic_figures", "feature_ablation", "stqa2_sensitivity"}:
        assert summary_payload is not None
        provenance["summary_commit_sha256"] = (
            summary_payload.get("commit_sha256") or canonical_sha256(summary_payload)
        )
    provenance = _committed(provenance)
    provenance_path = directory / f"{name}_provenance.json"
    provenance_path.write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    return provenance_path, provenance


def _write_config(
    root: Path,
    source_paths: dict[str, Path],
    figure_directory: Path,
) -> Path:
    output = root / "reconciliation"
    config = root / "robustness.toml"
    config.write_text(
        "\n".join(
            [
                "schema_version = 1",
                'algorithm_version = "development-robustness-reconciliation-v1"',
                'state = "frozen_development_reconciliation"',
                "",
                "[paths]",
                *[
                    f'{name}_provenance = "{source_paths[name].as_posix()}"'
                    for name in (
                        "initial_results",
                        "endpoint",
                        "qa",
                        "residual_spatial",
                        "diagnostic_figures",
                        "feature_ablation",
                        "stqa2_sensitivity",
                    )
                ],
                f'diagnostic_figure_output_directory = "{figure_directory.as_posix()}"',
                f'output_directory = "{output.as_posix()}"',
                "",
                "[analysis]",
                "final_test_year = 2025",
                "final_test_locked = true",
                'family = "joint"',
                'baseline_model_id = "B1"',
                'target_model_id = "M2"',
                "expected_tract_date_row_count = 63403",
                "expected_independent_date_count = 65",
                "expected_independent_spatial_block_count = 71",
                "expected_relative_endpoint_date_count = 34",
                "expected_diagnostic_figure_count = 4",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config


def _fixture_tree(tmp_path: Path, *, inject_2025: bool = False) -> tuple[Path, dict[str, Path]]:
    figures = tmp_path / "figures"
    source_paths: dict[str, Path] = {}
    source_payloads: dict[str, dict[str, Any]] = {}
    for name in ("initial_results", "endpoint", "qa", "residual_spatial"):
        path, payload = _write_source(
            tmp_path,
            name,
            figure_directory=figures,
            inject_2025=inject_2025 and name == "initial_results",
        )
        source_paths[name] = path
        source_payloads[name] = payload
    upstream = {
        recorded: {
            "file_sha256": sha256_file(source_paths[source]),
            "commit_sha256": source_payloads[source]["commit_sha256"],
        }
        for recorded, source in {
            "initial": "initial_results",
            "endpoint": "endpoint",
            "qa": "qa",
            "residual_spatial": "residual_spatial",
        }.items()
    }
    source_paths["diagnostic_figures"], _ = _write_source(
        tmp_path,
        "diagnostic_figures",
        figure_directory=figures,
        figure_upstream=upstream,
    )
    for name in ("feature_ablation", "stqa2_sensitivity"):
        source_paths[name], _ = _write_source(
            tmp_path,
            name,
            figure_directory=figures,
        )
    return _write_config(tmp_path, source_paths, figures), source_paths


def test_complete_authenticated_reconciliation_keeps_required_distinctions(
    tmp_path: Path,
) -> None:
    config, _ = _fixture_tree(tmp_path)

    provenance = reconcile_development_robustness(config)

    output = tmp_path / "reconciliation"
    summary = json.loads((output / SUMMARY_FILENAME).read_text(encoding="utf-8"))
    evidence = pd.read_csv(output / EVIDENCE_FILENAME)
    assert provenance["state"] == "complete"
    assert provenance["scientific_contract"]["final_test_unlocked"] is False
    assert summary["interpretive_contract"] == {
        **summary["interpretive_contract"],
        "primary_ci_and_qa_rerun_are_distinct": True,
        "qa_tract_summary_stqa_is_not_pixel_hard_mask": True,
        "feature_ablation_supports_predictive_association_not_causation": True,
        "sparse_groups_are_exploratory": True,
        "residual_spatial_clustering_remains_a_limitation": True,
    }
    primary = evidence.set_index("evidence_id").loc[
        "primary_joint_relative_mae_improvement"
    ]
    qa_rerun = evidence.set_index("evidence_id").loc[
        "qa_all_rows_relative_mae_improvement_rerun"
    ]
    assert primary["interpretation"].startswith("primary_predeclared")
    assert "not_the_primary" in qa_rerun["interpretation"]
    assert len(evidence) == 15


def test_missing_upstream_fails_closed_and_withdraws_old_marker(tmp_path: Path) -> None:
    config, source_paths = _fixture_tree(tmp_path)
    marker = tmp_path / "reconciliation" / PROVENANCE_FILENAME
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"state": "complete"}), encoding="utf-8")
    source_paths["feature_ablation"].unlink()

    with pytest.raises(RobustnessReconciliationError, match="missing"):
        reconcile_development_robustness(config)

    assert not marker.exists()


def test_tampered_upstream_output_fails_byte_lock(tmp_path: Path) -> None:
    config, source_paths = _fixture_tree(tmp_path)
    endpoint_directory = source_paths["endpoint"].parent
    with (endpoint_directory / "hotspot_summary.csv").open("a", encoding="utf-8") as handle:
        handle.write("tampered\n")

    with pytest.raises(RobustnessReconciliationError, match="byte lock"):
        reconcile_development_robustness(config)

    assert not (tmp_path / "reconciliation" / PROVENANCE_FILENAME).exists()


def test_authenticated_2025_content_is_rejected(tmp_path: Path) -> None:
    config, _ = _fixture_tree(tmp_path, inject_2025=True)

    with pytest.raises(RobustnessReconciliationError, match="2025 target date"):
        reconcile_development_robustness(config)


def test_unlocked_upstream_is_rejected_even_with_valid_commit(tmp_path: Path) -> None:
    config, source_paths = _fixture_tree(tmp_path)
    path = source_paths["stqa2_sensitivity"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("commit_sha256")
    payload["final_test_unlocked"] = True
    payload = _committed(payload)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    with pytest.raises(RobustnessReconciliationError, match="unlock"):
        reconcile_development_robustness(config)
