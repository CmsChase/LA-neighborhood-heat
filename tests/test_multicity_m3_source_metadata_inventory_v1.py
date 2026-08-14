from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from la_heat.multicity import m3_source_metadata_inventory_v1 as inventory_module
from la_heat.multicity.m3_source_acquisition_amendment import AMENDMENT_PATH
from la_heat.multicity.m3_source_metadata_inventory_v1 import (
    AUTHORIZATION_PATH,
    AUTHORIZATION_PERMISSIONS,
    INVENTORY_ACCESS_AUDIT,
    INVENTORY_PATH,
    MODULE_PATH,
    QUERY_IMPLEMENTATION_PATHS,
    RAW_ROOT,
    SCRIPT_PATH,
    M3SourceMetadataInventoryError,
    authenticate_expanded_source_inventory,
    authenticate_source_metadata_inventory_authorization,
    build_expanded_source_inventory,
    build_source_metadata_inventory_authorization,
    create_source_metadata_inventory_authorization,
)
from la_heat.multicity.source_footprints import (
    LANDSAT_FIELDS,
    local_date_interval_to_utc,
)
from la_heat.provenance import canonical_sha256, sha256_file

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def amendment() -> dict[str, Any]:
    return json.loads((PROJECT_ROOT / AMENDMENT_PATH).read_text(encoding="utf-8"))


def _copy(path: Path, root: Path) -> None:
    destination = root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PROJECT_ROOT / path, destination)


def _copy_inputs(root: Path, amendment: dict[str, Any]) -> None:
    paths = {AMENDMENT_PATH, MODULE_PATH, SCRIPT_PATH}
    paths.update(Path(path) for path in QUERY_IMPLEMENTATION_PATHS)
    paths.update(
        Path("configs/multicity/cities") / f"{city_id}.toml"
        for city_id in (
            "los_angeles_ca",
            "phoenix_az",
            "houston_tx",
            "chicago_il",
        )
    )
    for anchor in amendment["input_anchors"].values():
        paths.add(Path(anchor["path"]))
    previous_inventory = json.loads(
        (
            PROJECT_ROOT
            / amendment["input_anchors"]["previous_predictor_inventory"]["path"]
        ).read_text(encoding="utf-8")
    )
    for city_id in ("los_angeles_ca", "phoenix_az"):
        paths.add(
            Path(previous_inventory["output_tables"][f"{city_id}/overpasses"]["path"])
        )
    for path in paths:
        _copy(path, root)


def _patch_amendment_auth(
    monkeypatch: pytest.MonkeyPatch, amendment: dict[str, Any]
) -> None:
    monkeypatch.setattr(
        inventory_module,
        "authenticate_m3_source_acquisition_amendment",
        lambda _root, _path: amendment,
    )


def _prepared_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    amendment: dict[str, Any],
) -> Path:
    _copy_inputs(tmp_path, amendment)
    _patch_amendment_auth(monkeypatch, amendment)
    return tmp_path


def _query_summary(city_id: str, year: int) -> dict[str, Any]:
    query = {
        "collections": ["landsat-c2-l2"],
        "bbox": [-100.0, 25.0, -80.0, 45.0],
        "datetime": local_date_interval_to_utc(
            date(year, 5, 1), date(year, 10, 31), "America/Chicago"
        ),
        "limit": 100,
        "fields": {
            "include": list(LANDSAT_FIELDS),
            "exclude": ["assets", "links"],
        },
        "query": {
            "platform": {"in": ["landsat-8", "landsat-9"]},
            "landsat:collection_category": {"eq": "T1"},
            "landsat:correction": {"eq": "L2SP"},
        },
    }
    return {
        "city_id": city_id,
        "year": year,
        "endpoint": "https://example.invalid/search",
        "query": query,
        "query_sha256": canonical_sha256(query),
        "page_count": 1,
        "query_response_items": 1,
        "unique_items": 1,
        "duplicate_items": 0,
        "pagination_exhausted": True,
        "assets_excluded": True,
    }


def _fake_runner(
    calls: list[str], *, expose_assets: bool = False
) -> Any:
    def run(
        _root: Path, city_id: str, _client: object
    ) -> tuple[pd.DataFrame, list[dict[str, Any]], list[dict[str, Any]]]:
        calls.append(city_id)
        city_number = 1 if city_id == "houston_tx" else 2
        rows: list[dict[str, Any]] = []
        pages: list[dict[str, Any]] = []
        queries: list[dict[str, Any]] = []
        for year in range(2020, 2026):
            acquired = f"{year}-07-15T16:30:00Z"
            row = {
                "overpass_id": f"landsat-8|{city_id}|{year}",
                "platform": "landsat-8",
                "local_date": f"{year}-07-15",
                "acquired_utc_min": acquired,
                "acquired_utc_max": acquired,
                "scene_ids": f"LC08_{city_number:03d}_{year}",
                "wrs_path_rows": f"{city_number:03d}/001",
                "scene_count": 1,
                "union_city_coverage_fraction": 0.99,
                "ambiguous_local_date": False,
                "primary_eligible": True,
                "source_lock_sha256": canonical_sha256(
                    {"city_id": city_id, "year": year}
                ),
            }
            rows.append(row)
            feature: dict[str, Any] = {
                "id": f"LC08_{city_number:03d}_{year}",
                "collection": "landsat-c2-l2",
                "geometry": None,
                "bbox": [],
                "properties": {},
            }
            if expose_assets and year == 2020:
                feature["assets"] = {"lwir11": {"href": "forbidden"}}
            pages.append(
                {
                    "query_year": year,
                    "page": {
                        "type": "FeatureCollection",
                        "features": [feature],
                        "links": [],
                    },
                }
            )
            queries.append(_query_summary(city_id, year))
        return pd.DataFrame(rows), pages, queries

    return run


