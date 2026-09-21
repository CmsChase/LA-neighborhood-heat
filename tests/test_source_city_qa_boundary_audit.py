"""Focused pixel-scale boundary comparison checks without network access."""

from zipfile import ZipFile

import geopandas as gpd
from shapely.geometry import box

from experiments.source_city_qa_boundary_audit import (
    _tiger_frame,
    _tiger_to_existing_fields,
    compare_geometry,
)


def frames(*, shift_m=0.0, geoid="08041000100"):
    polygon = box(0 + shift_m, 0, 300 + shift_m, 300)
    place = gpd.GeoDataFrame(geometry=[polygon], crs="EPSG:32613")
    tracts = gpd.GeoDataFrame({"tract_geoid": [geoid]}, geometry=[polygon], crs="EPSG:32613")
    return place, None, tracts, {}


def test_identical_geometry_passes_exact_zone_gate():
    result = compare_geometry(frames(), frames(), crs="EPSG:32613")
    assert result["passes_pixel_scale_gate"] is True
    assert result["zone_disagreement_30m_cells"] == 0
    assert result["place_hausdorff_m"] == 0


def test_half_pixel_displacement_or_changed_tract_identity_fails():
    shifted = compare_geometry(frames(shift_m=16), frames(), crs="EPSG:32613")
    assert shifted["passes_pixel_scale_gate"] is False
    assert shifted["place_hausdorff_m"] > 15

    changed_id = compare_geometry(frames(geoid="08041000101"), frames(), crs="EPSG:32613")
    assert changed_id["passes_pixel_scale_gate"] is False
    assert changed_id["official_only_geoids"] == ["08041000101"]


def test_original_tiger_field_mapping_preserves_geoid_and_land_area():
    place = gpd.GeoDataFrame(
        {"GEOID": ["0816000"], "STATEFP": ["08"], "PLACEFP": ["16000"],
         "NAME": ["Colorado Springs"], "FUNCSTAT": ["A"], "ALAND": [123]},
        geometry=[box(0, 0, 30, 30)], crs="EPSG:32613",
    )
    mapped = _tiger_to_existing_fields(place, role="place")
    assert mapped.loc[0, "STATE"] == "08"
    assert mapped.loc[0, "PLACE"] == "16000"
    assert mapped.loc[0, "AREALAND"] == 123
    assert mapped.loc[0, "BASENAME"] == "Colorado Springs"


def test_offline_original_zip_reader(tmp_path):
    stem = "tl_2020_08_place"
    folder = tmp_path / "shape"
    folder.mkdir()
    gpd.GeoDataFrame(
        {"GEOID": ["0816000"]}, geometry=[box(0, 0, 1, 1)], crs="EPSG:4326"
    ).to_file(folder / f"{stem}.shp")
    archive_path = tmp_path / f"{stem}.zip"
    with ZipFile(archive_path, "w") as archive:
        for path in folder.iterdir():
            archive.write(path, arcname=path.name)
    result = _tiger_frame(archive_path, role="place")
    assert result["GEOID"].tolist() == ["0816000"]
