from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import la_heat.multicity.external_evaluation as external_evaluation
from la_heat.multicity.external_evaluation import (
    BOOTSTRAP_ITERATIONS,
    COMPLETION_FILENAME,
    ExternalEvaluationError,
    authenticate_external_evaluation_completion,
    authenticate_spatial_blocks,
    city_stratified_crossed_bootstrap,
    evaluate_external_frames,
    publish_external_evaluation,
)
from la_heat.multicity.external_target_authorization import PREDICTION_COLUMNS
from la_heat.multicity.portable_predictor_inventory import EXTERNAL_CITY_IDS
from la_heat.provenance import canonical_sha256

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _protocol() -> dict[str, object]:
    return {
        "evaluation_contract": {
            "primary_metric": (
                "one_minus_external_equal_city_equal_date_mae_m2_divided_by_b1"
            ),
            "minimum_relative_mae_improvement": 0.10,
            "require_ci_lower_above_zero": True,
            "require_no_external_city_point_degradation": True,
            "minimum_total_city_dates": 30,
            "minimum_dates_per_external_city": 8,
            "bootstrap_iterations": 10_000,
            "bootstrap_method": (
                "city_stratified_crossed_complete_date_x_5km_spatial_block"
            ),
            "bootstrap_seed": 20260728,
            "confidence_level": 0.95,
            "overall_coverage_lower": 0.85,
            "overall_coverage_upper": 0.95,
            "per_city_coverage_lower": 0.80,
            "minimum_retention": 0.60,
            "accepted_mae_improvement": 0.10,
            "hotspot_fraction": 0.20,
            "hotspot_tie_break": "score_desc_tract_geoid_asc",
            "secondary_metrics": [
                "per_city_equal_date_mae",
                "rmse",
                "signed_error",
                "anomaly_mae",
                "per_date_spearman",
                "top20_hotspot_metrics",
                "coverage",
                "interval_width",
                "wis",
                "risk_coverage",
            ],
        },
        "prediction_output_contract": {
            "prediction_columns": list(PREDICTION_COLUMNS),
            "planned_figure_ids": [
                "external_city_mae",
                "predicted_vs_observed",
                "error_by_city_date",
                "interval_calibration",
                "risk_coverage",
                "spatial_error_maps",
            ],
            "all_reports_require_row_date_block_counts": True,
        },
    }


def _frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    predictions: list[dict[str, object]] = []
    targets: list[dict[str, object]] = []
    blocks: list[dict[str, object]] = []
    for city_index, city_id in enumerate(EXTERNAL_CITY_IDS):
        for tract_index in range(4):
            tract = f"{city_index + 1:02d}{tract_index:09d}"
            blocks.append(
                {
                    "city_id": city_id,
                    "tract_geoid": tract,
                    "spatial_block": f"{city_id}-block-{tract_index // 2}",
                }
            )
        for date_index in range(10):
            target_date = f"2025-07-{date_index + 1:02d}"
            for tract_index in range(4):
                tract = f"{city_index + 1:02d}{tract_index:09d}"
                actual = 35.0 + city_index + date_index / 10 + tract_index / 4
                predictions.append(
                    {
                        "city_id": city_id,
                        "tract_geoid": tract,
                        "target_date": target_date,
                        "b1_prediction_c": actual + 2.0,
                        "m2_prediction_c": actual + 0.5,
                        "m2_lower_c": actual - 0.5,
                        "m2_upper_c": actual + 1.5,
                        "m2_interval_width_c": 2.0,
                        "m2_abstain": False,
                        "m2_accepted": True,
                    }
                )
                targets.append(
                    {
                        "city_id": city_id,
                        "tract_geoid": tract,
                        "target_date": target_date,
                        "target_lst_c": actual,
                        "target_available": True,
                        "date_usable": True,
                    }
                )
    return (
        pd.DataFrame(predictions).loc[:, PREDICTION_COLUMNS],
        pd.DataFrame(targets),
        pd.DataFrame(blocks),
    )


def test_frozen_evaluator_scores_indivisible_three_city_cohort() -> None:
    predictions, targets, blocks = _frames()

    result = evaluate_external_frames(predictions, targets, blocks, _protocol())

    assert result.summary["state"] == "complete"
    assert result.summary["city_ids"] == list(EXTERNAL_CITY_IDS)
    assert result.summary["usable_city_date_count"] == 30
    assert result.summary["point_prediction_gates"]["success"] is True
    assert result.summary["primary"]["relative_mae_improvement_fraction"] == pytest.approx(
        0.75
    )
    assert result.bootstrap["bootstrap_iterations"] == BOOTSTRAP_ITERATIONS
    assert result.bootstrap["random_rows_sampled"] is False
    assert result.bootstrap["cities_equal_weight"] is True
    assert set(result.bootstrap["city_units"]) == set(EXTERNAL_CITY_IDS)
    assert len(result.city_metrics) == 3
    assert len(result.date_metrics) == 30
    assert result.date_metrics["date_count"].eq(1).all()
    assert {
        "row_count",
        "date_count",
        "spatial_block_count",
    }.issubset(result.city_metrics)
    assert {
        "retained_rows",
        "retained_city_date_count",
        "retained_spatial_block_count",
    }.issubset(result.risk_coverage)
    assert result.risk_coverage[
        [
            "retained_rows",
            "retained_city_date_count",
            "retained_spatial_block_count",
        ]
    ].ge(1).all().all()
    assert {
        "usable_row_count",
        "usable_city_date_count",
        "spatial_block_count",
    }.issubset(result.summary)
    assert all(
        {"row_count", "date_count", "spatial_block_count"}.issubset(unit)
        for unit in result.bootstrap["city_units"].values()
    )


