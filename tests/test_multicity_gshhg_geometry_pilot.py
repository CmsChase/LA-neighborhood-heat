from __future__ import annotations

import hashlib
import stat
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import shapely
from pyproj import Geod
from shapely.geometry import LineString, Point, Polygon

from la_heat.multicity.gshhg_geometry_pilot import (
    GshhgGeometryPilotError,
    LakeSeed,
    ZipSafetyLimits,
    _is_dateline_seam,
    _nearest_source_evidence,
    _normalized_wkb_sha256,
    _read_exact_configs,
    _recorded_output_path,
    _thread_and_query_chunk_audit,
    audit_l1_dateline_segments,
    expanding_radius_distances,
    geodesic_reference_distances,
    gshhg_l1_exterior_linework,
    nearest_projected_bruteforce,
    nearest_projected_strtree,
    repair_predeclared_l1_geometry,
    require_projected_geodesic_parity,
    select_connected_great_lakes,
    select_five_great_lakes,
    validate_pinned_zip,
)

ROOT = Path(__file__).parents[1]


def _write_zip(
    path: Path,
    members: list[tuple[zipfile.ZipInfo | str, bytes]],
    *,
    compression: int = zipfile.ZIP_STORED,
) -> tuple[str, int]:
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        for member, content in members:
            archive.writestr(member, content)
    content = path.read_bytes()
    return hashlib.sha256(content).hexdigest(), len(content)


def _validate_fixture_zip(
    path: Path,
    digest: str,
    size: int,
    *,
    required: tuple[str, ...] = (),
    allowed: tuple[str, ...] | None = None,
    limits: ZipSafetyLimits | None = None,
):
    return validate_pinned_zip(
        path,
        expected_sha256=digest,
        expected_bytes=size,
        required_members=required,
        allowed_members=allowed,
        limits=limits,
    )


def test_pinned_zip_authenticates_without_extracting(tmp_path: Path) -> None:
    path = tmp_path / "source.zip"
    digest, size = _write_zip(
        path,
        [
            ("GSHHS_shp/f/GSHHS_f_L1.shp", b"shape"),
            ("GSHHS_shp/f/GSHHS_f_L1.dbf", b"table"),
            ("LICENSE.TXT", b"license"),
        ],
    )

    result = _validate_fixture_zip(
        path,
        digest,
        size,
        required=("GSHHS_shp/f/GSHHS_f_L1.shp", "LICENSE.TXT"),
        allowed=(
            "GSHHS_shp/f/GSHHS_f_L1.shp",
            "GSHHS_shp/f/GSHHS_f_L1.dbf",
            "LICENSE.TXT",
        ),
    )

    assert result["archive_testzip_passed"] is True
    assert result["extracted"] is False
    assert result["member_count"] == 3
    assert len(result["member_inventory_sha256"]) == 64
    assert not (tmp_path / "GSHHS_shp").exists()


@pytest.mark.parametrize(
    "names, match",
    [
        (["../escape.shp"], "Unsafe ZIP member"),
        (["safe/Layer.shp", "safe/layer.shp"], "case-folding"),
        (["safe//layer.shp"], "Unsafe ZIP member"),
        (["C:/layer.shp"], "Unsafe ZIP member"),
    ],
)
def test_pinned_zip_rejects_ambiguous_or_traversing_names(
    tmp_path: Path,
    names: list[str],
    match: str,
) -> None:
    path = tmp_path / "unsafe.zip"
    digest, size = _write_zip(path, [(name, b"x") for name in names])

    with pytest.raises(GshhgGeometryPilotError, match=match):
        _validate_fixture_zip(path, digest, size)


def test_pinned_zip_rejects_symbolic_links_and_zip_bombs(tmp_path: Path) -> None:
    symlink_path = tmp_path / "symlink.zip"
    symlink = zipfile.ZipInfo("link")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    digest, size = _write_zip(symlink_path, [(symlink, b"target")])
    with pytest.raises(GshhgGeometryPilotError, match="symbolic link"):
        _validate_fixture_zip(symlink_path, digest, size)

    bomb_path = tmp_path / "bomb.zip"
    digest, size = _write_zip(
        bomb_path,
        [("large.bin", b"0" * 100_000)],
        compression=zipfile.ZIP_DEFLATED,
    )
    with pytest.raises(GshhgGeometryPilotError, match="compression-ratio"):
        _validate_fixture_zip(
            bomb_path,
            digest,
            size,
            limits=ZipSafetyLimits(max_compression_ratio=2.0),
        )