def test_preview_is_exact_metadata_only_authorization() -> None:
    watched = (PROJECT_ROOT / AUTHORIZATION_PATH, PROJECT_ROOT / INVENTORY_PATH)
    before = [
        (path.exists(), sha256_file(path) if path.is_file() else None)
        for path in watched
    ]
    preview = build_source_metadata_inventory_authorization(PROJECT_ROOT)
    after = [
        (path.exists(), sha256_file(path) if path.is_file() else None)
        for path in watched
    ]
    assert before == after
    assert preview["state"] == "source_metadata_inventory_authorized"
    assert preview["metadata_query_city_ids"] == ["houston_tx", "chicago_il"]
    assert preview["permissions"] == AUTHORIZATION_PERMISSIONS
    assert preview["permissions"]["query_houston_chicago_public_landsat_stac_metadata"]
    assert preview["permissions"]["request_or_read_landsat_item_assets"] is False
    assert preview["permissions"]["read_landsat_thermal_or_target_qa_values"] is False
    unsigned = dict(preview)
    assert unsigned.pop("commit_sha256") == canonical_sha256(unsigned)


def test_authorization_is_append_only_and_exactly_rebuilt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    amendment: dict[str, Any],
) -> None:
    root = _prepared_root(tmp_path, monkeypatch, amendment)
    created = create_source_metadata_inventory_authorization(root)
    assert authenticate_source_metadata_inventory_authorization(root) == created
    with pytest.raises(M3SourceMetadataInventoryError, match="already exists"):
        create_source_metadata_inventory_authorization(root)


def test_authorization_permission_tamper_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    amendment: dict[str, Any],
) -> None:
    root = _prepared_root(tmp_path, monkeypatch, amendment)
    create_source_metadata_inventory_authorization(root)
    path = root / AUTHORIZATION_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["permissions"]["read_landsat_thermal_or_target_qa_values"] = True
    payload.pop("commit_sha256")
    payload["commit_sha256"] = canonical_sha256(payload)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(M3SourceMetadataInventoryError, match="reproduces exactly"):
        authenticate_source_metadata_inventory_authorization(root)


def test_expanded_inventory_retains_old_cities_and_queries_only_houston_chicago(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    amendment: dict[str, Any],
) -> None:
    root = _prepared_root(tmp_path, monkeypatch, amendment)
    create_source_metadata_inventory_authorization(root)
    calls: list[str] = []
    created = build_expanded_source_inventory(
        root,
        client=object(),
        query_runner=_fake_runner(calls),
    )
    assert calls == ["houston_tx", "chicago_il"]
    assert created["state"] == "expanded_source_inventory_complete"
    assert created["overpass_count_by_city"] == {
        "los_angeles_ca": 90,
        "phoenix_az": 22,
        "houston_tx": 6,
        "chicago_il": 6,
    }
    assert created["overpass_count"] == 124
    assert created["access_audit"] == INVENTORY_ACCESS_AUDIT
    assert created["blind_test_asset_or_value_accessed"] is False
    assert len(created["query_records"]) == 12
    assert len(created["raw_metadata_files"]) == 12
    assert all(
        set(row) >= {"city_id", "target_date", "overpass_id", "scene_ids"}
        for row in created["overpasses"]
    )
    target_plan = json.loads(
        (root / "manifests/multicity/targets/TARGET_BUILD_PLAN.json").read_text(
            encoding="utf-8"
        )
    )
    assert all(
        row["target_context_commit_sha256"]
        == canonical_sha256(target_plan["cities"][row["city_id"]]["context_locks"])
        for row in created["overpasses"]
    )
    assert all("scene_metadata" not in row for row in created["overpasses"])
    assert authenticate_expanded_source_inventory(root) == created


def test_asset_exposure_is_rejected_before_inventory_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    amendment: dict[str, Any],
) -> None:
    root = _prepared_root(tmp_path, monkeypatch, amendment)
    create_source_metadata_inventory_authorization(root)
    with pytest.raises(M3SourceMetadataInventoryError, match="exposed an asset"):
        build_expanded_source_inventory(
            root,
            client=object(),
            query_runner=_fake_runner([], expose_assets=True),
        )
    assert not (root / INVENTORY_PATH).exists()


def test_formal_outputs_are_committed_when_present() -> None:
    for relative in (AUTHORIZATION_PATH, INVENTORY_PATH):
        path = PROJECT_ROOT / relative
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            unsigned = dict(payload)
            commit = unsigned.pop("commit_sha256")
            assert commit == canonical_sha256(unsigned)
    if (PROJECT_ROOT / INVENTORY_PATH).exists():
        assert (PROJECT_ROOT / RAW_ROOT).is_dir()
