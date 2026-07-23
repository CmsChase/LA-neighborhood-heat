import geopandas as gpd
from shapely.geometry import box

from la_heat.boundaries import assign_spatial_blocks, load_city_tracts


def test_tract_selection_uses_only_city_area_fraction(tmp_path) -> None:
    tracts = gpd.GeoDataFrame(
        {
            "STATEFP": [b"06", b"06", b"06"],
            "COUNTYFP": [b"037", b"037", b"059"],
            "GEOID": [b"06037000001", b"06037000002", b"06059000001"],
        },
        geometry=[box(0, 0, 2, 2), box(1.9, 1.9, 3, 3), box(0, 0, 1, 1)],
        crs="EPSG:3857",
    )
    path = tmp_path / "tracts.parquet"
    tracts.to_parquet(path)
    city = gpd.GeoDataFrame(
        {"city": ["Los Angeles"]}, geometry=[box(0, 0, 2, 2)], crs="EPSG:3857"
    )
    selected = load_city_tracts(
        path,
        city,
        analysis_crs="EPSG:3857",
        state_fips="06",
        county_fips="037",
        minimum_city_area_fraction=0.50,
    )
    assert selected["GEOID"].tolist() == ["06037000001"]
    assert selected.iloc[0]["city_area_fraction"] == 1.0
    assert len(selected.iloc[0]["geometry_sha256"]) == 64


def test_spatial_blocks_are_deterministic_and_target_independent() -> None:
    tracts = gpd.GeoDataFrame(
        {"GEOID": ["a", "b"]},
        geometry=[box(100, 100, 200, 200), box(5100, 100, 5200, 200)],
        crs="EPSG:3310",
    )
    blocked = assign_spatial_blocks(tracts, block_size_km=5.0)
    assert blocked["spatial_block"].tolist() == ["x+0000_y+0000", "x+0001_y+0000"]
    assert blocked["latitude_quartile"].notna().all()
    assert blocked["longitude_quartile"].notna().all()


def test_special_use_tracts_can_be_excluded_by_predeclared_rule(tmp_path) -> None:
    tracts = gpd.GeoDataFrame(
        {
            "STATEFP": ["06", "06"],
            "COUNTYFP": ["037", "037"],
            "GEOID": ["06037000100", "06037980000"],
            "TRACTCE": ["000100", "980000"],
        },
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)],
        crs="EPSG:3857",
    )
    path = tmp_path / "tracts.parquet"
    tracts.to_parquet(path)
    city = gpd.GeoDataFrame(
        {"city": ["Los Angeles"]}, geometry=[box(0, 0, 2, 1)], crs="EPSG:3857"
    )
    selected = load_city_tracts(
        path,
        city,
        analysis_crs="EPSG:3857",
        state_fips="06",
        county_fips="037",
        minimum_city_area_fraction=0.50,
        exclude_special_use_tracts=True,
    )
    assert selected["GEOID"].tolist() == ["06037000100", "06037980000"]
    assert selected["primary_included"].tolist() == [True, False]
    assert selected.loc[1, "primary_exclusion_reason"] == "census_special_use_98xxxx"
