from __future__ import annotations

import inspect
import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from la_heat.model_selection import (
    EXPECTED_CANDIDATE_COUNTS,
    FROZEN_CONFIG_SEMANTIC_SHA256,
    MODEL_IDS,
    MODEL_SELECTION_FREEZE_FILENAME,
    ModelSelectionAuditError,
    build_model_selection_freeze_manifest,
    load_model_selection_config,
    select_candidate,
)
from la_heat.modeling import make_model_spec
from la_heat.provenance import canonical_sha256

CONFIG_PATH = Path(__file__).parents[1] / "configs" / "model_selection.toml"


def _config():
    return load_model_selection_config(CONFIG_PATH)


def _date_scores(
    config,
    model_id: str,
    dates: list[str],
    overrides: dict[str, list[float]],
    *,
    default: float = 9.0,
) -> pd.DataFrame:
    rows = []
    for candidate in config.candidates_for(model_id):
        values = overrides.get(candidate.candidate_id, [default] * len(dates))
        assert len(values) == len(dates)
        rows.extend(
            {
                "candidate_id": candidate.candidate_id,
                "target_date": date,
                "date_mae_c": value,
            }
            for date, value in zip(dates, values, strict=True)
        )
    return pd.DataFrame(rows, columns=["candidate_id", "target_date", "date_mae_c"])


def test_frozen_config_has_exact_models_candidate_counts_and_factory_keywords() -> None:
    config = _config()

    assert config.semantic_sha256 == FROZEN_CONFIG_SEMANTIC_SHA256
    assert config.development_years == tuple(range(2020, 2025))
    assert config.final_test_year == 2025
    assert config.unlock_final_test is False
    assert {model_id: len(config.candidates_for(model_id)) for model_id in MODEL_IDS} == (
        EXPECTED_CANDIDATE_COUNTS
    )
    factory_parameters = set(inspect.signature(make_model_spec).parameters)
    for candidate in config.candidates:
        assert set(candidate.factory_parameters()).issubset(factory_parameters)
        kwargs = config.factory_kwargs(candidate.model_id, candidate.candidate_id)
        assert kwargs["random_state"] == 20260719
        assert set(kwargs).issubset(factory_parameters)
    assert config.candidates_for("B0")[0].factory_parameters() == {}


def test_freeze_manifest_is_committed_without_targets_scores_or_fit(tmp_path: Path) -> None:
    payload = build_model_selection_freeze_manifest(CONFIG_PATH, tmp_path)

    assert payload["state"] == "frozen_pre_score"
    assert payload["frozen_before_scores"] is True
    assert payload["target_tables_read"] == []
    assert payload["score_tables_read"] == []
    assert payload["models_fitted"] is False
    assert payload["final_test_unlocked"] is False
    assert payload["candidate_counts"] == EXPECTED_CANDIDATE_COUNTS
    committed = json.loads(
        (tmp_path / MODEL_SELECTION_FREEZE_FILENAME).read_text(encoding="utf-8")
    )
    commit_sha256 = committed.pop("commit_sha256")
    assert canonical_sha256(committed) == commit_sha256 == payload["commit_sha256"]


def test_valid_but_unfrozen_grid_edit_fails_semantic_hash(tmp_path: Path) -> None:
    changed = tmp_path / "model_selection.toml"
    text = CONFIG_PATH.read_text(encoding="utf-8")
    changed.write_text(
        text.replace("ridge_alpha = 100.0", "ridge_alpha = 101.0", 1),
        encoding="utf-8",
    )

    with pytest.raises(ModelSelectionAuditError, match="semantic SHA-256 changed"):
        load_model_selection_config(changed)


def test_config_cannot_unlock_2025_or_change_inner_cv(tmp_path: Path) -> None:
    text = CONFIG_PATH.read_text(encoding="utf-8")
    unlocked = tmp_path / "unlocked.toml"
    unlocked.write_text(
        text.replace("unlock_final_test = false", "unlock_final_test = true"),
        encoding="utf-8",
    )
    with pytest.raises(PermissionError, match="2025 must remain locked"):
        load_model_selection_config(unlocked)

    random_inner = tmp_path / "random_inner.toml"
    random_inner.write_text(
        text.replace(
            'strategy = "leave_one_remaining_calendar_year_out"',
            'strategy = "random_row_split"',
        ),
        encoding="utf-8",
    )
    with pytest.raises(ModelSelectionAuditError, match="whole-year"):
        load_model_selection_config(random_inner)

    with pytest.raises(PermissionError, match="2025 lock"):
        select_candidate(
            replace(_config(), unlock_final_test=True),
            "B0",
            _date_scores(_config(), "B0", ["2024-07-01"], {}),
            expected_validation_dates=("2024-07-01",),
        )


