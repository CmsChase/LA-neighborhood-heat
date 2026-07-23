from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

import la_heat.residual_spatial_diagnostics as diagnostics
from la_heat.provenance import canonical_sha256, sha256_file

ROOT = Path(__file__).resolve().parents[1]


def _small_config(**changes: object) -> diagnostics.ResidualSpatialConfig:
    config = diagnostics.load_residual_spatial_config(
        ROOT / "configs/residual_spatial_diagnostics.toml"
    )
    defaults: dict[str, object] = {
        "development_years": (2024,),
        "expected_tract_date_rows_per_model": 1,
        "expected_independent_dates": 1,
        "expected_spatial_blocks": 1,
        "expected_tracts": 1,
        "expected_date_block_cells_per_model": 1,
        "permutations": 19,
    }
    defaults.update(changes)
    return replace(config, **defaults)


def _target_frame(date: str = "2024-07-01") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "tract_geoid": ["06037000001"],
            "target_date": [date],
            "spatial_block": ["x+0000_y+0000"],
            "target_lst_c": [40.0],
            "target_available": [True],
            "date_usable": [True],
            "tract_manifest_sha256": ["a" * 64],
        }
    )


def test_rook_adjacency_uses_only_shared_grid_edges() -> None:
    blocks = [
        "x+0000_y+0000",
        "x+0001_y+0000",
        "x+0000_y+0001",
        "x+0001_y+0001",
    ]

    edges = diagnostics.rook_adjacency_edges(blocks)

    assert set(edges) == {
        ("x+0000_y+0000", "x+0001_y+0000"),
        ("x+0000_y+0000", "x+0000_y+0001"),
        ("x+0000_y+0001", "x+0001_y+0001"),
        ("x+0001_y+0000", "x+0001_y+0001"),
    }
    assert (
        "x+0000_y+0000",
        "x+0001_y+0001",
    ) not in edges
    with pytest.raises(
        diagnostics.ResidualSpatialDiagnosticError,
        match="Invalid fixed-grid",
    ):
        diagnostics.rook_adjacency_edges(["block-a", "x+0000_y+0000"])


def test_morans_i_matches_known_line_and_permutation_is_deterministic() -> None:
    values = pd.Series(
        [1.0, 2.0, 3.0, 4.0],
        index=[
            "x+0000_y+0000",
            "x+0001_y+0000",
            "x+0002_y+0000",
            "x+0003_y+0000",
        ],
    )

    first = diagnostics.morans_i_rook(values, permutations=99, seed=1234)
    second = diagnostics.morans_i_rook(values, permutations=99, seed=1234)

    assert first.observation_count == 4
    assert first.rook_edge_count == 3
    assert first.morans_i == pytest.approx(1.0 / 3.0)
    assert first.randomization_expectation == pytest.approx(-1.0 / 3.0)
    assert first.permutation_p_value_two_sided == (
        second.permutation_p_value_two_sided
    )
    assert 0.0 < first.permutation_p_value_two_sided <= 1.0


def test_residuals_are_aggregated_to_date_block_before_moran() -> None:
    predictions = pd.DataFrame(
        {
            "family": ["joint"] * 4,
            "model_id": ["M2"] * 4,
            "tract_geoid": ["a", "b", "c", "d"],
            "target_date": pd.to_datetime(["2024-07-01"] * 4),
            "spatial_block": [
                "x+0000_y+0000",
                "x+0000_y+0000",
                "x+0001_y+0000",
                "x+0001_y+0000",
            ],
            "y_true": [10.0, 20.0, 30.0, 40.0],
            "y_pred": [11.0, 23.0, 29.0, 35.0],
            "residual_c": [1.0, 3.0, -1.0, -5.0],
            "absolute_error_c": [1.0, 3.0, 1.0, 5.0],
        }
    )

    cells = diagnostics.aggregate_date_block_residuals(predictions)

    assert len(cells) == 2
    observed = cells.set_index("spatial_block")
    assert observed.loc["x+0000_y+0000", "tract_date_row_count"] == 2
    assert observed.loc["x+0000_y+0000", "mean_residual_c"] == pytest.approx(2.0)
    assert observed.loc["x+0001_y+0000", "mean_residual_c"] == pytest.approx(-3.0)


def test_committed_input_and_frozen_config_locks_reject_tampering(
    tmp_path: Path,
) -> None:
    payload = {"schema_version": 1, "state": "complete"}
    payload["commit_sha256"] = canonical_sha256(payload)
    committed = tmp_path / "committed.json"
    committed.write_text(json.dumps(payload), encoding="utf-8")
    file_sha = sha256_file(committed)

    observed = diagnostics._read_committed_json(
        committed,
        expected_file_sha256=file_sha,
        expected_commit_sha256=payload["commit_sha256"],
        label="test input",
    )

    assert observed == payload
    committed.write_text(json.dumps({**payload, "state": "tampered"}), encoding="utf-8")
    with pytest.raises(
        diagnostics.ResidualSpatialDiagnosticError,
        match="file lock failed",
    ):
        diagnostics._read_committed_json(
            committed,
            expected_file_sha256=file_sha,
            expected_commit_sha256=payload["commit_sha256"],
            label="test input",
        )

    source = (ROOT / "configs/residual_spatial_diagnostics.toml").read_text(
        encoding="utf-8"
    )
    changed = source.replace(
        diagnostics._FROZEN_LOCKS["oof_predictions_sha256"], "0" * 64
    )
    changed_config = tmp_path / "changed.toml"
    changed_config.write_text(changed, encoding="utf-8")
    with pytest.raises(
        diagnostics.ResidualSpatialDiagnosticError,
        match="Frozen input hashes drifted",
    ):
        diagnostics.load_residual_spatial_config(changed_config)


def test_target_manifest_and_selected_oof_reject_2025_or_truth_drift() -> None:
    config = _small_config()
    with pytest.raises(PermissionError, match="Locked final-test year 2025"):
        diagnostics._validate_target_frame(_target_frame("2025-07-01"), config)

    targets = diagnostics._validate_target_frame(_target_frame(), config)
    predictions = pd.DataFrame(
        {
            "family": ["joint", "joint"],
            "model_id": ["M2", "B1"],
            "tract_geoid": ["06037000001", "06037000001"],
            "target_date": pd.to_datetime(["2024-07-01", "2024-07-01"]),
            "spatial_block": ["x+0000_y+0000", "x+0000_y+0000"],
            "y_true": [40.0, 41.0],
            "y_pred": [39.0, 40.0],
        }
    )
    with pytest.raises(
        diagnostics.ResidualSpatialDiagnosticError,
        match="OOF truth differs from target manifest for B1",
    ):
        diagnostics._validate_selected_predictions(predictions, targets, config)
