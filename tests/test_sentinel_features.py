from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from la_heat.config import load_config
from la_heat.grid import FixedGrid
from la_heat.provenance import atomic_json, atomic_parquet, parquet_file_record, sha256_file
from la_heat.sentinel_feature_builder import (
    _acquisition_cache_is_current,
    _load_frozen_sentinel_inventory,
    _read_asset_to_optical_grid,
    _validate_native_asset_grid,
    load_sentinel_stage_config,
)
from la_heat.sentinel_features import (
    INDEX_COLUMNS,
    REFLECTANCE_BANDS,
    AlignedSentinelTile,
    aggregate_acquisition_to_tracts,
    build_previous_60_day_composites,
    clear_land_mask,
    compute_optical_indices,
    decode_boa_reflectance,
    mosaic_aligned_tiles,
    parse_boa_calibration,
)

ALBEDO = {
    "B02": 0.2266,
    "B03": 0.1236,
    "B04": 0.1573,
    "B08": 0.3417,
    "B11": 0.1170,
    "B12": 0.0338,
}


def _metadata(*, offsets: bool, baseline: str = "04.00", omit_id: int | None = None) -> str:
    offset_xml = ""
    if offsets:
        offset_xml = "<BOA_ADD_OFFSET_VALUES_LIST>" + "".join(
            f'<BOA_ADD_OFFSET band_id="{band_id}">-1000</BOA_ADD_OFFSET>'
            for band_id in range(13)
            if band_id != omit_id
        ) + "</BOA_ADD_OFFSET_VALUES_LIST>"
    return f"""
    <Level-2A_User_Product>
      <PROCESSING_BASELINE>{baseline}</PROCESSING_BASELINE>
      <QUANTIFICATION_VALUES_LIST>
        <BOA_QUANTIFICATION_VALUE>10000</BOA_QUANTIFICATION_VALUE>
      </QUANTIFICATION_VALUES_LIST>
      {offset_xml}
    </Level-2A_User_Product>
    """


def _grid(*, resolution: float, width: int, height: int) -> FixedGrid:
    top = resolution * height
    return FixedGrid(
        crs="EPSG:32611",
        resolution_m=resolution,
        anchor_x_m=0.0,
        anchor_y_m=0.0,
        left=0.0,
        bottom=0.0,
        right=resolution * width,
        top=top,
        width=width,
        height=height,
        transform=from_origin(0.0, top, resolution, resolution),
    )


def test_baseline_04_offsets_are_mandatory_and_metadata_baseline_is_locked() -> None:
    parsed = parse_boa_calibration(_metadata(offsets=True), processing_baseline="04.00")

    assert parsed.quantification_value == 10000
    assert parsed.offset_by_band["B02"] == -1000
    assert parsed.offset_by_band["B8A"] == -1000
    assert len(parsed.sha256) == 64

    with pytest.raises(ValueError, match="requires BOA offsets"):
        parse_boa_calibration(_metadata(offsets=False), processing_baseline="04.00")
    with pytest.raises(ValueError, match=r"missing \['B02'\]"):
        parse_boa_calibration(
            _metadata(offsets=True, omit_id=1), processing_baseline="04.00"
        )
    with pytest.raises(ValueError, match="disagrees with the frozen inventory"):
        parse_boa_calibration(
            _metadata(offsets=True, baseline="05.10"), processing_baseline="04.00"
        )


def test_pre_04_defaults_to_zero_offset_but_partial_lists_fail_closed() -> None:
    calibration = parse_boa_calibration(
        _metadata(offsets=False, baseline="02.12"), processing_baseline="02.12"
    )
    assert set(calibration.offset_by_band.values()) == {0.0}

    with pytest.raises(ValueError, match="partial BOA offset"):
        parse_boa_calibration(
            _metadata(offsets=True, baseline="02.12", omit_id=1),
            processing_baseline="02.12",
        )


