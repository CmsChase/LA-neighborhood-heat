from __future__ import annotations

from collections.abc import Callable

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import MultiPolygon, Polygon, box

from la_heat.multicity.gshhg_l3_hierarchy_audit import (
    EXPECTED_COLUMNS,
    StructuralAuditError,
    _semantic_layer_sha256,
    audit_l3_structure,
    canonical_source_id_sort_key,
)

PARENT_IDS = (180507, 180515, 180517)


def _l2_parents(
    *,
    first_geometry: Polygon | None = None,
) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": list(PARENT_IDS)},
        geometry=[
            first_geometry or box(0.0, 0.0, 10.0, 10.0),
            box(20.0, 0.0, 30.0, 10.0),
            box(40.0, 0.0, 50.0, 10.0),
        ],
        crs="EPSG:4326",
    )


def _l3_frame(
    *,
    ids: list[str] | None = None,
    parent_ids: list[int] | None = None,
    sibling_ids: list[int] | None = None,
    areas: list[float] | None = None,
    geometries: list[Polygon | MultiPolygon] | None = None,
    levels: list[int] | None = None,
    sources: list[str] | None = None,
) -> gpd.GeoDataFrame:
    ids = ids or ["10-W", "2", "10-E", "10", "11", "1"]
    row_count = len(ids)
    parent_ids = parent_ids or [180507, 180507, 180507, 180515, 180517, 999999]
    sibling_ids = sibling_ids or [91, 0, 91, 10, 11, 1]
    areas = areas or [1.0] * row_count
    levels = levels or [3] * row_count
    sources = sources or ["WDBII"] * row_count
    geometries = geometries or [
        box(5.0, 1.0, 6.0, 2.0),
        box(1.0, 1.0, 2.0, 2.0),
        box(3.0, 1.0, 4.0, 2.0),
        box(21.0, 1.0, 22.0, 2.0),
        box(41.0, 1.0, 42.0, 2.0),
        box(61.0, 1.0, 62.0, 2.0),
    ]
    if not all(
        len(values) == row_count
        for values in (
            parent_ids,
            sibling_ids,
            areas,
            geometries,
            levels,
            sources,
        )
    ):
        raise ValueError("All synthetic L3 columns must have the same length.")
    return gpd.GeoDataFrame(
        {
            "id": pd.Series(ids, dtype="str"),
            "level": pd.Series(levels, dtype="int32"),
            "source": pd.Series(sources, dtype="str"),
            "parent_id": pd.Series(parent_ids, dtype="int32"),
            "sibling_id": pd.Series(sibling_ids, dtype="int32"),
            "area": pd.Series(areas, dtype="float64"),
        },
        geometry=geometries,
        crs="EPSG:4326",
    )


def _assert_gate(
    frame: gpd.GeoDataFrame,
    gate: str,
    *,
    parents: gpd.GeoDataFrame | None = None,
) -> None:
    with pytest.raises(StructuralAuditError) as exc_info:
        audit_l3_structure(frame, parents if parents is not None else _l2_parents())
    assert exc_info.value.gate == gate


def test_source_ids_use_numeric_then_none_e_w_suffix_order() -> None:
    identifiers = ["10-W", "2", "1-W", "10-E", "1-E", "10", "1"]

    ordered = sorted(identifiers, key=canonical_source_id_sort_key)

    assert ordered == ["1", "1-E", "1-W", "2", "10", "10-E", "10-W"]


@pytest.mark.parametrize(
    "identifier",
    ["", "01", "-1", "1-A", "1-e", "1-E-W", " 1", "1 "],
)
def test_source_id_parser_rejects_noncanonical_ids(identifier: str) -> None:
    with pytest.raises(StructuralAuditError) as exc_info:
        canonical_source_id_sort_key(identifier)
    assert exc_info.value.gate == "canonical_source_id"


