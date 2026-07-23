from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import la_heat.feature_universe as feature_universe_module
from la_heat.feature_universe import (
    FEATURE_UNIVERSE_FILENAME,
    FEATURE_UNIVERSE_PROVENANCE_FILENAME,
    FeatureUniverseError,
    build_feature_key_universe,
    construct_feature_key_universe,
    validate_feature_key_universe,
)
from la_heat.provenance import canonical_sha256, sha256_file


def _sha(seed: int) -> str:
    return f"{seed:064x}"


def _overpasses() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "overpass_id": ["overpass-a", "overpass-b"],
            "local_date": ["2023-07-01", "2024-07-02"],
            "primary_eligible": [True, True],
            "source_lock_sha256": [_sha(1), _sha(2)],
        }
    )


def _tracts() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "GEOID": ["06037000100", "06037000200", "06037000300"],
            "primary_included": [True, True, True],
            "tract_manifest_sha256": [_sha(10)] * 3,
        }
    )


def _write_inputs(
    root: Path,
    overpasses: pd.DataFrame | None = None,
    tracts: pd.DataFrame | None = None,
) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    overpass_path = root / "primary_overpass_manifest.csv"
    tract_path = root / "primary_tract_manifest.parquet"
    (overpasses if overpasses is not None else _overpasses()).to_csv(
        overpass_path,
        index=False,
    )
    (tracts if tracts is not None else _tracts()).to_parquet(tract_path, index=False)
    return overpass_path, tract_path


def _build(root: Path, overpasses=None, tracts=None) -> tuple[dict, pd.DataFrame]:
    overpass_path, tract_path = _write_inputs(root / "inputs", overpasses, tracts)
    output = root / "output"
    payload = build_feature_key_universe(
        overpass_path,
        tract_path,
        output,
        expected_date_count=2,
        expected_tract_count=3,
    )
    return payload, pd.read_parquet(output / FEATURE_UNIVERSE_FILENAME)


def test_builds_exact_cartesian_target_blind_grid_and_commit(tmp_path: Path) -> None:
    payload, output = _build(tmp_path)

    expected = pd.DataFrame(
        {
            "tract_geoid": pd.Series(
                [
                    "06037000100",
                    "06037000200",
                    "06037000300",
                    "06037000100",
                    "06037000200",
                    "06037000300",
                ],
                dtype="string",
            ),
            "target_date": pd.Series(
                ["2023-07-01"] * 3 + ["2024-07-02"] * 3,
                dtype="datetime64[ns]",
            ),
        }
    )
    pd.testing.assert_frame_equal(output, expected)
    assert output["target_date"].dt.tz is None
    assert (output["target_date"] == output["target_date"].dt.normalize()).all()
    assert payload["status"] == "target_blind_draft"
    assert payload["phase2_promoted"] is False
    assert payload["target_tables_read"] == []
    assert payload["eligible_date_count"] == 2
    assert payload["primary_tract_count"] == 3
    assert payload["key_count"] == 6
    assert payload["years"] == [2023, 2024]

    provenance_path = tmp_path / "output" / FEATURE_UNIVERSE_PROVENANCE_FILENAME
    committed = json.loads(provenance_path.read_text(encoding="utf-8"))
    checksum = committed.pop("commit_sha256")
    assert canonical_sha256(committed) == checksum
    record = committed["output_files"][FEATURE_UNIVERSE_FILENAME]
    assert record["sha256"] == sha256_file(tmp_path / "output" / FEATURE_UNIVERSE_FILENAME)
    assert record["rows"] == 6


def test_input_shuffling_does_not_change_output_or_semantic_hash(tmp_path: Path) -> None:
    first, first_output = _build(tmp_path / "first")
    second, second_output = _build(
        tmp_path / "second",
        _overpasses().sample(frac=1, random_state=7).reset_index(drop=True),
        _tracts().sample(frac=1, random_state=8).reset_index(drop=True),
    )

    pd.testing.assert_frame_equal(first_output, second_output)
    assert first["semantic_key_sha256"] == second["semantic_key_sha256"]
    assert (
        first["inputs"]["primary_overpass_manifest"]["semantic_sha256"]
        == second["inputs"]["primary_overpass_manifest"]["semantic_sha256"]
    )
    assert (
        first["inputs"]["primary_tract_manifest"]["semantic_sha256"]
        == second["inputs"]["primary_tract_manifest"]["semantic_sha256"]
    )
    assert (
        first["output_files"][FEATURE_UNIVERSE_FILENAME]["sha256"]
        == second["output_files"][FEATURE_UNIVERSE_FILENAME]["sha256"]
    )