def test_boa_decode_applies_offset_and_masks_nodata_and_saturation() -> None:
    calibration = parse_boa_calibration(_metadata(offsets=True), processing_baseline="04.00")
    decoded = decode_boa_reflectance(
        np.array([[0, 1000, 2000, 65535]], dtype=np.uint16),
        band="B04",
        calibration=calibration,
    )

    assert np.isnan(decoded[0, 0])
    assert decoded[0, 1] == pytest.approx(0.0)
    assert decoded[0, 2] == pytest.approx(0.1)
    assert np.isnan(decoded[0, 3])


def test_scl_and_index_contracts_use_only_classes_4_5_and_no_denominator_clipping() -> None:
    values = {
        "B02": np.array([[0.1, 0.1, 0.1, 0.1]], dtype=np.float32),
        "B03": np.array([[0.2, 0.2, 0.2, 0.2]], dtype=np.float32),
        "B04": np.array([[0.2, 0.0, 0.2, 0.2]], dtype=np.float32),
        "B08": np.array([[0.6, 0.0, 0.6, 0.6]], dtype=np.float32),
        "B8A": np.array([[0.55, 0.55, 0.55, np.nan]], dtype=np.float32),
        "B11": np.array([[0.3, 0.3, 0.3, 0.3]], dtype=np.float32),
        "B12": np.array([[0.25, 0.25, 0.25, 0.25]], dtype=np.float32),
    }
    mask = clear_land_mask(np.array([[4, 5, 6, 4]]), values)
    indices = compute_optical_indices(
        values, denominator_epsilon=1e-6, albedo_coefficients=ALBEDO
    )

    assert mask.tolist() == [[True, True, False, False]]
    assert indices["ndvi"][0, 0] == pytest.approx(0.5)
    assert np.isnan(indices["ndvi"][0, 1])
    expected_albedo = sum(ALBEDO[band] * values[band][0, 0] for band in ALBEDO)
    assert indices["albedo_proxy"][0, 0] == pytest.approx(expected_albedo)


def _tile(item: str, mgrs: str, scl: np.ndarray, value: float) -> AlignedSentinelTile:
    return AlignedSentinelTile(
        item_id=item,
        mgrs_tile=mgrs,
        scl=scl,
        reflectance={
            band: np.full(scl.shape, value, dtype=np.float32)
            for band in REFLECTANCE_BANDS
        },
        calibration_sha256=f"cal-{item}",
    )


def test_adjacent_tiles_are_one_deterministic_mosaic_not_duplicate_observations() -> None:
    west = _tile("west", "11SLT", np.array([[4, 4, 0], [4, 4, 0]]), 0.2)
    east = _tile("east", "11SLU", np.array([[0, 5, 5], [0, 5, 5]]), 0.8)

    first = mosaic_aligned_tiles([east, west])
    second = mosaic_aligned_tiles([west, east])

    assert first.item_ids == ("west", "east")
    assert first.owned_pixel_counts == (4, 2)
    assert np.array_equal(first.owner_index, second.owner_index)
    assert first.reflectance["B04"][0].tolist() == pytest.approx([0.2, 0.2, 0.8])

    cloudy_first = _tile("cloudy", "11SLT", np.array([[8]]), 0.2)
    clear_second = _tile("clear", "11SLU", np.array([[4]]), 0.8)
    qa_priority = mosaic_aligned_tiles([cloudy_first, clear_second])
    assert qa_priority.item_ids == ("cloudy", "clear")
    assert qa_priority.owner_index.item() == 1
    assert qa_priority.reflectance["B04"].item() == pytest.approx(0.8)


