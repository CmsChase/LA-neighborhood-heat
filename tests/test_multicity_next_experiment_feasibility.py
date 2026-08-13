from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd

from la_heat.multicity.next_experiment_feasibility import (
    ACCESS_CONTRACT,
    eligible_physical_overpasses,
    load_feasibility_config,
    worldcover_eligible_counts,
)
from la_heat.multicity.source_footprints import (
    LANDSAT_FIELDS,
    LANDSAT_PROPERTIES,
    SourceFootprintError,
    fetch_public_stac_metadata,
)

ROOT = Path(__file__).resolve().parents[1]


class _Response:
    status_code = 200
    headers: dict[str, str] = {}
    url = "https://planetarycomputer.microsoft.com/api/stac/v1/search"

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.payload

    def close(self) -> None:
        return None


class _Client:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.body: dict[str, object] | None = None

    def post(self, _url: str, **kwargs: object) -> _Response:
        self.body = dict(kwargs["json"])
        return _Response(self.payload)


def _stac_item(*, include_assets: bool = False) -> dict[str, object]:
    item: dict[str, object] = {
        "id": "LC09_L2SP_047027_20250719_02_T1",
        "collection": "landsat-c2-l2",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[-1, 0], [0, 0], [0, 1], [-1, 1], [-1, 0]]],
        },
        "bbox": [-1, 0, 0, 1],
        "properties": {
            "datetime": "2025-07-19T18:40:00Z",
            "platform": "landsat-9",
            "landsat:wrs_path": "047",
            "landsat:wrs_row": "027",
            "landsat:collection_category": "T1",
            "landsat:correction": "L2SP",
        },
    }
    if include_assets:
        item["assets"] = {"lwir11": {"href": "https://forbidden.example/target.tif"}}
    return item


def test_config_freezes_candidate_and_replacement_order() -> None:
    config = load_feasibility_config(ROOT)
    assert [city.id for city in config.primary] == [
        "seattle_wa",
        "denver_co",
        "atlanta_ga",
        "miami_fl",
    ]
    assert [city.id for city in config.replacements] == [
        "dallas_tx",
        "minneapolis_mn",
        "portland_or",
        "baltimore_md",
    ]
    assert ACCESS_CONTRACT["new_candidate_thermal_values_read"] is False
    assert ACCESS_CONTRACT["new_candidate_target_qa_values_read"] is False
    assert ACCESS_CONTRACT["landsat_stac_item_assets_returned"] is False
    assert ACCESS_CONTRACT["worldcover_stac_item_assets_read"] is True
    assert ACCESS_CONTRACT["worldcover_signed_map_asset_urls_persisted"] is False


def test_landsat_stac_query_excludes_assets_and_rejects_returned_assets() -> None:
    payload = {
        "type": "FeatureCollection",
        "features": [_stac_item()],
        "links": [],
    }
    client = _Client(payload)
    features, _, _ = fetch_public_stac_metadata(
        client,
        api="https://planetarycomputer.microsoft.com/api/stac/v1",
        collection="landsat-c2-l2",
        bbox_wgs84=[-1, 0, 0, 1],
        datetime_interval="2025-03-01T00:00:00Z/2025-12-01T00:00:00Z",
        fields=LANDSAT_FIELDS,
        properties=LANDSAT_PROPERTIES,
        page_limit=100,
        query={
            "landsat:collection_category": {"eq": "T1"},
            "landsat:correction": {"eq": "L2SP"},
        },
    )
    assert features[0]["id"] == "LC09_L2SP_047027_20250719_02_T1"
    assert client.body is not None
    assert client.body["fields"]["exclude"] == ["assets", "links"]  # type: ignore[index]

    forbidden = _Client(
        {"type": "FeatureCollection", "features": [_stac_item(include_assets=True)], "links": []}
    )
    try:
        fetch_public_stac_metadata(
            forbidden,
            api="https://planetarycomputer.microsoft.com/api/stac/v1",
            collection="landsat-c2-l2",
            bbox_wgs84=[-1, 0, 0, 1],
            datetime_interval="2025-03-01T00:00:00Z/2025-12-01T00:00:00Z",
            fields=LANDSAT_FIELDS,
            properties=LANDSAT_PROPERTIES,
            page_limit=100,
        )
    except SourceFootprintError as error:
        assert "assets or links" in str(error)
    else:
        raise AssertionError("A Landsat item asset object was accepted.")


def test_physical_date_gate_uses_coverage_boundary_and_sixteen_dates() -> None:
    frame = pd.DataFrame(
        {
            "local_date": [f"2025-05-{day:02d}" for day in range(1, 18)],
            "union_city_coverage_fraction": [0.98] * 16 + [0.9799],
            "ambiguous_local_date": [False] * 17,
        }
    )
    marked, dates, passed = eligible_physical_overpasses(
        frame,
        minimum_union_coverage_fraction=0.98,
        minimum_unique_physical_dates=16,
    )
    assert passed is True
    assert len(dates) == 16
    assert int(marked["primary_eligible"].sum()) == 16
    _, _, failed = eligible_physical_overpasses(
        frame.iloc[:15],
        minimum_union_coverage_fraction=0.98,
        minimum_unique_physical_dates=16,
    )
    assert failed is False


def test_worldcover_gate_counts_nonwater_support_per_tract() -> None:
    zones = np.array([[1, 1, 2], [1, 2, 2]], dtype=np.int32)
    classes = np.array([[10, 80, 0], [50, 80, 30]], dtype=np.uint8)
    counts = worldcover_eligible_counts(zones, classes, tract_count=2)
    assert counts.tolist() == [2, 1]
    water_only = np.array([[80, 80, 0], [80, 80, 0]], dtype=np.uint8)
    assert worldcover_eligible_counts(zones, water_only, tract_count=2).tolist() == [0, 0]
    unexpected = np.array([[10, 81, 0], [50, 80, 30]], dtype=np.uint8)
    try:
        worldcover_eligible_counts(zones, unexpected, tract_count=2)
    except RuntimeError as error:
        assert "unexpected classes" in str(error)
    else:
        raise AssertionError("An unknown WorldCover class was counted as eligible.")


def test_module_has_no_target_reader_or_model_imports() -> None:
    path = ROOT / "src/la_heat/multicity/next_experiment_feasibility.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        str(node.module)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    forbidden = {
        "la_heat.aligned_landsat",
        "la_heat.final_test_inventory",
        "la_heat.multicity.target_processor",
        "la_heat.multicity.target_engine",
        "la_heat.multicity.model_fit_prediction",
        "la_heat.multicity.external_evaluation",
    }
    assert imports.isdisjoint(forbidden)