def test_irrelevant_target_like_input_columns_do_not_affect_keys_or_semantic_hash(
    tmp_path: Path,
) -> None:
    first, first_output = _build(tmp_path / "first")
    overpasses = _overpasses().assign(
        target_lst_c=[999.0, -999.0],
        date_usable=[False, True],
    )
    tracts = _tracts().assign(
        target_available=[True, False, True],
        valid_pixel_count=[0, 1, 2],
    )
    second, second_output = _build(tmp_path / "second", overpasses, tracts)

    pd.testing.assert_frame_equal(first_output, second_output)
    assert first["semantic_key_sha256"] == second["semantic_key_sha256"]
    assert first["inputs"]["primary_overpass_manifest"]["sha256"] != second["inputs"][
        "primary_overpass_manifest"
    ]["sha256"]
    assert first["inputs"]["primary_tract_manifest"]["sha256"] != second["inputs"][
        "primary_tract_manifest"
    ]["sha256"]


@pytest.mark.parametrize(
    ("mutator", "match", "exception"),
    [
        (
            lambda overpasses, tracts: (
                overpasses.assign(local_date=["2023-07-01", "2023-07-01"]),
                tracts,
            ),
            "duplicate local dates",
            FeatureUniverseError,
        ),
        (
            lambda overpasses, tracts: (
                overpasses.assign(local_date=["not-a-date", "2024-07-02"]),
                tracts,
            ),
            "invalid calendar date",
            FeatureUniverseError,
        ),
        (
            lambda overpasses, tracts: (
                overpasses.assign(
                    local_date=["2023-07-01T00:00:00+00:00", "2024-07-02"]
                ),
                tracts,
            ),
            "timezone-naive",
            FeatureUniverseError,
        ),
        (
            lambda overpasses, tracts: (
                overpasses.assign(local_date=["2023-07-01", "2025-07-02"]),
                tracts,
            ),
            "locked dates",
            PermissionError,
        ),
        (
            lambda overpasses, tracts: (
                overpasses,
                tracts.assign(GEOID=["06037000100", "06037000100", "06037000300"]),
            ),
            "duplicate GEOIDs",
            FeatureUniverseError,
        ),
        (
            lambda overpasses, tracts: (
                overpasses,
                tracts.assign(GEOID=["bad", "06037000200", "06037000300"]),
            ),
            "canonical 11-digit",
            FeatureUniverseError,
        ),
        (
            lambda overpasses, tracts: (
                overpasses.assign(primary_eligible=[True, "yes"]),
                tracts,
            ),
            "only non-null booleans",
            FeatureUniverseError,
        ),
        (
            lambda overpasses, tracts: (
                overpasses,
                tracts.assign(primary_included=[True, 1, True]),
            ),
            "only non-null booleans",
            FeatureUniverseError,
        ),
        (
            lambda overpasses, tracts: (
                overpasses.assign(source_lock_sha256=[_sha(1), "broken"]),
                tracts,
            ),
            "canonical lowercase SHA-256",
            FeatureUniverseError,
        ),
        (
            lambda overpasses, tracts: (
                overpasses,
                tracts.assign(tract_manifest_sha256=[_sha(10), _sha(11), _sha(10)]),
            ),
            "inconsistent",
            FeatureUniverseError,
        ),
    ],
)
def test_invalid_or_locked_inputs_fail_closed(mutator, match, exception) -> None:
    overpasses, tracts = mutator(_overpasses(), _tracts())

    with pytest.raises(exception, match=match):
        construct_feature_key_universe(
            overpasses,
            tracts,
            expected_date_count=2,
            expected_tract_count=3,
        )


def test_validator_rejects_target_like_columns() -> None:
    universe, _ = construct_feature_key_universe(
        _overpasses(),
        _tracts(),
        expected_date_count=2,
        expected_tract_count=3,
    )
    universe["target_lst_c"] = 35.0

    with pytest.raises(FeatureUniverseError, match="Target-like columns"):
        validate_feature_key_universe(universe)


def test_failed_commit_write_clears_stale_marker_but_leaves_atomic_parquet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    overpass_path, tract_path = _write_inputs(tmp_path / "inputs")
    output = tmp_path / "output"
    output.mkdir()
    marker = output / FEATURE_UNIVERSE_PROVENANCE_FILENAME
    marker.write_text('{"stale": true}', encoding="utf-8")

    def fail_json(payload, destination):
        raise OSError("simulated commit failure")

    monkeypatch.setattr(feature_universe_module, "atomic_json", fail_json)
    with pytest.raises(OSError, match="simulated commit failure"):
        build_feature_key_universe(
            overpass_path,
            tract_path,
            output,
            expected_date_count=2,
            expected_tract_count=3,
        )

    assert not marker.exists()
    feature_path = output / FEATURE_UNIVERSE_FILENAME
    assert feature_path.is_file()
    assert not feature_path.with_suffix(".parquet.partial").exists()
    validate_feature_key_universe(pd.read_parquet(feature_path))
