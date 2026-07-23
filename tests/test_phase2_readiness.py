from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from la_heat.daymet_feature_stage import DAYMET_PRIMARY_WINDOWS
from la_heat.feature_universe import KEY_COLUMNS
from la_heat.phase2_readiness import (
    CALENDAR_COLUMNS,
    SENTINEL_AUDIT_REQUIRED_COLUMNS,
    Phase2ReadinessError,
    _audit_daymet_state,
    validate_ready_feature_families,
)
from la_heat.provenance import (
    canonical_frame_sha256,
    canonical_sha256,
    sha256_file,
)
from la_heat.sentinel_features import INDEX_COLUMNS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = (
    PROJECT_ROOT / "manifests/phase2_registry/combined_feature_registry_draft.csv"
)


def _inputs() -> dict[str, pd.DataFrame]:
    registry = pd.read_csv(REGISTRY_PATH)
    dates = pd.to_datetime(["2024-06-01", "2024-06-17"])
    tracts = ["06037101110", "06037101122"]
    key_universe = pd.MultiIndex.from_product(
        [tracts, dates], names=list(KEY_COLUMNS)
    ).to_frame(index=False)
    key_universe["tract_geoid"] = key_universe["tract_geoid"].astype("string")

    static_names = registry.loc[
        registry["static"].astype(bool) & ~registry["role"].eq("key"), "feature_name"
    ].tolist()
    static_features = pd.DataFrame({"tract_geoid": tracts})
    for index, name in enumerate(static_names, start=1):
        static_features[name] = np.array([index, index + 0.5], dtype=float)
    static_audit = pd.DataFrame(
        {
            "tract_geoid": tracts,
            "eligible_pixel_count_static": [100, 200],
            "eligible_pixel_identity_sha256": ["a" * 64, "b" * 64],
        }
    )

    calendar = key_universe.copy()
    calendar[CALENDAR_COLUMNS[0]] = [0.1, 0.2, 0.1, 0.2]
    calendar[CALENDAR_COLUMNS[1]] = [0.9, 0.8, 0.9, 0.8]

    sentinel = key_universe.copy()
    available = np.array([True, False, True, False])
    for index, name in enumerate(INDEX_COLUMNS, start=1):
        sentinel[name] = np.where(available, index / 10, np.nan)
    audit = key_universe.copy()
    audit_values = {
        "window_membership_count": [4, 4, 4, 4],
        "qualifying_acquisition_count": [3, 2, 3, 2],
        "minimum_lag_days": [1, 1, 1, 1],
        "maximum_lag_days": [4, 4, 4, 4],
        "median_acquisition_coverage": [0.9, 0.4, 0.9, 0.4],
        "newest_source_end_date": audit["target_date"] - pd.Timedelta(days=1),
        "oldest_source_end_date": audit["target_date"] - pd.Timedelta(days=4),
        "sentinel_feature_available": available,
    }
    for name in SENTINEL_AUDIT_REQUIRED_COLUMNS:
        audit[name] = audit_values[name]

    denominator = static_audit.set_index("tract_geoid")
    lineage = key_universe.loc[key_universe.index.repeat(4)].reset_index(drop=True)
    lineage["lag_days"] = np.tile(np.arange(1, 5), len(key_universe))
    lineage["physical_acquisition_id"] = [
        f"acquisition-{key_index}-{lag}"
        for key_index in range(len(key_universe))
        for lag in range(1, 5)
    ]
    lineage["source_end_date"] = lineage["target_date"] - pd.to_timedelta(
        lineage["lag_days"], unit="D"
    )
    lineage["acquisition_local_date"] = lineage["source_end_date"]
    lineage["source_age_days_audit_only"] = lineage["lag_days"]
    lineage["eligible_pixel_count_static"] = lineage["tract_geoid"].map(
        denominator["eligible_pixel_count_static"]
    )
    lineage["eligible_pixel_identity_sha256_audit_only"] = lineage["tract_geoid"].map(
        denominator["eligible_pixel_identity_sha256"]
    )
    qualifying_by_row = np.repeat([3, 2, 3, 2], 4)
    lineage["included_in_composite"] = (
        np.tile(np.arange(1, 5), len(key_universe)) <= qualifying_by_row
    )

    return {
        "key_universe": key_universe,
        "registry": registry,
        "static_features": static_features,
        "static_audit": static_audit,
        "calendar_features": calendar,
        "sentinel_features": sentinel,
        "sentinel_audit": audit,
        "sentinel_lineage": lineage,
    }