def test_l1_uses_only_exterior_and_reassembles_dateline_source_id() -> None:
    polygon_with_hole = Polygon(
        [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)],
        holes=[[(4, 4), (6, 4), (6, 6), (4, 6), (4, 4)]],
    )
    east_component = Polygon([(179, 10), (180, 10), (180, 20), (179, 20), (179, 10)])
    west_component = Polygon([(-180, 10), (-179, 10), (-179, 20), (-180, 20), (-180, 10)])
    frame = gpd.GeoDataFrame(
        {"id": ["1", "7-W", "7-E"], "level": [1, 1, 1]},
        geometry=[polygon_with_hole, east_component, west_component],
        crs="EPSG:4326",
    )

    lines = gshhg_l1_exterior_linework(frame, max_vertices=32)

    first = lines.loc[lines["source_id"].eq("1")]
    assert np.isclose(sum(line.length for line in first.geometry), 40.0)
    split = lines.loc[lines["source_id"].eq("7")]
    assert set(split["component_id"]) == {"7-E", "7-W"}
    for line in split.geometry:
        coordinates = np.asarray(line.coords)
        assert not np.all(np.isclose(np.abs(coordinates[:, 0]), 180.0))
        assert np.max(np.abs(np.diff(coordinates[:, 0]))) <= 180.0


def test_dateline_rule_removes_only_same_sign_meridian_seams() -> None:
    assert _is_dateline_seam((180.0, 0.0), (180.0, 1.0), tolerance=1e-9)
    assert _is_dateline_seam((-180.0, 0.0), (-180.0, 1.0), tolerance=1e-9)
    assert not _is_dateline_seam((180.0, 0.0), (-180.0, 1.0), tolerance=1e-9)

    exact_jump = Polygon([(-90.0, 0.0), (90.0, 0.0), (89.0, 1.0), (-89.0, 1.0), (-90.0, 0.0)])
    frame = gpd.GeoDataFrame(
        {"id": ["1"], "level": [1]},
        geometry=[exact_jump],
        crs="EPSG:4326",
    )
    with pytest.raises(GshhgGeometryPilotError, match="antimeridian"):
        audit_l1_dateline_segments(frame)
    with pytest.raises(GshhgGeometryPilotError, match="crosses the world"):
        gshhg_l1_exterior_linework(frame)


def test_dateline_split_points_have_equivalent_nonzero_distance() -> None:
    east_component = Polygon([(179, 10), (180, 10), (180, 20), (179, 20), (179, 10)])
    west_component = Polygon([(-180, 10), (-179, 10), (-179, 20), (-180, 20), (-180, 10)])
    frame = gpd.GeoDataFrame(
        {"id": ["9-W", "9-E"], "level": [1, 1]},
        geometry=[east_component, west_component],
        crs="EPSG:4326",
    )
    lines = gshhg_l1_exterior_linework(frame)
    points = gpd.GeoSeries(
        [Point(179.9, 15), Point(-179.9, 15)],
        crs="EPSG:4326",
    )
    local_crs = "+proj=aeqd +lat_0=15 +lon_0=180 +datum=WGS84 +units=m +no_defs"

    distances = nearest_projected_strtree(points, lines.geometry, local_crs)

    assert np.all(distances > 90_000)
    assert np.isclose(distances[0], distances[1], atol=1e-6, rtol=0.0)


def _lake_fixture() -> tuple[gpd.GeoDataFrame, list[LakeSeed]]:
    polygons: list[Polygon] = []
    ids: list[str] = []
    areas: list[float] = []
    seeds: list[LakeSeed] = []
    for index in range(5):
        left = float(index * 10)
        polygons.append(
            Polygon(
                [
                    (left, 0),
                    (left + 4, 0),
                    (left + 4, 4),
                    (left, 4),
                    (left, 0),
                ]
            )
        )
        ids.append(str(index + 1))
        areas.append(100.0 + index)
        seeds.append(LakeSeed(f"lake_{index + 1}", left + 2, 2))
    polygons.append(Polygon([(1, 1), (3, 1), (3, 3), (1, 3), (1, 1)]))
    ids.append("99")
    areas.append(-5.0)
    return (
        gpd.GeoDataFrame(
            {"id": ids, "level": [2] * 6, "area": areas},
            geometry=polygons,
            crs="EPSG:4326",
        ),
        seeds,
    )


