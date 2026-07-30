from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import shapely
from shapely.geometry import LineString, Point, box

import la_heat.multicity.gshhg_l3_hierarchy_audit as audit_module
from la_heat.multicity.gshhg_l3_hierarchy_audit import (
    DistanceRun,
    GitGate,
    NumericalAuditError,
    StructuralAuditError,
    _derive_probes,
    _distance_run,
    _nearest_logical_source_evidence,
    _probe_gate_record,
)


def _logical_lines(
    source_ids: list[str],
    geometries: list[LineString],
) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "source_level": [3] * len(source_ids),
            "source_id": source_ids,
        },
        geometry=geometries,
        crs="EPSG:4326",
    )


def _projected_minimum(
    point: gpd.GeoSeries,
    candidates: gpd.GeoDataFrame,
    projected_crs: str,
) -> float:
    projected_point = point.to_crs(projected_crs).iloc[0]
    projected_lines = candidates.to_crs(projected_crs)
    distances = shapely.distance(
        projected_point,
        projected_lines.geometry.to_numpy(),
    )
    return float(np.min(np.asarray(distances, dtype=np.float64)))


def _probe_distance_run(
    distance_m: float,
    *,
    source_id: str = "2",
) -> DistanceRun:
    return DistanceRun(
        record={
            "distance_m": distance_m,
            "nearest_source_evidence": {
                "nearest_source_level": 3,
                "nearest_source_id": source_id,
                "nearest_tie_count": 1,
                "nearest_longitude": 0.0,
                "nearest_latitude": 0.0,
            },
        },
        candidates=gpd.GeoDataFrame(geometry=[], crs="EPSG:4326"),
    )


def test_probe_selection_uses_area_descending_then_canonical_id() -> None:
    selected_l3 = gpd.GeoDataFrame(
        {
            "id": ["1", "10-W", "10-E", "2"],
            "parent_id": [180507] * 4,
            "area": [9.0, 10.0, 10.0, 10.0],
        },
        geometry=[
            box(-118.40, 34.00, -118.35, 34.05),
            box(-118.30, 34.00, -118.25, 34.05),
            box(-118.20, 34.00, -118.15, 34.05),
            box(-118.10, 34.00, -118.05, 34.05),
        ],
        crs="EPSG:4326",
    )

    probes = _derive_probes(selected_l3)

    assert len(probes) == 1
    assert probes[0]["child_source_id"] == "2"
    assert probes[0]["source_reported_area"] == 10.0


def test_probe_is_strictly_inside_and_uses_frozen_northern_utm_formula() -> None:
    child = box(-118.30, 34.00, -118.20, 34.10)
    selected_l3 = gpd.GeoDataFrame(
        {
            "id": ["2"],
            "parent_id": [180507],
            "area": [1.0],
        },
        geometry=[child],
        crs="EPSG:4326",
    )

    probe = _derive_probes(selected_l3)[0]
    point = Point(probe["longitude"], probe["latitude"])

    assert child.contains(point)
    assert not child.boundary.intersects(point)
    assert probe["projected_crs"] == "EPSG:32611"
    assert probe["derived_projected_crs"] == "EPSG:32611"


def test_multiple_nearest_chunks_from_one_logical_source_are_not_a_tie() -> None:
    point = gpd.GeoSeries([Point(0.0, 0.0)], crs="EPSG:4326")
    candidates = _logical_lines(
        ["7", "7", "8"],
        [
            LineString([(0.01, -0.01), (0.01, 0.0)]),
            LineString([(0.01, 0.0), (0.01, 0.01)]),
            LineString([(0.02, -0.01), (0.02, 0.01)]),
        ],
    )
    projected_crs = "EPSG:32631"
    expected = _projected_minimum(point, candidates, projected_crs)

    evidence = _nearest_logical_source_evidence(
        point,
        candidates,
        projected_crs=projected_crs,
        expected_distance_m=expected,
        tie_tolerance_m=0.000001,
        require_unique=True,
    )

    assert evidence["nearest_source_id"] == "7"
    assert evidence["nearest_tie_count"] == 1
    assert evidence["chunk_rows_aggregated_by_logical_source"] is True


def test_equal_nearest_distances_from_different_sources_fail_unique_gate() -> None:
    point = gpd.GeoSeries([Point(0.0, 0.0)], crs="EPSG:4326")
    shared_line = LineString([(0.01, -0.01), (0.01, 0.01)])
    candidates = _logical_lines(["7", "8"], [shared_line, shared_line])
    projected_crs = "EPSG:32631"
    expected = _projected_minimum(point, candidates, projected_crs)

    with pytest.raises(NumericalAuditError) as exc_info:
        _nearest_logical_source_evidence(
            point,
            candidates,
            projected_crs=projected_crs,
            expected_distance_m=expected,
            tie_tolerance_m=0.000001,
            require_unique=True,
        )

    assert exc_info.value.gate == "unique_nearest_logical_source"


def test_direct_own_exterior_difference_over_tolerance_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        audit_module,
        "nearest_projected_bruteforce",
        lambda *_args, **_kwargs: np.asarray([0.0], dtype=np.float64),
    )
    selected_l3 = gpd.GeoDataFrame(
        {"id": ["2"]},
        geometry=[box(-0.01, -0.01, 0.01, 0.01)],
        crs="EPSG:4326",
    )
    probe = {
        "point_id": "probe",
        "child_source_id": "2",
        "longitude": 0.0,
        "latitude": 0.0,
        "projected_crs": "EPSG:32631",
    }
    just_over_tolerance = float(np.nextafter(0.000001, np.inf))

    with pytest.raises(NumericalAuditError) as exc_info:
        _probe_gate_record(
            probe,
            _probe_distance_run(just_over_tolerance),
            _probe_distance_run(1.0),
            selected_l3=selected_l3,
            tolerance_m=0.000001,
        )

    assert exc_info.value.gate == "probe_indexed_equals_direct_own_exterior"