def test_selection_uses_stitched_equal_date_mae_not_equal_year_or_row_weighting() -> None:
    config = _config()
    candidates = config.candidates_for("B1")
    simplest = candidates[0].candidate_id
    second = candidates[1].candidate_id
    scores = _date_scores(
        config,
        "B1",
        ["2020-06-01", "2020-08-01", "2021-07-01"],
        {
            simplest: [0.0, 0.0, 4.0],
            second: [1.5, 1.5, 1.5],
        },
    )

    result = select_candidate(
        config,
        "B1",
        scores,
        expected_validation_dates=(
            "2020-06-01",
            "2020-08-01",
            "2021-07-01",
        ),
    )

    assert result.selected_candidate.candidate_id == simplest
    selected_score = next(
        score for score in result.ranking if score.candidate_id == simplest
    )
    assert selected_score.mean_date_mae_c == pytest.approx(4.0 / 3.0)
    assert result.independent_validation_date_count == 3
    assert result.validation_years == (2020, 2021)


def test_tolerance_tie_prefers_frozen_lower_complexity_rank() -> None:
    config = _config()
    candidates = config.candidates_for("B1")
    simplest = candidates[0].candidate_id
    second = candidates[1].candidate_id
    scores = _date_scores(
        config,
        "B1",
        ["2022-06-01", "2023-06-01"],
        {
            simplest: [1.0 + 5.0e-13, 1.0 + 5.0e-13],
            second: [1.0, 1.0],
        },
    )

    result = select_candidate(
        config,
        "B1",
        scores,
        expected_validation_dates=("2022-06-01", "2023-06-01"),
    )

    assert result.selected_candidate.candidate_id == simplest
    assert result.tied_candidate_ids == (simplest, second)
    assert result.ranking[0].candidate_id == second


def test_score_contract_rejects_missing_date_duplicate_and_locked_year() -> None:
    config = _config()
    candidates = config.candidates_for("B2")
    scores = _date_scores(config, "B2", ["2022-06-01", "2023-06-01"], {})

    missing_date = scores.drop(
        scores.loc[
            scores["candidate_id"].eq(candidates[0].candidate_id)
            & scores["target_date"].eq("2023-06-01")
        ].index
    )
    with pytest.raises(ModelSelectionAuditError, match="caller-declared"):
        select_candidate(
            config,
            "B2",
            missing_date,
            expected_validation_dates=("2022-06-01", "2023-06-01"),
        )

    duplicated = pd.concat([scores, scores.iloc[[0]]], ignore_index=True)
    with pytest.raises(ModelSelectionAuditError, match="Duplicate candidate-date"):
        select_candidate(
            config,
            "B2",
            duplicated,
            expected_validation_dates=("2022-06-01", "2023-06-01"),
        )

    locked = scores.assign(target_date="2025-06-01")
    with pytest.raises(PermissionError, match="Locked 2025"):
        select_candidate(
            config,
            "B2",
            locked,
            expected_validation_dates=("2022-06-01", "2023-06-01"),
        )


@pytest.mark.parametrize("bad_score", [-0.01, float("nan"), float("inf"), True])
def test_score_contract_rejects_invalid_per_date_mae(bad_score: object) -> None:
    config = _config()
    scores = _date_scores(config, "B0", ["2024-07-01"], {})
    if isinstance(bad_score, bool):
        scores["date_mae_c"] = scores["date_mae_c"].astype(object)
    scores.loc[0, "date_mae_c"] = bad_score

    with pytest.raises(ModelSelectionAuditError, match="date_mae_c"):
        select_candidate(
            config,
            "B0",
            scores,
            expected_validation_dates=("2024-07-01",),
        )


def test_score_contract_rejects_date_omitted_by_every_candidate() -> None:
    config = _config()
    scores = _date_scores(
        config,
        "B1",
        ["2022-06-01", "2023-06-01"],
        {},
    )

    with pytest.raises(ModelSelectionAuditError, match="caller-declared"):
        select_candidate(
            config,
            "B1",
            scores,
            expected_validation_dates=(
                "2022-06-01",
                "2023-06-01",
                "2023-07-01",
            ),
        )


@pytest.mark.parametrize(
    "expected_dates, error_type, message",
    [
        ((), ModelSelectionAuditError, "non-empty"),
        (
            ("2023-06-01", "2023-06-01"),
            ModelSelectionAuditError,
            "unique",
        ),
        (("2025-06-01",), PermissionError, "locked 2025"),
    ],
)
def test_expected_validation_dates_fail_closed(
    expected_dates: tuple[str, ...],
    error_type: type[Exception],
    message: str,
) -> None:
    config = _config()
    scores = _date_scores(config, "B0", ["2023-06-01"], {})

    with pytest.raises(error_type, match=message):
        select_candidate(
            config,
            "B0",
            scores,
            expected_validation_dates=expected_dates,
        )
