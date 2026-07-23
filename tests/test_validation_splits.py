import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from la_heat.provenance import canonical_sha256
from la_heat.validation_splits import (
    ValidationSplitAuditError,
    assign_fold_roles,
    build_inner_cv_roles,
    build_validation_split_draft,
    build_validation_split_tables,
    prepare_fixed_tracts,
    prepare_row_groups,
    validate_oof_coverage,
)

YEARS = (2020, 2021)


def _tracts() -> gpd.GeoDataFrame:
    # The first two polygons are in adjacent 5 km blocks and exactly 1000 m apart.
    return gpd.GeoDataFrame(
        {
            "GEOID": ["a", "b", "c"],
            "spatial_block": [
                "x+0000_y+0000",
                "x+0001_y+0000",
                "x+0002_y+0000",
            ],
            "primary_included": [True, True, True],
        },
        geometry=[
            box(4800, 100, 4900, 200),
            box(5900, 100, 6000, 200),
            box(11000, 100, 11100, 200),
        ],
        crs="EPSG:3310",
    )


def _rows() -> pd.DataFrame:
    blocks = dict(zip(_tracts()["GEOID"], _tracts()["spatial_block"], strict=True))
    records = []
    for year in YEARS:
        for geoid in ("a", "b", "c"):
            records.append(
                {
                    "tract_geoid": geoid,
                    "target_date": f"{year}-07-01",
                    "spatial_block": blocks[geoid],
                    "target_lst_c": 30.0 + year,
                }
            )
    return pd.DataFrame(records)


def _tables(rows: pd.DataFrame | None = None, tracts: gpd.GeoDataFrame | None = None):
    return build_validation_split_tables(
        _rows() if rows is None else rows,
        _tracts() if tracts is None else tracts,
        development_years=YEARS,
        final_test_year=2025,
        analysis_crs="EPSG:3310",
        block_size_km=5.0,
        joint_buffer_m=1000.0,
    )


def test_split_tables_are_target_blind_and_deterministic_under_shuffle() -> None:
    original = _tables()
    changed = _rows().sample(frac=1, random_state=8).reset_index(drop=True)
    changed["target_lst_c"] = [-999.0, 10.0, 500.0, -20.0, 0.0, 99.0]
    shuffled = _tables(changed, _tracts().sample(frac=1, random_state=4))

    pd.testing.assert_frame_equal(original.row_groups, shuffled.row_groups)
    pd.testing.assert_frame_equal(original.fold_definitions, shuffled.fold_definitions)
    pd.testing.assert_frame_equal(
        original.spatial_buffer_geoids, shuffled.spatial_buffer_geoids
    )
    assert "target_lst_c" not in original.row_groups


def test_fold_families_are_full_cartesian_and_each_row_is_oof_once() -> None:
    tables = _tables()
    counts = tables.fold_definitions.groupby("family").size().to_dict()
    assert counts == {"joint": 6, "spatial": 3, "temporal": 2}
    audit = validate_oof_coverage(
        tables.row_groups,
        tables.fold_definitions,
        tables.spatial_buffer_geoids,
    )
    assert all(
        record["minimum_test_assignments_per_row"]
        == record["maximum_test_assignments_per_row"]
        == 1
        for record in audit.values()
    )


def test_joint_fold_purges_held_year_and_exact_1km_geometry_buffer() -> None:
    tables = _tables()
    buffer_a = tables.spatial_buffer_geoids.loc[
        tables.spatial_buffer_geoids["held_out_block"].eq("x+0000_y+0000")
    ]
    assert buffer_a["tract_geoid"].tolist() == ["a", "b"]
    assert buffer_a.set_index("tract_geoid").loc[
        "b", "distance_to_held_out_block_m"
    ] == pytest.approx(1000.0)
    assert buffer_a.set_index("tract_geoid").loc["b", "exclusion_role"] == "buffer_only"

    roles = assign_fold_roles(
        tables.row_groups,
        family="joint",
        held_out_year=2020,
        held_out_block="x+0000_y+0000",
        buffered_geoids=frozenset({"a", "b"}),
    )
    role_frame = tables.row_groups.assign(role=roles)
    assert role_frame.loc[role_frame["role"].eq("test"), "tract_geoid"].tolist() == ["a"]
    train = role_frame.loc[role_frame["role"].eq("train")]
    assert train[["year", "tract_geoid"]].to_records(index=False).tolist() == [(2021, "c")]
    assert len(role_frame.loc[role_frame["role"].eq("purged")]) == 4