def test_five_lakes_use_distinct_positive_area_polygons() -> None:
    frame, seeds = _lake_fixture()

    selected = select_five_great_lakes(frame, seeds)

    assert selected["lake_name"].tolist() == [seed.name for seed in seeds]
    assert selected["source_id"].nunique() == 5
    assert (selected["area"] > 0).all()
    assert "99" not in set(selected["source_id"])


def test_five_lake_selection_fails_on_ambiguous_or_duplicate_identity() -> None:
    frame, seeds = _lake_fixture()
    boundary_seeds = [*seeds]
    boundary_seeds[0] = LakeSeed("lake_1", 0, 2)
    with pytest.raises(GshhgGeometryPilotError, match="matched 0"):
        select_five_great_lakes(frame, boundary_seeds)

    frame.loc[1, "id"] = "1"
    with pytest.raises(GshhgGeometryPilotError, match="five distinct"):
        select_five_great_lakes(frame, seeds)


def test_projected_strtree_matches_bruteforce_in_float64() -> None:
    points = gpd.GeoSeries(
        [Point(500, 500), Point(8500, 5000)],
        crs="EPSG:3857",
    )
    lines = gpd.GeoSeries(
        [
            LineString([(0, -1000), (0, 10_000)]),
            LineString([(10_000, -1000), (10_000, 10_000)]),
        ],
        crs="EPSG:3857",
    )

    indexed = nearest_projected_strtree(points, lines, "EPSG:3857")
    brute = nearest_projected_bruteforce(points, lines, "EPSG:3857")

    assert indexed.dtype == np.float64
    assert np.array_equal(indexed, brute)
    assert np.array_equal(indexed, np.array([500.0, 1500.0]))


def test_radius_ladder_is_invariant_and_strict_at_boundary() -> None:
    points = gpd.GeoSeries([Point(0, 0)], crs="EPSG:3857")
    lines = gpd.GeoSeries(
        [LineString([(1000, -100), (1000, 100)])],
        crs="EPSG:3857",
    )

    expanded = expanding_radius_distances(
        points,
        lines,
        "EPSG:3857",
        radii_km=[1, 2],
    )
    direct = expanding_radius_distances(
        points,
        lines,
        "EPSG:3857",
        radii_km=[2],
        method="bruteforce",
    )

    assert expanded.accepted_radius_km == 2.0
    assert direct.accepted_radius_km == 2.0
    assert np.array_equal(expanded.distances_m, direct.distances_m)
    with pytest.raises(GshhgGeometryPilotError, match="exhausted"):
        expanding_radius_distances(
            points,
            lines,
            "EPSG:3857",
            radii_km=[1],
        )


def test_projected_distance_passes_independent_geodesic_reference() -> None:
    points = gpd.GeoSeries([Point(-117.9, 34.0)], crs="EPSG:4326")
    lines = gpd.GeoSeries(
        [LineString([(-118.0, 33.5), (-118.0, 34.5)])],
        crs="EPSG:4326",
    )

    projected = nearest_projected_strtree(points, lines, "EPSG:32611")
    geodesic = geodesic_reference_distances(points, lines, max_step_m=100.0)
    audit = require_projected_geodesic_parity(
        projected,
        geodesic,
        absolute_tolerance_m=50.0,
        relative_tolerance=0.001,
    )

    assert audit["maximum_absolute_difference_m"] < 50.0
    with pytest.raises(GshhgGeometryPilotError, match="predeclared"):
        require_projected_geodesic_parity(
            projected + 1000.0,
            geodesic,
            absolute_tolerance_m=50.0,
            relative_tolerance=0.001,
        )