def test_acquisition_aggregation_uses_frozen_denominator_and_coverage_gate() -> None:
    optical = _grid(resolution=20.0, width=3, height=3)
    target = _grid(resolution=30.0, width=2, height=2)
    zones = np.array([[1, 2], [3, 4]], dtype=np.int16)
    eligible = np.ones((2, 2), dtype=bool)
    base_valid = np.ones((3, 3), dtype=bool)
    base_valid[0, 0] = False
    indices = {
        raw_name: np.full((3, 3), index + 1, dtype=np.float32)
        for index, raw_name in enumerate(("ndvi", "evi", "ndwi", "ndbi", "albedo_proxy"))
    }
    result = aggregate_acquisition_to_tracts(
        physical_acquisition_id="physical-1",
        acquisition_local_date="2024-07-01",
        platform="sentinel-2a",
        processing_baseline="05.10",
        indices=indices,
        base_valid_20m=base_valid,
        optical_grid=optical,
        target_grid=target,
        zone_raster_30m=zones,
        eligible_land_30m=eligible,
        tract_geoids=["a", "b", "c", "d"],
        expected_eligible_counts={value: 1 for value in ("a", "b", "c", "d")},
        minimum_acquisition_coverage=0.8,
    )

    assert len(result) == 4
    assert result.loc[result.tract_geoid == "a", "acquisition_coverage_fraction"].item() < 0.8
    assert np.isnan(result.loc[result.tract_geoid == "a", INDEX_COLUMNS[0]].item())
    assert result.loc[result.tract_geoid == "d", INDEX_COLUMNS[0]].item() == pytest.approx(1.0)

    with pytest.raises(ValueError, match="frozen target lock"):
        aggregate_acquisition_to_tracts(
            physical_acquisition_id="physical-1",
            acquisition_local_date="2024-07-01",
            platform="sentinel-2a",
            processing_baseline="05.10",
            indices=indices,
            base_valid_20m=base_valid,
            optical_grid=optical,
            target_grid=target,
            zone_raster_30m=zones,
            eligible_land_30m=eligible,
            tract_geoids=["a", "b", "c", "d"],
            expected_eligible_counts={"a": 2, "b": 1, "c": 1, "d": 1},
            minimum_acquisition_coverage=0.8,
        )


def test_acquisition_coverage_requires_joint_validity_of_all_five_indices() -> None:
    grid = _grid(resolution=20.0, width=2, height=2)
    zones = np.ones((2, 2), dtype=np.int16)
    indices = {
        raw_name: np.ones((2, 2), dtype=np.float32)
        for raw_name in ("ndvi", "evi", "ndwi", "ndbi", "albedo_proxy")
    }
    # SCL/band validity is 100%, but one index passes its denominator gate on
    # only 75% of the frozen support.  Joint coverage must therefore fail 80%.
    indices["evi"][0, 0] = np.nan
    result = aggregate_acquisition_to_tracts(
        physical_acquisition_id="physical-joint-mask",
        acquisition_local_date="2024-07-01",
        platform="sentinel-2a",
        processing_baseline="05.10",
        indices=indices,
        base_valid_20m=np.ones((2, 2), dtype=bool),
        optical_grid=grid,
        target_grid=grid,
        zone_raster_30m=zones,
        eligible_land_30m=np.ones((2, 2), dtype=bool),
        tract_geoids=["a"],
        expected_eligible_counts={"a": 4},
        minimum_acquisition_coverage=0.8,
    )

    assert result["acquisition_coverage_fraction"].item() == pytest.approx(0.75)
    assert not result["acquisition_qualifies_coverage"].item()
    assert result[list(INDEX_COLUMNS)].isna().all(axis=None)


def _acquisition_rows() -> pd.DataFrame:
    records = []
    dates = ["2024-07-01", "2024-07-31", "2024-08-19", "2024-08-29"]
    for index, acquired in enumerate(dates, start=1):
        for geoid in ("a", "b"):
            record = {
                "tract_geoid": geoid,
                "physical_acquisition_id": f"physical-{index}",
                "acquisition_local_date": acquired,
                "acquisition_coverage_fraction": 0.9,
            }
            record.update({column: float(index) for column in INDEX_COLUMNS})
            if geoid == "b" and index > 2:
                record["acquisition_coverage_fraction"] = 0.7
            records.append(record)
    return pd.DataFrame(records)