def test_inner_cv_is_year_grouped_and_strictly_nested_in_outer_train() -> None:
    rows = []
    for year in range(2020, 2025):
        for record in _rows().iloc[:3].to_dict("records"):
            record["target_date"] = f"{year}-07-01"
            rows.append(record)
    tables = build_validation_split_tables(
        pd.DataFrame(rows),
        _tracts(),
        development_years=tuple(range(2020, 2025)),
        final_test_year=2025,
        analysis_crs="EPSG:3310",
        block_size_km=5.0,
        joint_buffer_m=1000.0,
    )
    buffers = frozenset(
        tables.spatial_buffer_geoids.loc[
            tables.spatial_buffer_geoids["held_out_block"].eq("x+0000_y+0000"),
            "tract_geoid",
        ]
    )
    outer_cases = {
        "temporal": assign_fold_roles(
            tables.row_groups, family="temporal", held_out_year=2020
        ),
        "spatial": assign_fold_roles(
            tables.row_groups,
            family="spatial",
            held_out_block="x+0000_y+0000",
        ),
        "joint": assign_fold_roles(
            tables.row_groups,
            family="joint",
            held_out_year=2020,
            held_out_block="x+0000_y+0000",
            buffered_geoids=buffers,
        ),
    }

    for family, outer_roles in outer_cases.items():
        inner = build_inner_cv_roles(tables.row_groups, outer_roles)
        assert len(inner) == (5 if family == "spatial" else 4)
        outer_excluded = ~outer_roles.eq("train")
        for validation_year, roles in inner.items():
            assert roles.loc[outer_excluded].eq("outer_excluded").all()
            validation = tables.row_groups.loc[roles.eq("validation")]
            train = tables.row_groups.loc[roles.eq("train")]
            assert set(validation["year"]) == {validation_year}
            assert validation_year not in set(train["year"])


@pytest.mark.parametrize(
    ("target_date", "message", "error_type"),
    [
        ("2025-07-01", "Locked final-test year", PermissionError),
        ("2020-07-01T12:00:00", "civil midnight", ValidationSplitAuditError),
        ("2020-07-01T00:00:00Z", "timezone-naive", ValidationSplitAuditError),
    ],
)
def test_row_groups_reject_locked_or_non_civil_dates(
    target_date: str, message: str, error_type: type[Exception]
) -> None:
    rows = _rows()
    rows.loc[0, "target_date"] = target_date
    with pytest.raises(error_type, match=message):
        prepare_row_groups(
            rows,
            development_years=YEARS,
            final_test_year=2025,
        )


def test_row_groups_reject_duplicate_keys_and_block_drift() -> None:
    duplicated = pd.concat([_rows(), _rows().iloc[[0]]], ignore_index=True)
    with pytest.raises(ValidationSplitAuditError, match="Duplicate tract-date"):
        _tables(duplicated)

    drifted = _rows()
    drifted.loc[0, "spatial_block"] = "x+9999_y+9999"
    with pytest.raises(ValidationSplitAuditError, match="disagrees with fixed tract block"):
        _tables(drifted)


def test_fixed_tracts_reject_recomputed_block_or_crs_mismatch() -> None:
    drifted = _tracts()
    drifted.loc[0, "spatial_block"] = "x+0001_y+0000"
    with pytest.raises(ValidationSplitAuditError, match="disagrees with tract centroid"):
        prepare_fixed_tracts(
            drifted,
            analysis_crs="EPSG:3310",
            block_size_km=5.0,
        )

    with pytest.raises(ValidationSplitAuditError, match="EPSG:3310"):
        prepare_fixed_tracts(
            _tracts().to_crs("EPSG:3857"),
            analysis_crs="EPSG:3310",
            block_size_km=5.0,
        )


def _write_test_config(project_root: Path) -> Path:
    configs = project_root / "configs"
    configs.mkdir(parents=True)
    output = project_root / "manifests" / "validation_splits"
    config = configs / "validation_splits.toml"
    config.write_text(
        f'''schema_version = 1
algorithm_version = "validation-splits-v1"
state = "predeclared_draft"
development_years = [2020, 2021, 2022, 2023, 2024]
final_test_year = 2025

[inputs]
row_groups = "data/rows.parquet"
tract_manifest = "data/tracts.parquet"

[spatial]
analysis_crs = "EPSG:3310"
block_size_km = 5.0
joint_buffer_m = 1000.0

[schemes.temporal]
strategy = "leave_one_calendar_year_out"
[schemes.spatial]
strategy = "leave_one_existing_spatial_block_out"
[schemes.joint]
strategy = "cartesian_year_x_block_with_geometry_buffer"

[inner_cv]
strategy = "leave_one_remaining_calendar_year_out"
scope = "outer_train_only"
preprocessing_fit_scope = "inner_train_only"

[outputs]
directory = "{output.relative_to(project_root).as_posix()}"
''',
        encoding="utf-8",
    )
    return config


def test_builder_writes_commit_marker_last_with_auditable_draft_state(tmp_path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    rows = []
    for year in range(2020, 2025):
        for record in _rows().iloc[:3].to_dict("records"):
            record["target_date"] = f"{year}-07-01"
            record["target_lst_c"] = year * 100.0
            rows.append(record)
    pd.DataFrame(rows).to_parquet(data / "rows.parquet", index=False)
    _tracts().to_parquet(data / "tracts.parquet", index=False)
    config = _write_test_config(tmp_path)

    payload = build_validation_split_draft(config)

    output = tmp_path / "manifests" / "validation_splits"
    assert payload["state"] == "predeclared_draft"
    assert payload["phase_complete"] is False
    assert payload["ready_for_model_evaluation"] is False
    assert payload["fold_counts"] == {"temporal": 5, "spatial": 3, "joint": 15}
    assert (output / "row_groups.parquet").is_file()
    assert (output / "fold_definitions.csv").is_file()
    assert (output / "spatial_buffer_geoids.parquet").is_file()
    marker = json.loads((output / "split_provenance.json").read_text(encoding="utf-8"))
    recorded = marker.pop("commit_sha256")
    assert recorded == canonical_sha256(marker)
    assert marker["input_column_contract"]["target_or_predictor_values_read"] is False