def _write_present_daymet_artifacts(
    tmp_path: Path,
    inputs: dict[str, pd.DataFrame],
    *,
    mutate: Callable[[pd.DataFrame, pd.DataFrame], None] | None = None,
) -> dict[str, Path]:
    registry = inputs["registry"]
    weather_names = registry.loc[
        registry["family"].eq("weather") & registry["role"].eq("model"),
        "feature_name",
    ].tolist()
    complete_days = {
        1: np.array([1, 1, 1, 0], dtype=int),
        3: np.array([3, 3, 2, 0], dtype=int),
        7: np.array([7, 6, 2, 0], dtype=int),
    }
    features = inputs["key_universe"].copy()
    for feature_index, name in enumerate(weather_names, start=1):
        window_days = next(
            window
            for window in DAYMET_PRIMARY_WINDOWS
            if name.endswith(f"prev_{window}d")
        )
        features[name] = np.where(
            complete_days[window_days] == window_days,
            feature_index / 10,
            np.nan,
        )

    audit = inputs["key_universe"].copy()
    for window_days in DAYMET_PRIMARY_WINDOWS:
        suffix = f"prev_{window_days}d"
        audit[f"daymet_source_start_date_{suffix}"] = (
            audit["target_date"] - pd.Timedelta(days=window_days)
        )
        audit[f"daymet_source_end_date_{suffix}"] = (
            audit["target_date"] - pd.Timedelta(days=1)
        )
        audit[f"daymet_source_days_expected_{suffix}"] = window_days
        audit[f"daymet_source_days_complete_{suffix}"] = complete_days[window_days]
    audit["daymet_source_start_date"] = audit["daymet_source_start_date_prev_7d"]
    audit["daymet_source_end_date"] = audit["daymet_source_end_date_prev_7d"]
    audit["daymet_source_days_expected"] = audit[
        "daymet_source_days_expected_prev_7d"
    ]
    audit["daymet_source_days_complete"] = audit[
        "daymet_source_days_complete_prev_7d"
    ]
    audit["daymet_grid_cells_expected"] = 1
    audit["daymet_grid_cells_present_min"] = np.where(
        audit["daymet_source_days_complete"].eq(7), 1, 0
    )
    audit["daymet_static_eligible_area_m2"] = audit["tract_geoid"].map(
        inputs["static_audit"].set_index("tract_geoid")[
            "eligible_pixel_count_static"
        ]
    ) * 900.0
    audit["daymet_all_primary_windows_complete"] = np.logical_and.reduce(
        [
            complete_days[window_days] == window_days
            for window_days in DAYMET_PRIMARY_WINDOWS
        ]
    )
    if mutate is not None:
        mutate(features, audit)

    weights = inputs["static_audit"].loc[
        :, ["tract_geoid", "eligible_pixel_count_static"]
    ].rename(columns={"eligible_pixel_count_static": "eligible_pixel_count"})
    weights["daymet_cell_id"] = ["cell-a", "cell-b"]
    weights["static_denominator_m2"] = weights["eligible_pixel_count"] * 900.0
    weights["weight"] = 1.0

    inventory = pd.DataFrame(
        [
            {"variable": variable, "year": year}
            for year in range(2020, 2025)
            for variable in ("tmax", "tmin", "prcp", "srad", "vp", "dayl")
        ]
    )
    inventory_path = tmp_path / "granule_inventory.csv"
    inventory.to_csv(inventory_path, index=False)
    summary_path = tmp_path / "inventory_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "state": "subsets_complete",
                "contains_final_test_year": False,
                "inventory_file_sha256": sha256_file(inventory_path),
                "granule_count": len(inventory),
                "inventory_semantic_sha256": canonical_frame_sha256(
                    inventory, sort_by=["year", "variable"]
                ),
            }
        ),
        encoding="utf-8",
    )

    feature_path = tmp_path / "daymet_features.parquet"
    audit_path = tmp_path / "daymet_feature_audit.parquet"
    weights_path = tmp_path / "daymet_fixed_cell_weights.parquet"
    features.to_parquet(feature_path, index=False)
    audit.to_parquet(audit_path, index=False)
    weights.to_parquet(weights_path, index=False)
    provenance_path = tmp_path / "daymet_features_provenance.json"
    provenance = {
        "target_blind": True,
        "target_or_qa_tables_read": [],
        "source_end_offset_days": -1,
        "window_days": list(DAYMET_PRIMARY_WINDOWS),
        "static_eligible_land_denominator_invariant": True,
        "date_specific_weight_renormalization": False,
        "srad_energy_computed_cell_first": True,
        "output_files": {
            feature_path.name: {
                "sha256": sha256_file(feature_path),
                "rows": len(features),
            },
            audit_path.name: {
                "sha256": sha256_file(audit_path),
                "rows": len(audit),
            },
            weights_path.name: {
                "sha256": sha256_file(weights_path),
                "rows": len(weights),
            },
        },
    }
    provenance["commit_sha256"] = canonical_sha256(provenance)
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
    return {
        "inventory_path": inventory_path,
        "summary_path": summary_path,
        "subset_manifest_path": tmp_path / "subset_downloads.csv",
        "feature_path": feature_path,
        "provenance_path": provenance_path,
    }