def _membership() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "target_date": ["2024-08-30"] * 4,
            "physical_acquisition_id": [f"physical-{index}" for index in range(1, 5)],
            "acquisition_local_date": [
                "2024-07-01",
                "2024-07-31",
                "2024-08-19",
                "2024-08-29",
            ],
            "lag_days": [60, 30, 11, 1],
        }
    )


def test_composite_requires_three_physical_acquisitions_and_keeps_audit_separate() -> None:
    result = build_previous_60_day_composites(
        _acquisition_rows(),
        _membership(),
        target_dates=[date(2024, 8, 30)],
        tract_geoids=["a", "b"],
        minimum_acquisition_coverage=0.8,
        minimum_acquisitions=3,
        final_test_year=2025,
        unlock_final_test=False,
    )

    assert list(result.features.columns) == ["target_date", "tract_geoid", *INDEX_COLUMNS]
    assert result.features.loc[result.features.tract_geoid == "a", INDEX_COLUMNS[0]].item() == 2.5
    assert result.audit.loc[
        result.audit.tract_geoid == "a", "qualifying_acquisition_count"
    ].item() == 4
    assert result.audit.loc[
        result.audit.tract_geoid == "b", "qualifying_acquisition_count"
    ].item() == 2
    assert result.features.loc[
        result.features.tract_geoid == "b", list(INDEX_COLUMNS)
    ].isna().all(axis=None)
    assert len(result.lineage) == 8
    assert (pd.to_datetime(result.lineage.source_end_date) < pd.to_datetime(
        result.lineage.target_date
    )).all()
    assert (
        result.lineage["source_age_days_audit_only"] == result.lineage["lag_days"]
    ).all()


@pytest.mark.parametrize(
    ("target_date", "acquired", "lag"),
    [
        ("2024-08-30", "2024-08-30", 0),
        ("2024-08-30", "2024-08-31", -1),
        ("2024-08-30", "2024-06-30", 61),
        ("2024-08-30", "2024-08-29", 2),
    ],
)
def test_composite_fails_closed_on_same_day_future_stale_or_false_lag(
    target_date: str, acquired: str, lag: int
) -> None:
    membership = _membership().iloc[[0]].copy()
    membership.loc[:, "target_date"] = target_date
    membership.loc[:, "acquisition_local_date"] = acquired
    membership.loc[:, "lag_days"] = lag
    acquisitions = _acquisition_rows().loc[
        lambda frame: frame.physical_acquisition_id == "physical-1"
    ].copy()
    acquisitions.loc[:, "acquisition_local_date"] = acquired

    with pytest.raises(ValueError, match="d-60:d-1"):
        build_previous_60_day_composites(
            acquisitions,
            membership,
            target_dates=[target_date],
            tract_geoids=["a", "b"],
            minimum_acquisition_coverage=0.8,
            minimum_acquisitions=3,
            final_test_year=2025,
            unlock_final_test=False,
        )


def test_composite_enforces_2025_lock_before_emitting_rows() -> None:
    with pytest.raises(PermissionError, match="2025"):
        build_previous_60_day_composites(
            _acquisition_rows(),
            _membership().assign(target_date="2025-08-30"),
            target_dates=["2025-08-30"],
            tract_geoids=["a", "b"],
            minimum_acquisition_coverage=0.8,
            minimum_acquisitions=3,
            final_test_year=2025,
            unlock_final_test=False,
        )


def test_composite_rejects_an_incomplete_acquisition_tract_universe() -> None:
    incomplete = _acquisition_rows().loc[
        lambda frame: ~(
            (frame.physical_acquisition_id == "physical-1") & (frame.tract_geoid == "b")
        )
    ]
    with pytest.raises(ValueError, match="complete tract universe"):
        build_previous_60_day_composites(
            incomplete,
            _membership(),
            target_dates=["2024-08-30"],
            tract_geoids=["a", "b"],
            minimum_acquisition_coverage=0.8,
            minimum_acquisitions=3,
            final_test_year=2025,
            unlock_final_test=False,
        )


