from pathlib import Path

import geopandas as gpd
import pytest
import shapely

from la_heat.multicity.config import load_multicity_plan
from la_heat.multicity.geography import (
    LayerAcquisition,
    LayerCandidate,
    LayerUnavailableError,
    MulticityGeographyError,
    _acquire_with_fallback,
    select_city_tracts,
    stage_city_geography,
    standardize_place,
    standardize_tracts,
)

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "configs" / "multicity" / "experiment.toml"


def _phoenix():
    return next(
        city
        for city in load_multicity_plan(CONFIG).cities
        if city.id == "phoenix_az"
    )


def _tracts() -> gpd.GeoDataFrame:
    rows = [
        ("04013000100", "000100", shapely.box(0, 0, 4, 4)),
        ("04013000200", "000200", shapely.box(8, 0, 12, 4)),
        ("04013980000", "980000", shapely.box(4, 4, 6, 6)),
        ("04013000300", "000300", shapely.box(9, 5, 14, 7)),
        ("04013000400", "000400", shapely.box(20, 20, 22, 22)),
    ]
    return gpd.GeoDataFrame(
        {
            "OBJECTID": list(range(1, 6)),
            "GEOID": [row[0] for row in rows],
            "STATE": ["04"] * 5,
            "COUNTY": ["013"] * 5,
            "TRACT": [row[1] for row in rows],
            "BASENAME": [row[1] for row in rows],
            "NAME": [f"Census Tract {row[1]}" for row in rows],
            "FUNCSTAT": ["S"] * 5,
            "AREALAND": [16, 16, 4, 10, 4],
        },
        geometry=[row[2] for row in rows],
        crs="EPSG:5070",
    )


def test_city_tract_selection_keeps_exact_half_and_excludes_special_use() -> None:
    boundary = gpd.GeoDataFrame(
        {"city_id": ["phoenix_az"]},
        geometry=[shapely.box(0, 0, 10, 10)],
        crs="EPSG:5070",
    )

    candidates, primary = select_city_tracts(
        boundary,
        _tracts(),
        city_id="phoenix_az",
        analysis_crs="EPSG:5070",
        minimum_place_area_fraction=0.5,
        exclude_special_use_tracts=True,
    )

    assert primary["tract_geoid"].tolist() == ["04013000100", "04013000200"]
    exact_half = candidates.set_index("tract_geoid").loc["04013000200"]
    assert exact_half["place_area_fraction"] == pytest.approx(0.5)
    special = candidates.set_index("tract_geoid").loc["04013980000"]
    assert special["primary_exclusion_reason"] == "census_special_use_98xxxx"
    below = candidates.set_index("tract_geoid").loc["04013000300"]
    assert below["primary_exclusion_reason"] == "below_place_area_threshold"
    outside = candidates.set_index("tract_geoid").loc["04013000400"]
    assert outside["primary_exclusion_reason"] == "no_place_overlap"
    assert primary.geometry.area.tolist() == pytest.approx([16.0, 8.0])


def test_standardizers_enforce_configured_census_identity() -> None:
    city = _phoenix()
    nested_shells = shapely.MultiPolygon(
        [
            shapely.box(-112.2, 33.3, -111.8, 33.7),
            shapely.box(-112.1, 33.4, -112.0, 33.5),
        ]
    )
    assert not nested_shells.is_valid
    place = gpd.GeoDataFrame(
        {
            "OBJECTID": [1],
            "GEOID": ["0455000"],
            "STATE": ["04"],
            "PLACE": ["55000"],
            "BASENAME": ["Phoenix"],
            "NAME": ["Phoenix city"],
            "FUNCSTAT": ["A"],
            "AREALAND": ["1341601620"],
        },
        geometry=[nested_shells],
        crs="EPSG:4326",
    )
    standardized_place = standardize_place(place, city)
    standardized_tracts = standardize_tracts(_tracts().to_crs("EPSG:4326"), city)

    assert standardized_place["census_place_geoid"].tolist() == ["0455000"]
    assert standardized_place["source_land_area_m2"].tolist() == [1341601620]
    assert standardized_place.geometry.is_valid.all()
    assert standardized_place.geometry.iloc[0].equals(shapely.make_valid(nested_shells))
    assert standardized_tracts["GEOID"].tolist()[0] == "04013000100"

    broken = _tracts().to_crs("EPSG:4326")
    broken.loc[0, "GEOID"] = "04013009999"
    with pytest.raises(
        MulticityGeographyError,
        match="internally inconsistent",
    ):
        standardize_tracts(broken, city)


def test_source_fallback_is_explicit_and_audited() -> None:
    primary = LayerCandidate(
        "primary",
        "https://primary.example/0",
        "Primary",
        "authoritative_primary",
    )
    mirror = LayerCandidate(
        "mirror",
        "https://mirror.example/0",
        "Mirror",
        "pilot_mirror_not_protocol_frozen",
    )

    def downloader(candidate: LayerCandidate) -> LayerAcquisition:
        if candidate == primary:
            raise LayerUnavailableError("offline")
        return LayerAcquisition(
            candidate,
            gpd.GeoDataFrame(
                {"GEOID": ["1"]},
                geometry=[shapely.box(0, 0, 1, 1)],
                crs="EPSG:4326",
            ),
            {"maxRecordCount": 2000},
            {},
        )

    acquired = _acquire_with_fallback(
        (primary, mirror),
        unavailable_origins=set(),
        downloader=downloader,
    )

    assert acquired.candidate == mirror
    assert [attempt["status"] for attempt in acquired.attempts] == [
        "unavailable",
        "selected",
    ]


def test_draft_rejects_non_phoenix_metadata_staging_before_network() -> None:
    with pytest.raises(MulticityGeographyError, match="does not authorize"):
        stage_city_geography(CONFIG, "houston_tx")