def _audit_present_daymet(
    paths: dict[str, Path], inputs: dict[str, pd.DataFrame]
) -> tuple[dict[str, object], dict[str, object]]:
    return _audit_daymet_state(
        **paths,
        universe=inputs["key_universe"],
        registry=inputs["registry"],
        static_audit=inputs["static_audit"],
        final_test_year=2025,
    )


def test_present_daymet_path_validates_all_windows_and_21_feature_availability(
    tmp_path: Path,
) -> None:
    inputs = _inputs()
    paths = _write_present_daymet_artifacts(tmp_path, inputs)

    family, records = _audit_present_daymet(paths, inputs)

    assert family == {
        "family": "daymet",
        "status": "complete",
        "row_count": 4,
        "feature_count": 21,
        "available_row_count": 1,
        "missing_row_count": 3,
        "notes": (
            "complete civil-day windows d-n through d-1; any missing dynamic "
            "values remain explicit for fold-local imputation"
        ),
    }
    assert {
        "daymet_features",
        "daymet_features_provenance",
        "daymet_feature_audit",
        "daymet_fixed_cell_weights",
    }.issubset(records)


def test_present_daymet_path_rejects_window_feature_finiteness_disagreement(
    tmp_path: Path,
) -> None:
    inputs = _inputs()

    def mutate(features: pd.DataFrame, audit: pd.DataFrame) -> None:
        del audit
        seven_day_features = [
            name
            for name in features
            if name.startswith("daymet_") and name.endswith("prev_7d")
        ]
        features.loc[1, seven_day_features] = 1.0

    paths = _write_present_daymet_artifacts(tmp_path, inputs, mutate=mutate)

    with pytest.raises(Phase2ReadinessError, match="7-day feature finiteness"):
        _audit_present_daymet(paths, inputs)


@pytest.mark.parametrize(
    "column, value, message",
    [
        (
            "daymet_source_start_date_prev_3d",
            pd.Timestamp("2024-05-30"),
            "3-day audit does not start exactly",
        ),
        (
            "daymet_source_days_expected_prev_1d",
            2,
            "within \\[1, 1\\]",
        ),
        (
            "daymet_source_days_complete_prev_7d",
            8,
            "within \\[0, 7\\]",
        ),
    ],
)
def test_present_daymet_path_rejects_illegal_window_audit(
    tmp_path: Path,
    column: str,
    value: object,
    message: str,
) -> None:
    inputs = _inputs()

    def mutate(features: pd.DataFrame, audit: pd.DataFrame) -> None:
        del features
        audit.loc[0, column] = value

    paths = _write_present_daymet_artifacts(tmp_path, inputs, mutate=mutate)

    with pytest.raises(Phase2ReadinessError, match=message):
        _audit_present_daymet(paths, inputs)


def test_ready_families_validate_target_blind_exact_key_contract() -> None:
    rows = validate_ready_feature_families(**_inputs())
    by_family = {row["family"]: row for row in rows}

    assert set(by_family) == {
        "key_universe",
        "registry",
        "static",
        "calendar",
        "sentinel",
    }
    assert all(row["status"] == "complete" for row in rows)
    assert by_family["sentinel"]["available_row_count"] == 2
    assert by_family["sentinel"]["missing_row_count"] == 2


def test_partial_sentinel_feature_row_fails_closed() -> None:
    inputs = _inputs()
    inputs["sentinel_features"].loc[0, INDEX_COLUMNS[0]] = np.nan

    with pytest.raises(Phase2ReadinessError, match="all five finite"):
        validate_ready_feature_families(**inputs)


def test_target_day_sentinel_lineage_fails_closed() -> None:
    inputs = _inputs()
    target_date = inputs["sentinel_lineage"].loc[0, "target_date"]
    inputs["sentinel_lineage"].loc[0, "source_end_date"] = target_date
    inputs["sentinel_lineage"].loc[0, "acquisition_local_date"] = target_date
    inputs["sentinel_lineage"].loc[0, "lag_days"] = 0
    inputs["sentinel_lineage"].loc[0, "source_age_days_audit_only"] = 0

    with pytest.raises(Phase2ReadinessError, match="target-day or future"):
        validate_ready_feature_families(**inputs)


def test_changing_sentinel_denominator_fails_closed() -> None:
    inputs = _inputs()
    inputs["sentinel_lineage"].loc[1, "eligible_pixel_count_static"] = 101

    with pytest.raises(Phase2ReadinessError, match="changes a tract's static"):
        validate_ready_feature_families(**inputs)


def test_locked_2025_key_fails_closed() -> None:
    inputs = _inputs()
    inputs["key_universe"].loc[0, "target_date"] = pd.Timestamp("2025-06-01")

    with pytest.raises(PermissionError, match="2025"):
        validate_ready_feature_families(**inputs)