def test_geodesic_reference_is_frozen_point_to_densified_vertex_minimum() -> None:
    points = gpd.GeoSeries([Point(1.0, 1.0)], crs="EPSG:4326")
    lines = gpd.GeoSeries(
        [LineString([(0.0, 0.0), (2.0, 0.0)])],
        crs="EPSG:4326",
    )

    observed = geodesic_reference_distances(
        points,
        lines,
        max_step_m=1_000_000.0,
    )[0]
    geod = Geod(ellps="WGS84")
    expected = min(geod.inv(1.0, 1.0, longitude, 0.0)[2] for longitude in (0.0, 2.0))

    assert observed == pytest.approx(expected, abs=1e-9)
    assert observed > 150_000.0


def test_nearest_source_evidence_retains_segment_identity_and_coordinate() -> None:
    point = gpd.GeoSeries([Point(0.0, 0.0)], crs="EPSG:4326")
    lines = gpd.GeoDataFrame(
        {
            "source_id": ["far", "near"],
            "component_id": ["far-E", "near-W"],
            "shoreline_class": ["fixture", "fixture"],
            "polygon_index": [0, 1],
            "run_index": [0, 2],
            "chunk_index": [0, 3],
        },
        geometry=[
            LineString([(2.0, -1.0), (2.0, 1.0)]),
            LineString([(1.0, -1.0), (1.0, 1.0)]),
        ],
        crs="EPSG:4326",
    )
    distance = float(nearest_projected_strtree(point, lines, "EPSG:3857")[0])

    evidence = _nearest_source_evidence(
        point,
        lines,
        "EPSG:3857",
        expected_distance_m=distance,
        tie_tolerance_m=1e-6,
    )

    assert evidence["source_id"] == "near"
    assert evidence["component_id"] == "near-W"
    assert evidence["polygon_index"] == 1
    assert evidence["nearest_longitude"] == pytest.approx(1.0, abs=1e-12)
    assert evidence["nearest_latitude"] == pytest.approx(0.0, abs=1e-12)


def test_worker_audit_exercises_true_vector_query_chunks() -> None:
    point = {
        "longitude": 0.0,
        "latitude": 0.0,
        "projected_crs": "EPSG:3857",
    }
    lines = gpd.GeoDataFrame(
        {
            "source_id": ["one"],
            "component_id": ["one"],
            "shoreline_class": ["fixture"],
        },
        geometry=[LineString([(1.0, -1.0), (1.0, 1.0)])],
        crs="EPSG:4326",
    )
    expected = float(
        nearest_projected_strtree(
            gpd.GeoSeries([Point(0.0, 0.0)], crs="EPSG:4326"),
            lines,
            "EPSG:3857",
        )[0]
    )

    audit = _thread_and_query_chunk_audit(
        {("city", "source"): (point, lines, expected)},
        {
            "invariance_absolute_tolerance_m": 1e-6,
            "worker_counts": [1, 2],
            "query_chunk_sizes": [1, 2, 4],
        },
    )

    assert audit["all_runs_invariant"] is True
    assert any(run["vectorized_query_exercised"] for run in audit["runs"])
    assert {run["identical_query_replicates_per_task"] for run in audit["runs"]} == {4}