def test_semantic_hashes_and_selected_order_are_row_order_invariant() -> None:
    original = _l3_frame()
    permuted = original.iloc[[5, 3, 0, 4, 2, 1]].reset_index(drop=True)

    first_selected, first_audit = audit_l3_structure(original, _l2_parents())
    second_selected, second_audit = audit_l3_structure(permuted, _l2_parents())

    assert _semantic_layer_sha256(original) == _semantic_layer_sha256(permuted)
    assert (
        first_audit["full_layer_attribute_geometry_semantic_sha256"]
        == (second_audit["full_layer_attribute_geometry_semantic_sha256"])
    )
    first_record = first_audit["selected_direct_descendants"]
    second_record = second_audit["selected_direct_descendants"]
    assert (
        first_record["attribute_geometry_semantic_sha256"]
        == (second_record["attribute_geometry_semantic_sha256"])
    )
    assert (
        first_record["exterior_linework_semantic_sha256"]
        == (second_record["exterior_linework_semantic_sha256"])
    )
    assert first_selected["id"].tolist() == ["2", "10", "10-E", "10-W", "11"]
    assert second_selected["id"].tolist() == first_selected["id"].tolist()


def test_valid_layer_selects_every_direct_child_and_ignores_sibling_id() -> None:
    frame = _l3_frame()

    selected, audit = audit_l3_structure(frame, _l2_parents())

    record = audit["selected_direct_descendants"]
    expected_mask = frame["parent_id"].isin(PARENT_IDS)
    assert set(selected["id"]) == set(frame.loc[expected_mask, "id"])
    assert record["row_count"] == int(expected_mask.sum())
    assert record["counts_by_parent"] == {
        "180507": 3,
        "180515": 1,
        "180517": 1,
    }
    assert record["sibling_id_used_for_selection"] is False
    assert record["l4_member_opened"] is False
    assert audit["all_structural_gates_passed"] is True


def test_child_outside_declared_parent_fails_strict_within_gate() -> None:
    frame = _l3_frame(
        ids=["2"],
        parent_ids=[180507],
        sibling_ids=[0],
        geometries=[box(9.0, 1.0, 11.0, 2.0)],
    )

    _assert_gate(frame, "selected_child_strictly_within_parent")


def test_child_declaring_unavailable_selected_parent_fails() -> None:
    frame = _l3_frame(
        ids=["2"],
        parent_ids=[180507],
        sibling_ids=[0],
        geometries=[box(1.0, 1.0, 2.0, 2.0)],
    )
    parents = _l2_parents().iloc[1:].reset_index(drop=True)

    _assert_gate(frame, "selected_child_declared_parent_present", parents=parents)


def test_child_touching_parent_exterior_fails_boundary_gate() -> None:
    frame = _l3_frame(
        ids=["2"],
        parent_ids=[180507],
        sibling_ids=[0],
        geometries=[box(0.0, 1.0, 1.0, 2.0)],
    )

    assert frame.geometry.iloc[0].within(_l2_parents().geometry.iloc[0])
    _assert_gate(frame, "selected_child_boundary_disjoint_from_parent_exterior")


@pytest.mark.parametrize(
    "second_geometry",
    [
        box(1.5, 1.5, 2.5, 2.5),
        box(1.25, 1.25, 1.75, 1.75),
    ],
    ids=["partial-overlap", "containment"],
)
def test_sibling_overlap_or_containment_fails(
    second_geometry: Polygon,
) -> None:
    frame = _l3_frame(
        ids=["2", "3"],
        parent_ids=[180507, 180507],
        sibling_ids=[3, 2],
        geometries=[box(1.0, 1.0, 2.0, 2.0), second_geometry],
    )

    _assert_gate(frame, "selected_sibling_interiors_not_overlapping")


def test_sibling_boundary_touch_is_allowed_because_interiors_do_not_overlap() -> None:
    frame = _l3_frame(
        ids=["2", "3"],
        parent_ids=[180507, 180507],
        sibling_ids=[3, 2],
        geometries=[box(1.0, 1.0, 2.0, 2.0), box(2.0, 1.0, 3.0, 2.0)],
    )

    selected, audit = audit_l3_structure(frame, _l2_parents())

    assert selected["id"].tolist() == ["2", "3"]
    assert audit["topology"]["all_selected_sibling_interiors_nonoverlapping"] is True
    assert audit["topology"]["positive_area_sibling_overlap_pair_count"] == 0


