from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import la_heat.phase2_registry as phase2_registry_module
from la_heat.calendar_features import calendar_feature_registry_rows
from la_heat.feature_registry import validate_feature_registry
from la_heat.phase2_registry import (
    DAYMET_SOURCE,
    HISTORICAL_ARCHIVE_AVAILABLE_BY,
    PHASE2_REGISTRY_FILENAME,
    PHASE2_REGISTRY_PROVENANCE_FILENAME,
    SENTINEL_SOURCE,
    STATIC_FEATURE_ORDER,
    Phase2RegistryError,
    build_phase2_registry,
    construct_phase2_registry,
    daymet_feature_registry_rows,
)
from la_heat.provenance import canonical_sha256, sha256_file
from la_heat.sentinel_features import INDEX_COLUMNS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_REGISTRY = (
    PROJECT_ROOT / "data/processed/static_features/static_feature_registry.csv"
)

EXPECTED_DAYMET_NAMES = tuple(
    f"daymet_{stem}_{aggregation}_prev_{window}d"
    for window in (1, 3, 7)
    for stem, aggregation in (
        ("dayl_s", "mean"),
        ("prcp_mm", "sum"),
        ("srad_w_m2", "mean"),
        ("tmax_c", "mean"),
        ("tmin_c", "mean"),
        ("vp_pa", "mean"),
        ("srad_energy_mj_m2", "sum"),
    )
)
EXPECTED_DAYMET_UNITS = (
    "s/day",
    "mm",
    "W/m^2",
    "degrees C",
    "degrees C",
    "Pa",
    "MJ/m^2",
) * 3


def _static() -> pd.DataFrame:
    return pd.read_csv(STATIC_REGISTRY)


def test_combined_registry_has_exact_order_counts_and_contract() -> None:
    combined = construct_phase2_registry(_static())
    calendar_names = tuple(calendar_feature_registry_rows()["feature_name"])
    expected_names = (
        *STATIC_FEATURE_ORDER,
        *calendar_names,
        *EXPECTED_DAYMET_NAMES,
        *INDEX_COLUMNS,
    )

    assert tuple(combined["feature_name"]) == expected_names
    assert len(combined) == 49
    assert combined["feature_name"].is_unique
    assert combined["role"].value_counts().to_dict() == {
        "model": 46,
        "key": 2,
        "audit_only": 1,
    }
    assert combined.loc[combined["family"].eq("weather"), "role"].eq("model").all()
    assert combined.loc[combined["family"].eq("satellite"), "role"].eq("model").all()
    validate_feature_registry(combined, development_start="2020-05-01")


def test_daymet_names_units_offsets_and_source_are_exact() -> None:
    rows = daymet_feature_registry_rows()

    assert tuple(rows["feature_name"]) == EXPECTED_DAYMET_NAMES
    assert tuple(rows["units"]) == EXPECTED_DAYMET_UNITS
    assert tuple(rows["source_start_offset_days"]) == (
        *([-1] * 7),
        *([-3] * 7),
        *([-7] * 7),
    )
    assert rows["source_end_offset_days"].eq(-1).all()
    assert rows["source"].eq(DAYMET_SOURCE).all()
    assert rows["source"].str.contains("Daymet V4 R1", regex=False).all()
    assert rows["source"].str.contains("10.3334/ORNLDAAC/2129", regex=False).all()
    assert rows["available_by"].eq(HISTORICAL_ARCHIVE_AVAILABLE_BY).all()


def test_sentinel_names_units_offsets_and_source_are_exact() -> None:
    combined = construct_phase2_registry(_static())
    rows = combined.loc[combined["family"].eq("satellite")]

    assert tuple(rows["feature_name"]) == INDEX_COLUMNS
    assert rows["units"].eq("unitless").all()
    assert rows["source_start_offset_days"].eq(-60).all()
    assert rows["source_end_offset_days"].eq(-1).all()
    assert rows["source"].eq(SENTINEL_SOURCE).all()
    assert rows["source"].str.contains("Sentinel-2 L2A historical archive", regex=False).all()
    assert rows["available_by"].eq(HISTORICAL_ARCHIVE_AVAILABLE_BY).all()


def test_model_registry_has_no_thermal_target_identifier_or_dynamic_audit_fields() -> None:
    combined = construct_phase2_registry(_static())
    model_names = combined.loc[combined["role"].eq("model"), "feature_name"]
    normalized = model_names.str.casefold()

    for forbidden in ("thermal", "lst", "st_b10", "target", "geoid", "tract_id"):
        assert not normalized.str.contains(forbidden, regex=False).any()
    dynamic = combined.loc[~combined["static"] & ~combined["role"].eq("key")]
    assert dynamic["role"].eq("model").all()
    assert not dynamic["feature_name"].str.contains(
        "coverage|count|qa|lineage|available", case=False, regex=True
    ).any()


def test_static_row_shuffle_is_canonicalized() -> None:
    baseline = construct_phase2_registry(_static())
    shuffled = construct_phase2_registry(
        _static().sample(frac=1, random_state=29).reset_index(drop=True)
    )

    pd.testing.assert_frame_equal(baseline, shuffled)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda frame: frame.assign(
            source=frame["source"].mask(frame.index == 2, "tampered source")
        ),
        lambda frame: frame.assign(
            role=frame["role"].mask(frame.index == 2, "audit_only")
        ),
        lambda frame: frame.drop(index=frame.index[-1]),
    ],
)
def test_tampered_static_registry_fails_closed(mutate) -> None:
    with pytest.raises(Phase2RegistryError, match="promoted semantic lock"):
        construct_phase2_registry(mutate(_static()))


def test_build_writes_output_then_valid_commit_marker(tmp_path: Path) -> None:
    output = tmp_path / "manifest"
    payload = build_phase2_registry(STATIC_REGISTRY, output)

    assert payload["status"] == "predeclared_draft"
    assert payload["phase2_complete"] is False
    assert payload["dynamic_values_complete"] is False
    assert payload["dynamic_coverage_complete"] is False
    assert payload["target_or_qa_tables_read"] == []
    assert payload["row_count"] == 49
    assert payload["configuration_semantic_sha256"]
    assert payload["pipeline_sha256"]

    registry_path = output / PHASE2_REGISTRY_FILENAME
    marker_path = output / PHASE2_REGISTRY_PROVENANCE_FILENAME
    assert sha256_file(registry_path) == payload["output_files"][
        PHASE2_REGISTRY_FILENAME
    ]["sha256"]
    committed = json.loads(marker_path.read_text(encoding="utf-8"))
    commit_sha256 = committed.pop("commit_sha256")
    assert canonical_sha256(committed) == commit_sha256
    assert commit_sha256 == payload["commit_sha256"]


def test_failed_marker_write_leaves_no_false_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "manifest"
    output.mkdir()
    marker = output / PHASE2_REGISTRY_PROVENANCE_FILENAME
    marker.write_text('{"stale": true}', encoding="utf-8")

    def fail_json(payload, destination):
        raise OSError("simulated marker failure")

    monkeypatch.setattr(phase2_registry_module, "atomic_json", fail_json)
    with pytest.raises(OSError, match="simulated marker failure"):
        build_phase2_registry(STATIC_REGISTRY, output)

    assert not marker.exists()
    assert (output / PHASE2_REGISTRY_FILENAME).is_file()