def test_custom_output_path_is_recorded_truthfully(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    inside = project_root / "artifacts" / "result.json"
    outside = tmp_path / "external" / "result.json"

    assert _recorded_output_path(project_root, inside) == "artifacts/result.json"
    assert _recorded_output_path(project_root, outside) == outside.resolve().as_posix()


def test_distance_rejects_geographic_output_crs() -> None:
    points = gpd.GeoSeries([Point(0, 0)], crs="EPSG:4326")
    lines = gpd.GeoSeries(
        [LineString([(1, -1), (1, 1)])],
        crs="EPSG:4326",
    )

    with pytest.raises(GshhgGeometryPilotError, match="projected"):
        nearest_projected_strtree(points, lines, "EPSG:4326")


def test_v2_amendment_and_base_preregistration_are_exactly_bound() -> None:
    amendment_path, amendment, base_path, base = _read_exact_configs(
        ROOT / "configs/multicity/gshhg_geometry_pilot_v2.toml"
    )

    assert amendment_path.name == "gshhg_geometry_pilot_v2.toml"
    assert base_path.name == "gshhg_geometry_pilot_v1.toml"
    assert amendment["v1_failure"]["diagnostic_distance_values_computed"] is False
    assert base["distance_audit"]["geodesic_absolute_tolerance_m"] == 100.0
    assert base["comparison"]["predictor_construction_allowed"] is False


def test_v2_repairs_only_one_exact_predeclared_invalid_polygon() -> None:
    invalid = Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)])
    valid = Polygon([(10, 0), (12, 0), (12, 2), (10, 2), (10, 0)])
    frame = gpd.GeoDataFrame(
        {
            "id": ["7", "8"],
            "level": [1, 1],
            "source": ["WVS", "WVS"],
            "parent_id": [-1, -1],
            "sibling_id": [-1, -1],
            "area": [1.0, 4.0],
        },
        geometry=[invalid, valid],
        crs="EPSG:4326",
    )
    repaired_container = shapely.make_valid(invalid)
    polygonal = shapely.union_all(
        [part for part in repaired_container.geoms if part.geom_type in {"Polygon", "MultiPolygon"}]
    )
    settings = {
        "allowed_invalid_polygon_count": 1,
        "source_id": "7",
        "level": 1,
        "source": "WVS",
        "validity_reason": shapely.is_valid_reason(invalid),
        "expected_bounds": list(invalid.bounds),
        "original_normalized_wkb_sha256": _normalized_wkb_sha256(invalid),
        "expected_make_valid_container_type": repaired_container.geom_type,
        "expected_polygonal_type": polygonal.geom_type,
        "expected_polygonal_normalized_wkb_sha256": _normalized_wkb_sha256(polygonal),
        "maximum_planar_area_delta_square_degrees": 10.0,
    }

    repaired, audit = repair_predeclared_l1_geometry(frame, settings)

    assert repaired.geometry.is_valid.all()
    assert audit["source_id"] == "7"
    assert audit["selection_used_city_or_distance"] is False
    changed = dict(settings)
    changed["source_id"] = "999"
    with pytest.raises(GshhgGeometryPilotError, match="identity"):
        repair_predeclared_l1_geometry(frame, changed)


def test_v2_five_names_may_authenticate_three_frozen_connected_waters() -> None:
    geometries = [
        Polygon([(0, 0), (9, 0), (9, 3), (0, 3), (0, 0)]),
        Polygon([(12, 0), (15, 0), (15, 3), (12, 3), (12, 0)]),
        Polygon([(18, 0), (21, 0), (21, 3), (18, 3), (18, 0)]),
        Polygon([(30, 0), (31, 0), (31, 1), (30, 1), (30, 0)]),
    ]
    frame = gpd.GeoDataFrame(
        {
            "id": ["10", "20", "30", "99"],
            "level": [2, 2, 2, 2],
            "source": ["WDBII"] * 4,
            "parent_id": [1] * 4,
            "sibling_id": [-1] * 4,
            "area": [100.0, 20.0, 10.0, -1.0],
        },
        geometry=geometries,
        crs="EPSG:4326",
    )
    seeds = [
        LakeSeed("A", 1, 1),
        LakeSeed("B", 4, 1),
        LakeSeed("C", 7, 1),
        LakeSeed("D", 13, 1),
        LakeSeed("E", 19, 1),
    ]
    source_rows = []
    for source_id, geometry, area in zip(
        ["10", "20", "30"],
        geometries[:3],
        [100.0, 20.0, 10.0],
        strict=True,
    ):
        source_rows.append(
            {
                "source_id": source_id,
                "reported_area": area,
                "expected_bounds": list(geometry.bounds),
                "expected_coordinate_count": len(geometry.exterior.coords),
                "expected_normalized_wkb_sha256": _normalized_wkb_sha256(geometry),
            }
        )
    settings = {
        "named_lake_count": 5,
        "expected_distinct_source_polygon_count": 3,
        "expected_source_ids": ["10", "20", "30"],
        "seed_mapping": [
            {"name": "A", "source_id": "10"},
            {"name": "B", "source_id": "10"},
            {"name": "C", "source_id": "10"},
            {"name": "D", "source_id": "20"},
            {"name": "E", "source_id": "30"},
        ],
        "source_polygons": source_rows,
    }

    selected, audit = select_connected_great_lakes(frame, seeds, settings)

    assert selected["id"].tolist() == ["10", "20", "30"]
    assert audit["named_seed_count"] == 5
    assert audit["distinct_source_polygon_count"] == 3
    assert audit["negative_area_river_lake_count_excluded"] == 1