def test_exact_180_degree_exterior_jump_fails() -> None:
    child = Polygon(
        [
            (-90.0, 0.0),
            (90.0, 0.0),
            (89.0, 1.0),
            (-89.0, 1.0),
            (-90.0, 0.0),
        ]
    )
    parent = box(-100.0, -10.0, 100.0, 10.0)
    frame = _l3_frame(
        ids=["2"],
        parent_ids=[180507],
        sibling_ids=[0],
        geometries=[child],
    )

    _assert_gate(
        frame,
        "selected_exterior_longitude_jumps",
        parents=_l2_parents(first_geometry=parent),
    )


@pytest.mark.parametrize("area", [0.0, -1.0, np.nan, np.inf, -np.inf])
def test_reported_area_must_be_finite_and_positive(area: float) -> None:
    frame = _l3_frame(
        ids=["2"],
        parent_ids=[180507],
        sibling_ids=[0],
        areas=[area],
        geometries=[box(1.0, 1.0, 2.0, 2.0)],
    )

    _assert_gate(frame, "l3_reported_area_finite_positive")


def test_duplicate_source_ids_fail() -> None:
    frame = _l3_frame(
        ids=["2", "2"],
        parent_ids=[180507, 180507],
        sibling_ids=[0, 0],
        geometries=[box(1.0, 1.0, 2.0, 2.0), box(3.0, 1.0, 4.0, 2.0)],
    )

    _assert_gate(frame, "l3_source_ids_unique")


@pytest.mark.parametrize(
    ("mutate", "gate"),
    [
        (
            lambda frame: frame[
                [
                    "level",
                    "id",
                    "source",
                    "parent_id",
                    "sibling_id",
                    "area",
                    "geometry",
                ]
            ],
            "l3_exact_columns",
        ),
        (
            lambda frame: frame.assign(level=frame["level"].astype("int64")),
            "l3_exact_dtypes",
        ),
        (
            lambda frame: frame.set_crs("EPSG:3857", allow_override=True),
            "l3_crs",
        ),
    ],
    ids=["column-order", "dtype", "crs"],
)
def test_exact_schema_is_enforced(
    mutate: Callable[[gpd.GeoDataFrame], gpd.GeoDataFrame],
    gate: str,
) -> None:
    frame = _l3_frame(
        ids=["2"],
        parent_ids=[180507],
        sibling_ids=[0],
        geometries=[box(1.0, 1.0, 2.0, 2.0)],
    )
    changed = mutate(frame)

    _assert_gate(changed, gate)


def test_exact_columns_fixture_matches_frozen_contract() -> None:
    frame = _l3_frame()

    assert tuple(frame.columns) == EXPECTED_COLUMNS
    assert {column: str(dtype) for column, dtype in frame.dtypes.items()} == {
        "id": "str",
        "level": "int32",
        "source": "str",
        "parent_id": "int32",
        "sibling_id": "int32",
        "area": "float64",
        "geometry": "geometry",
    }


def test_level_and_source_domain_fail_closed() -> None:
    wrong_level = _l3_frame(
        ids=["2"],
        parent_ids=[180507],
        sibling_ids=[0],
        levels=[2],
        geometries=[box(1.0, 1.0, 2.0, 2.0)],
    )
    wrong_source = _l3_frame(
        ids=["2"],
        parent_ids=[180507],
        sibling_ids=[0],
        sources=["OTHER"],
        geometries=[box(1.0, 1.0, 2.0, 2.0)],
    )

    _assert_gate(wrong_level, "all_l3_rows_level_3")
    _assert_gate(wrong_source, "l3_source_values")


def test_polygon_only_nonempty_valid_geometry_contract() -> None:
    multipolygon = _l3_frame(
        ids=["2"],
        parent_ids=[180507],
        sibling_ids=[0],
        geometries=[MultiPolygon([box(1.0, 1.0, 2.0, 2.0)])],
    )
    empty = _l3_frame(
        ids=["2"],
        parent_ids=[180507],
        sibling_ids=[0],
        geometries=[Polygon()],
    )
    bow_tie = _l3_frame(
        ids=["2"],
        parent_ids=[180507],
        sibling_ids=[0],
        geometries=[Polygon([(1.0, 1.0), (2.0, 2.0), (1.0, 2.0), (2.0, 1.0)])],
    )

    _assert_gate(multipolygon, "l3_geometry_type")
    _assert_gate(empty, "l3_geometry_nonempty")
    _assert_gate(bow_tie, "l3_geometry_valid_without_repair")