def test_improvement_equal_to_one_micrometre_fails_strict_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        audit_module,
        "nearest_projected_bruteforce",
        lambda *_args, **_kwargs: np.asarray([1.0], dtype=np.float64),
    )
    selected_l3 = gpd.GeoDataFrame(
        {"id": ["2"]},
        geometry=[box(-0.01, -0.01, 0.01, 0.01)],
        crs="EPSG:4326",
    )
    probe = {
        "point_id": "probe",
        "child_source_id": "2",
        "longitude": 0.0,
        "latitude": 0.0,
        "projected_crs": "EPSG:32631",
    }

    with pytest.raises(NumericalAuditError) as exc_info:
        _probe_gate_record(
            probe,
            _probe_distance_run(1.0),
            _probe_distance_run(1.000001),
            selected_l3=selected_l3,
            tolerance_m=0.000001,
        )

    assert exc_info.value.gate == "probe_l3_strict_improvement"


def test_distance_run_checks_radius_engines_order_and_geodesic_reference() -> None:
    point = {
        "point_id": "synthetic",
        "point_kind": "real_l3_source_geometry_probe",
        "label": "synthetic numerical helper probe",
        "longitude": 0.0,
        "latitude": 0.0,
        "projected_crs": "EPSG:32631",
    }
    linework = _logical_lines(
        ["7", "8"],
        [
            LineString([(0.01, -0.01), (0.01, 0.0), (0.01, 0.01)]),
            LineString([(0.02, -0.01), (0.02, 0.0), (0.02, 0.01)]),
        ],
    )
    settings = {
        "search_radii_km": [0.5, 2.0, 4.0],
        "invariance_absolute_tolerance_m": 0.000001,
        "geodesic_densification_max_step_m": 50.0,
        "geodesic_absolute_tolerance_m": 100.0,
        "geodesic_relative_tolerance": 0.005,
    }

    run = _distance_run(
        point,
        linework,
        settings,
        source_contract="synthetic_l3",
        include_geodesic=True,
        require_unique_source=True,
    )
    record = run.record

    assert record["accepted_radius_km"] == 2.0
    assert [row["accepted"] for row in record["radius_audit"]] == [
        False,
        True,
        True,
    ]
    assert record["distance_m"] > 0.0
    assert record["strtree_bruteforce_absolute_difference_m"] <= 0.000001
    assert record["source_order_absolute_difference_m"] <= 0.000001
    assert record["maximum_radius_invariance_difference_m"] <= 0.000001
    assert record["geodesic_distance_m"] > 0.0
    assert abs(record["projected_minus_geodesic_m"]) <= 100.0
    assert record["nearest_source_evidence"]["nearest_source_id"] == "7"


def test_phase_one_failure_never_starts_probe_or_distance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "synthetic.toml"
    config = {"source": {"archive_path": "synthetic-never-opened.zip"}}
    git_gate = GitGate(
        head="a" * 40,
        branch="main",
        origin_main="a" * 40,
        tracked_blob_sha1={},
    )
    success_path = tmp_path / "success.json"
    v1_failure_path = tmp_path / "v1_failure.json"
    failure_path = tmp_path / "failure.json"
    table_path = tmp_path / "diagnostic.csv"
    v1_failure_path.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(
        audit_module,
        "_authenticate_pre_archive_inputs",
        lambda *_args, **_kwargs: (
            tmp_path,
            config_path,
            config,
            {},
            {},
            {},
            object(),
            git_gate,
            (),
        ),
    )
    monkeypatch.setattr(
        audit_module,
        "_terminal_paths",
        lambda *_args, **_kwargs: (
            success_path,
            v1_failure_path,
            failure_path,
            table_path,
        ),
    )
    monkeypatch.setattr(
        audit_module,
        "_same_git_gate",
        lambda *_args, **_kwargs: None,
    )

    def fail_structure(*_args: object, **_kwargs: object) -> None:
        raise StructuralAuditError(
            "synthetic_phase_one_failure",
            expected="valid synthetic structure",
            observed="invalid synthetic structure",
        )

    def forbidden_numerical(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Numerical phase started after phase-one failure.")

    monkeypatch.setattr(audit_module, "run_structural_phase", fail_structure)
    monkeypatch.setattr(audit_module, "run_numerical_phase", forbidden_numerical)
    monkeypatch.setattr(audit_module, "_derive_probes", forbidden_numerical)
    monkeypatch.setattr(audit_module, "_distance_run", forbidden_numerical)
    monkeypatch.setattr(
        audit_module,
        "_failure_payload",
        lambda error, **_kwargs: {
            "state": "synthetic_failure",
            "phase": "phase_1_structure",
            "gate": error.gate,
        },
    )

    result = audit_module.audit_gshhg_l3_hierarchy(config_path)

    assert result == {
        "state": "synthetic_failure",
        "phase": "phase_1_structure",
        "gate": "synthetic_phase_one_failure",
    }
    assert failure_path.is_file()
    assert not table_path.exists()
