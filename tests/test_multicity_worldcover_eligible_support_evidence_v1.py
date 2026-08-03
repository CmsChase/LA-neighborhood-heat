from __future__ import annotations

import ast
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box, mapping

from la_heat.grid import build_fixed_grid
from la_heat.multicity import missing_support_calibration_evidence_v1 as evidence
from la_heat.multicity import worldcover_eligible_support_evidence_v1 as worldcover

ROOT = Path(__file__).resolve().parents[1]


def test_canonical_hash_contract_is_endian_and_bitorder_explicit() -> None:
    zones = np.array([[1, 2], [3, 4]], dtype=np.int64)
    mask = np.array([[True, False], [False, True]])

    assert worldcover._array_sha(zones, dtype="<i4") == worldcover._array_sha(
        zones.astype(">i4"), dtype="<i4"
    )
    assert worldcover._mask_sha(mask) == worldcover._mask_sha(mask.astype(np.uint8))
    assert worldcover._mask_sha(mask) != worldcover._mask_sha(mask[:, ::-1])


def test_per_tract_support_conserves_zone_valid_water_and_eligible_cells() -> None:
    zones = np.array([[1, 1], [2, 2]], dtype=np.int32)
    classes = np.array([[10, 80], [20, 0]], dtype=np.uint8)
    eligible = (zones > 0) & (classes != 0) & (classes != 80)
    boundary = gpd.GeoDataFrame(
        {"name": ["city"]}, geometry=[box(0, 0, 60, 60)], crs="EPSG:32611"
    )
    grid = build_fixed_grid(
        boundary,
        target_crs="EPSG:32611",
        resolution_m=30,
        anchor_x_m=0,
        anchor_y_m=0,
    )

    table, identities = worldcover._support_table(
        city_id="test_city",
        geoids=("00000000001", "00000000002"),
        zones=zones,
        classes=classes,
        eligible=eligible,
        grid=grid,
    )

    assert table["eligible_cell_count"].tolist() == [1, 1]
    assert table["worldcover_permanent_water_cell_count"].tolist() == [1, 0]
    assert table["worldcover_nodata_cell_count"].tolist() == [0, 1]
    assert table["city_support_identity_sha256"].nunique() == 1
    assert len(identities["eligible_mask_sha256"]) == 64


def test_worldcover_item_requires_v100_tile_asset_and_positive_intersection() -> None:
    boundary = gpd.GeoDataFrame(
        {"name": ["city"]}, geometry=[box(-118.5, 33.5, -118.0, 34.0)], crs="EPSG:4326"
    )
    feature = {
        "id": "ESA_WorldCover_10m_2020_v100_N33W120",
        "collection": "esa-worldcover",
        "geometry": mapping(box(-120, 33, -117, 36)),
        "assets": {
            "map": {
                "href": (
                    "https://esa-worldcover.s3.eu-central-1.amazonaws.com/"
                    "v100/2020/map/"
                    "ESA_WorldCover_10m_2020_v100_N33W120_Map.tif"
                )
            },
            "rendered_preview": {"href": "https://example.test/preview.png"},
        },
    }
    selected = worldcover._validate_items(
        None, features=[feature], boundary=boundary  # type: ignore[arg-type]
    )
    assert selected[0]["tile_id"] == "N33W120"
    assert selected[0]["stac_asset_keys"] == ["map", "rendered_preview"]


def test_frozen_worldcover_blob_prefix_accepts_saved_official_stac_href() -> None:
    config = evidence.read_evidence_config(ROOT / evidence.CONFIG_PATH)
    client = worldcover._BoundedClient(object(), config)
    client._authorize(
        "GET",
        (
            "https://ai4edataeuwest.blob.core.windows.net/esa-worldcover/"
            "v100/2020/map/ESA_WorldCover_10m_2020_v100_N33W120_Map.tif"
        ),
        asset=True,
    )
    assert client.request_count == 1


@pytest.mark.parametrize(
    "url",
    [
        (
            "https://esa-worldcover.s3.eu-central-1.amazonaws.com/"
            "esa-worldcover/v100/2020/map/"
            "ESA_WorldCover_10m_2020_v100_N33W120_Map.tif"
        ),
        (
            "https://ai4edataeuwest.blob.core.windows.net/"
            "v100/2020/map/ESA_WorldCover_10m_2020_v100_N33W120_Map.tif"
        ),
    ],
)
def test_worldcover_asset_prefix_cannot_be_reused_across_hosts(url: str) -> None:
    config = evidence.read_evidence_config(ROOT / evidence.CONFIG_PATH)
    client = worldcover._BoundedClient(object(), config)

    with pytest.raises(
        evidence.MissingSupportCalibrationEvidenceV1Error,
        match="path is outside the allowlist",
    ):
        client._authorize("GET", url, asset=True)
    assert client.request_count == 0


def _write_tile(path: Path, *, left: float, value: int) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=1,
        crs="EPSG:32611",
        transform=from_origin(left, 20, 10, 10),
        dtype="uint8",
        nodata=0,
    ) as destination:
        destination.write(np.full((2, 2), value, dtype=np.uint8), 1)


def test_native_mosaic_is_invariant_to_nonoverlapping_tile_order(tmp_path: Path) -> None:
    left = tmp_path / "left.tif"
    right = tmp_path / "right.tif"
    _write_tile(left, left=0, value=10)
    _write_tile(right, left=20, value=20)
    boundary = gpd.GeoDataFrame(
        {"name": ["city"]}, geometry=[box(0, 0, 40, 20)], crs="EPSG:32611"
    )
    grid = build_fixed_grid(
        boundary,
        target_crs="EPSG:32611",
        resolution_m=10,
        anchor_x_m=0,
        anchor_y_m=0,
    )
    forward = worldcover._mosaic_to_grid(
        [left, right], boundary=boundary, grid=grid
    )
    reverse = worldcover._mosaic_to_grid(
        [right, left], boundary=boundary, grid=grid
    )
    assert np.array_equal(forward, reverse)
    assert forward.tolist() == [[10, 10, 20, 20], [10, 10, 20, 20]]


def test_existing_worldcover_raster_must_keep_exact_grid(tmp_path: Path) -> None:
    boundary = gpd.GeoDataFrame(
        {"name": ["city"]}, geometry=[box(0, 0, 60, 60)], crs="EPSG:32611"
    )
    grid = build_fixed_grid(
        boundary,
        target_crs="EPSG:32611",
        resolution_m=30,
        anchor_x_m=0,
        anchor_y_m=0,
    )
    path = tmp_path / "existing.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=grid.width,
        height=grid.height,
        count=1,
        crs=grid.crs,
        transform=from_origin(30, 60, 30, 30),
        dtype="uint8",
        nodata=0,
    ) as destination:
        destination.write(np.ones(grid.shape, dtype=np.uint8), 1)
    with pytest.raises(
        worldcover.MissingSupportCalibrationEvidenceV1Error,
        match="grid differs",
    ):
        worldcover._write_raster_no_clobber(
            path,
            np.ones(grid.shape, dtype=np.uint8),
            grid=grid,
            dtype="uint8",
            nodata=0,
        )


def test_worldcover_program_imports_no_target_model_or_final_reader() -> None:
    source = (
        ROOT
        / "src/la_heat/multicity/worldcover_eligible_support_evidence_v1.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(
        name.startswith(
            (
                "la_heat.final_",
                "la_heat.model",
                "la_heat.target",
                "la_heat.feature_ablation",
            )
        )
        for name in imported_modules
    )