def test_evaluator_rejects_split_cohort_and_changed_contract() -> None:
    predictions, targets, blocks = _frames()
    split = predictions.loc[predictions["city_id"].ne(EXTERNAL_CITY_IDS[-1])]
    split_targets = targets.loc[targets["city_id"].ne(EXTERNAL_CITY_IDS[-1])]
    with pytest.raises(ExternalEvaluationError, match="cohort"):
        evaluate_external_frames(split, split_targets, blocks, _protocol())

    changed = deepcopy(_protocol())
    changed["evaluation_contract"]["bootstrap_iterations"] = 999  # type: ignore[index]
    with pytest.raises(ExternalEvaluationError, match="contract"):
        evaluate_external_frames(predictions, targets, blocks, changed)


def test_bootstrap_parameters_and_full_key_universe_are_frozen() -> None:
    predictions, targets, blocks = _frames()
    result = evaluate_external_frames(predictions, targets, blocks, _protocol())

    with pytest.raises(ExternalEvaluationError, match="cannot be changed"):
        city_stratified_crossed_bootstrap(result.scored_rows, iterations=999)
    with pytest.raises(ExternalEvaluationError, match="key universes"):
        evaluate_external_frames(predictions, targets.iloc[:-1], blocks, _protocol())


def test_spatial_blocks_authenticate_against_real_protocol_lock() -> None:
    protocol = json.loads(
        (PROJECT_ROOT / "manifests/multicity/evaluation/PROTOCOL_MODEL_LOCK.json").read_text(
            encoding="utf-8"
        )
    )

    manifest, blocks = authenticate_spatial_blocks(PROJECT_ROOT, protocol)

    assert manifest["commit_sha256"] == protocol["input_fingerprints"][
        "spatial_blocks"
    ]["commit_sha256"]
    assert manifest["output"]["rows"] == len(blocks)
    assert manifest["output"]["sha256"]


def test_completion_check_authenticates_outputs_and_exact_input_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    predictions, targets, blocks = _frames()
    result = evaluate_external_frames(predictions, targets, blocks, _protocol())
    output = tmp_path / "external-evaluation"
    bindings = {
        "protocol_lock_commit_sha256": "a" * 64,
        "authorization_commit_sha256": "b" * 64,
        "external_prediction_commit_sha256": "c" * 64,
        "external_target_completion_commit_sha256": "d" * 64,
        "values_opened_commit_sha256": "e" * 64,
        "spatial_blocks_manifest_commit_sha256": "f" * 64,
        "spatial_blocks_manifest_sha256": "1" * 64,
        "spatial_blocks_sha256": "2" * 64,
        "spatial_blocks_semantic_sha256": "3" * 64,
    }
    published = publish_external_evaluation(
        PROJECT_ROOT,
        result,
        input_bindings=bindings,
        output_directory=output,
    )
    monkeypatch.setattr(
        external_evaluation,
        "_authenticate_evaluation_inputs",
        lambda *_args, **_kwargs: SimpleNamespace(bindings=bindings),
    )

    observed = authenticate_external_evaluation_completion(
        PROJECT_ROOT, output_directory=output
    )

    assert observed == published
    completion_path = output / COMPLETION_FILENAME
    changed = json.loads(completion_path.read_text(encoding="utf-8"))
    changed["input_bindings"]["spatial_blocks_sha256"] = "9" * 64
    changed.pop("commit_sha256")
    changed["commit_sha256"] = canonical_sha256(changed)
    completion_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ExternalEvaluationError, match="contract or inputs"):
        authenticate_external_evaluation_completion(
            PROJECT_ROOT, output_directory=output
        )


@pytest.mark.parametrize(
    ("key", "changed_value"),
    [
        ("external_models_refit_or_recalibrated", True),
        ("prediction_commit_preceded_target_access", False),
        ("three_city_cohort_evaluated_as_one_claim", False),
    ],
)
def test_completion_check_rejects_changed_blind_boundary_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    changed_value: bool,
) -> None:
    predictions, targets, blocks = _frames()
    result = evaluate_external_frames(predictions, targets, blocks, _protocol())
    output = tmp_path / "external-evaluation"
    bindings = {"frozen_inputs": "a" * 64}
    publish_external_evaluation(
        PROJECT_ROOT,
        result,
        input_bindings=bindings,
        output_directory=output,
    )
    monkeypatch.setattr(
        external_evaluation,
        "_authenticate_evaluation_inputs",
        lambda *_args, **_kwargs: SimpleNamespace(bindings=bindings),
    )
    summary_path = output / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary[key] = changed_value
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    completion_path = output / COMPLETION_FILENAME
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    completion["output_files"]["summary.json"] = {
        "bytes": summary_path.stat().st_size,
        "sha256": external_evaluation.sha256_file(summary_path),
    }
    completion["summary_commit_sha256"] = canonical_sha256(summary)
    completion.pop("commit_sha256")
    completion["commit_sha256"] = canonical_sha256(completion)
    completion_path.write_text(json.dumps(completion), encoding="utf-8")

    with pytest.raises(ExternalEvaluationError, match="metrics"):
        authenticate_external_evaluation_completion(
            PROJECT_ROOT, output_directory=output
        )
