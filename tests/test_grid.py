import geopandas as gpd
from shapely.geometry import box

from la_heat.grid import build_fixed_grid


def test_fixed_grid_snaps_outward_and_hashes_deterministically() -> None:
    boundary = gpd.GeoDataFrame(
        {"name": ["test"]}, geometry=[box(1, 2, 61, 92)], crs="EPSG:32611"
    )
    first = build_fixed_grid(
        boundary,
        target_crs="EPSG:32611",
        resolution_m=30.0,
        anchor_x_m=15.0,
        anchor_y_m=15.0,
    )
    second = build_fixed_grid(
        boundary,
        target_crs="EPSG:32611",
        resolution_m=30.0,
        anchor_x_m=15.0,
        anchor_y_m=15.0,
    )
    assert (first.left, first.bottom, first.right, first.top) == (-15, -15, 75, 105)
    assert first.shape == (4, 3)
    assert first.sha256 == second.sha256
    assert first.left % 30 == 15
    assert first.top % 30 == 15