def test_versioned_stage_config_locks_albedo_and_primary_qa_contract() -> None:
    stage = load_sentinel_stage_config("configs/sentinel_features.toml")

    assert stage.minimum_coverage == 0.8
    assert stage.minimum_acquisitions == 3
    assert stage.albedo_coefficients == ALBEDO
    assert stage.raw["qa"]["global_scene_cloud_cover_filter"] is False
    assert stage.raw["window"]["start_days_before_target"] == 60
    assert stage.raw["window"]["end_days_before_target"] == 1
    assert stage.raw["grid"]["reflectance_resampling"] == "average"
    assert stage.raw["albedo_proxy"]["intercept"] == 0.0


def test_native_sentinel_grid_phase_is_validated_before_dn_averaging() -> None:
    grid = _grid(resolution=20.0, width=2, height=2)

    def validate(transform_value, *, categorical: bool) -> None:
        with MemoryFile() as memory:
            with memory.open(
                driver="GTiff",
                width=2,
                height=2,
                count=1,
                dtype="uint16",
                crs="EPSG:32611",
                transform=transform_value,
                nodata=0,
            ) as dataset:
                _validate_native_asset_grid(
                    dataset, grid=grid, categorical=categorical
                )

    validate(from_origin(0, 40, 20, 20), categorical=True)
    validate(from_origin(0, 40, 10, 10), categorical=False)
    with pytest.raises(ValueError, match="phase-aligned"):
        validate(from_origin(5, 40, 20, 20), categorical=False)
    with pytest.raises(ValueError, match="unsupported native resolution"):
        validate(from_origin(0, 40, 30, 30), categorical=False)


def test_area_average_propagates_any_native_saturation_to_missing() -> None:
    target = _grid(resolution=20.0, width=1, height=1)
    with MemoryFile() as memory:
        with memory.open(
            driver="GTiff",
            width=2,
            height=2,
            count=1,
            dtype="uint16",
            crs="EPSG:32611",
            transform=from_origin(0, 20, 10, 10),
            nodata=0,
        ) as dataset:
            dataset.write(
                np.array([[1000, 1000], [1000, 65535]], dtype=np.uint16), 1
            )
        averaged = _read_asset_to_optical_grid(
            memory.name,
            grid=target,
            categorical=False,
            saturated_dn=65535,
        )

    assert averaged.shape == (1, 1)
    assert np.isnan(averaged.item())


def test_real_frozen_inventory_passes_byte_cohort_and_temporal_locks() -> None:
    inventory = _load_frozen_sentinel_inventory(
        Path("manifests/sentinel_inventory"),
        research=load_config("configs/research.toml"),
    )

    assert len(inventory.acquisitions) == 226
    assert len(inventory.items) == 449
    assert len(inventory.membership) == 1045
    assert inventory.membership["lag_days"].between(1, 60).all()
    assert pd.to_datetime(inventory.membership["target_date"]).dt.year.max() == 2024
    assert inventory.items["cloud_cover_percent_audit_only"].max() > 99


def test_acquisition_cache_revalidates_raw_metadata_hash(tmp_path) -> None:
    directory = tmp_path / "cache"
    metadata_path = tmp_path / "raw" / "metadata.xml"
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_bytes(b"<xml>original</xml>")
    output = pd.DataFrame({"tract_geoid": ["a"], "value": [1.0]})
    output_path = directory / "acquisition_tract.parquet"
    atomic_parquet(output, output_path)
    lock = {"pipeline": "locked"}
    atomic_json(
        {
            "state": "complete",
            "cache_lock": lock,
            "tract_count": 1,
            "product_metadata": [
                {
                    "product_metadata_path": metadata_path.as_posix(),
                    "product_metadata_sha256": sha256_file(metadata_path),
                }
            ],
            "output_file": parquet_file_record(output_path, output),
        },
        directory / "summary.json",
    )

    assert _acquisition_cache_is_current(directory, expected_lock=lock)
    metadata_path.write_bytes(b"<xml>modified</xml>")
    assert not _acquisition_cache_is_current(directory, expected_lock=lock)
