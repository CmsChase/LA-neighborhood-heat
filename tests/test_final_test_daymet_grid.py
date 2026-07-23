from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import rasterio

import la_heat.daymet_grid as development_daymet
from la_heat.daymet_grid import (
    DAYMET_CMR_COLLECTION_ID,
    DAYMET_FULL_GRID_TRANSFORM,
    DAYMET_GRID_CRS,
    DaymetGranule,
    DaymetNetCDFSpec,
    EarthdataBearerToken,
)
from la_heat.final_test_daymet_grid import (
    GRANULE_INVENTORY_FILENAME,
    PROVENANCE_FILENAME,
    SUBSET_DOWNLOADS_FILENAME,
    WEATHER_REQUIREMENTS_FILENAME,
    FinalTestDaymetGridError,
    derive_final_test_daymet_requirements,
    discover_exact_final_test_daymet_granules,
    stage_final_test_daymet_grid,
)
from la_heat.provenance import canonical_frame_sha256, canonical_sha256, sha256_file
from la_heat.weather_daymet import DEFAULT_DAYMET_VARIABLES

CONFIG = Path(__file__).parents[1] / "configs" / "research.toml"


class _Response:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self.payload


class _Client:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, **kwargs: object) -> _Response:
        self.calls.append((url, kwargs))
        return _Response(self.payload)


def _committed(payload: dict[str, Any], path: Path) -> dict[str, Any]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(result)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result), encoding="utf-8")
    return result


def _keys(*dates: str) -> pd.DataFrame:
    records = []
    for target_date in dates:
        for index, geoid in enumerate(("06037000001", "06037000002"), start=1):
            records.append(
                {
                    "tract_geoid": geoid,
                    "target_date": pd.Timestamp(target_date),
                    "overpass_id": f"overpass-{target_date}",
                    "platform": "landsat-9",
                    "spatial_block": f"block-{index}",
                    "latitude_quartile": index,
                    "longitude_quartile": index + 1,
                }
            )
    return pd.DataFrame(records)


def _cmr_entry(variable: str, year: int, number: int) -> dict[str, object]:
    filename = f"daymet_v4_daily_na_{variable}_{year}.nc"
    title = f"Daymet_Daily_V4R1.{filename}"
    return {
        "id": f"G{number}-ORNL_CLOUD",
        "title": title,
        "granule_size": "123.5",
        "updated": "2026-01-01T00:00:00Z",
        "links": [
            {
                "rel": "http://esipfed.org/ns/fedsearch/1.1/data#",
                "href": (
                    "https://data.ornldaac.earthdata.nasa.gov/protected/daymet/"
                    f"Daymet_Daily_V4R1/data/{filename}"
                ),
            },
            {
                "rel": "http://esipfed.org/ns/fedsearch/1.1/service#",
                "href": (
                    "https://opendap.earthdata.nasa.gov/collections/"
                    f"{DAYMET_CMR_COLLECTION_ID}/granules/{title}"
                ),
            },
        ],
    }


def _granule(variable: str, year: int, number: int) -> DaymetGranule:
    entry = _cmr_entry(variable, year, number)
    links = entry["links"]
    assert isinstance(links, list)
    return DaymetGranule(
        concept_id=str(entry["id"]),
        title=str(entry["title"]),
        variable=variable,
        year=year,
        size_mb=123.5,
        https_url=str(links[0]["href"]),
        opendap_url=str(links[1]["href"]),
        updated_at=str(entry["updated"]),
    )


def _all_granules(years: tuple[int, ...]) -> list[DaymetGranule]:
    return [
        _granule(variable, year, number)
        for number, (year, variable) in enumerate(
            (
                (year, variable)
                for year in years
                for variable in development_daymet._normalize_variables(
                    DEFAULT_DAYMET_VARIABLES
                )
            ),
            start=1,
        )
    ]


def _locked_inputs(tmp_path: Path, keys: pd.DataFrame) -> dict[str, Path]:
    config = tmp_path / "configs/research.toml"
    config.parent.mkdir(parents=True)
    config.write_bytes(CONFIG.read_bytes())

    formal_path = tmp_path / "manifests/model_lock/MODEL_LOCK.json"
    formal = _committed(
        {
            "state": "frozen_for_one_time_2025_evaluation",
            "formal_model_lock_written": True,
            "final_test_year": 2025,
            "final_test_locked": True,
            "final_test_unlocked": False,
            "final_test_used": False,
            "final_test_values_read": False,
            "contains_final_test_year": False,
            "one_time_final_evaluation_authorized": False,
            "models": {"B1": {}, "M2": {}},
        },
        formal_path,
    )

    inventory = tmp_path / "manifests/final_test_2025/landsat_inventory"
    key_path = inventory / "target_blind_key_universe.parquet"
    key_path.parent.mkdir(parents=True)
    keys.to_parquet(key_path, index=False)
    key_record = {
        "path": str(key_path),
        "sha256": sha256_file(key_path),
        "bytes": key_path.stat().st_size,
        "rows": len(keys),
    }
    inventory_payload = {
        "state": "target_blind_inventory_frozen",
        "final_test_year": 2025,
        "target_blind": True,
        "target_assets_opened": False,
        "target_or_qa_values_read": False,
        "labels_created": False,
        "models_loaded": False,
        "model_scores_read": False,
        "one_time_evaluation_consumed": False,
        "primary_overpass_count": int(keys["target_date"].nunique()),
        "tract_count": int(keys["tract_geoid"].nunique()),
        "key_count": len(keys),
        "formal_model_lock": {
            "path": str(formal_path),
            "sha256": sha256_file(formal_path),
            "commit_sha256": formal["commit_sha256"],
        },
        "source_records": {
            "research_config": {
                "path": str(config),
                "sha256": sha256_file(config),
            }
        },
        "semantic_hashes": {
            "key_universe": canonical_frame_sha256(
                keys, sort_by=["target_date", "tract_geoid"]
            )
        },
        "output_files": {"target_blind_key_universe.parquet": key_record},
    }
    inventory_summary = inventory / "LANDSAT_INVENTORY.json"
    _committed(inventory_payload, inventory_summary)
    return {
        "config": config,
        "formal": formal_path,
        "inventory": inventory,
        "manifest": tmp_path / "manifests/final_test_2025/daymet_grid",
        "raw": tmp_path / "data/raw/final_test_2025/daymet/subsets",
        "key": key_path,
    }


def _discoverer(granules: list[DaymetGranule]) -> Callable[..., list[DaymetGranule]]:
    def discover(*_args: object, **_kwargs: object) -> list[DaymetGranule]:
        return granules

    return discover


def _spec(
    path: Path,
    *,
    variable: str,
    year: int,
    requirements: object,
) -> DaymetNetCDFSpec:
    del requirements
    crs = rasterio.crs.CRS.from_string(DAYMET_GRID_CRS)
    transform = DAYMET_FULL_GRID_TRANSFORM * rasterio.Affine.translation(2900, 5666)
    return DaymetNetCDFSpec(
        path=path,
        variable=variable,
        year=year,
        subdataset_uri=str(path),
        shape=(80, 64),
        transform=transform,
        crs_wkt=crs.to_wkt(),
        dates=tuple(pd.date_range(f"{year}-01-01", periods=365, freq="D")),
        nodata=-9999.0,
        scales=(1.0,) * 365,
        offsets=(0.0,) * 365,
        units="synthetic",
    )


def test_requirements_are_exact_2025_and_derive_source_years_from_d_minus_7() -> None:
    requirements, membership = derive_final_test_daymet_requirements(
        _keys("2025-01-03", "2025-07-01")
    )

    assert requirements.source_years == (2024, 2025)
    assert len(membership) == 14
    assert set(membership["lag_days"]) == set(range(1, 8))
    assert (
        pd.to_datetime(membership["weather_date"])
        < pd.to_datetime(membership["target_date"])
    ).all()

    with pytest.raises(FinalTestDaymetGridError, match="unique.*2025"):
        derive_final_test_daymet_requirements(_keys("2024-07-01"))
    forbidden = _keys("2025-07-01").assign(target_lst_c=35.0)
    with pytest.raises(FinalTestDaymetGridError, match="only the frozen metadata"):
        derive_final_test_daymet_requirements(forbidden)


def test_exact_discovery_does_not_bypass_the_development_year_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requirements, _ = derive_final_test_daymet_requirements(_keys("2025-07-01"))
    entries = [
        _cmr_entry(variable, 2025, index)
        for index, variable in enumerate(
            development_daymet._normalize_variables(DEFAULT_DAYMET_VARIABLES),
            start=1,
        )
    ]
    client = _Client({"feed": {"entry": entries}})
    monkeypatch.setattr(
        development_daymet,
        "discover_daymet_v4r1_granules",
        lambda **_kwargs: pytest.fail("development discovery must not be called"),
    )

    result = discover_exact_final_test_daymet_granules(
        requirements,
        http_client=client,
    )

    assert {(value.variable, value.year) for value in result} == {
        (variable, 2025) for variable in DEFAULT_DAYMET_VARIABLES
    }
    params = client.calls[0][1]["params"]
    assert isinstance(params, dict)
    assert params["temporal"] == "2025-01-01T00:00:00Z,2025-12-31T23:59:59Z"


def test_inventory_only_authenticates_blind_keys_and_writes_only_manifest_tree(
    tmp_path: Path,
) -> None:
    paths = _locked_inputs(tmp_path, _keys("2025-07-01", "2025-07-17"))
    granules = _all_granules((2025,))

    result = stage_final_test_daymet_grid(
        config_path=paths["config"],
        formal_lock_path=paths["formal"],
        landsat_inventory_directory=paths["inventory"],
        manifest_directory=paths["manifest"],
        raw_subset_directory=paths["raw"],
        discoverer=_discoverer(granules),
    )

    assert result["state"] == "inventory_complete"
    assert result["target_blind"] is True
    assert result["target_or_qa_tables_read"] == []
    assert result["target_values_read"] is False
    assert result["models_loaded"] is False
    assert result["model_scores_read"] is False
    assert result["source_years"] == [2025]
    assert result["required_weather_date_count"] == 14
    assert not paths["raw"].exists()
    assert {path.name for path in paths["manifest"].iterdir()} == {
        PROVENANCE_FILENAME,
        GRANULE_INVENTORY_FILENAME,
        WEATHER_REQUIREMENTS_FILENAME,
    }


def test_tampered_key_fails_before_discovery(tmp_path: Path) -> None:
    paths = _locked_inputs(tmp_path, _keys("2025-07-01"))
    with paths["key"].open("ab") as stream:
        stream.write(b"tampered")

    with pytest.raises(FinalTestDaymetGridError, match="byte lock"):
        stage_final_test_daymet_grid(
            config_path=paths["config"],
            formal_lock_path=paths["formal"],
            landsat_inventory_directory=paths["inventory"],
            manifest_directory=paths["manifest"],
            raw_subset_directory=paths["raw"],
            discoverer=lambda *_args, **_kwargs: pytest.fail(
                "discovery must not run after lock failure"
            ),
        )


def test_download_resume_skips_hash_committed_subsets(tmp_path: Path) -> None:
    paths = _locked_inputs(tmp_path, _keys("2025-07-01"))
    granules = _all_granules((2025,))
    calls: list[str] = []

    def flaky_download(
        url: str,
        destination: Path,
        *,
        credential: EarthdataBearerToken,
        maximum_bytes: int,
    ) -> dict[str, object]:
        assert credential.value == "test-token"
        assert maximum_bytes > 0
        calls.append(destination.name)
        if len(calls) == 2:
            raise ConnectionError("synthetic interruption")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"CDF\x01synthetic-netcdf")
        return {
            "path": str(destination),
            "bytes": destination.stat().st_size,
            "sha256": sha256_file(destination),
            "source_url": url,
            "retrieved_on": "2026-07-23",
            "credential_source": "synthetic",
        }

    common = {
        "config_path": paths["config"],
        "formal_lock_path": paths["formal"],
        "landsat_inventory_directory": paths["inventory"],
        "manifest_directory": paths["manifest"],
        "raw_subset_directory": paths["raw"],
        "discoverer": _discoverer(granules),
        "download_subsets": True,
        "credential": EarthdataBearerToken("test-token", "synthetic"),
        "inspector": _spec,
    }
    with pytest.raises(ConnectionError, match="synthetic interruption"):
        stage_final_test_daymet_grid(**common, downloader=flaky_download)

    partial = json.loads(
        (paths["manifest"] / PROVENANCE_FILENAME).read_text(encoding="utf-8")
    )
    assert partial["state"] == "subsets_partial"
    assert partial["completed_subset_count"] == 1
    assert (paths["manifest"] / SUBSET_DOWNLOADS_FILENAME).is_file()
    first_name = calls[0]

    resumed_calls: list[str] = []

    def finish_download(
        url: str,
        destination: Path,
        *,
        credential: EarthdataBearerToken,
        maximum_bytes: int,
    ) -> dict[str, object]:
        del credential, maximum_bytes
        resumed_calls.append(destination.name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"CDF\x01synthetic-netcdf")
        return {
            "path": str(destination),
            "bytes": destination.stat().st_size,
            "sha256": sha256_file(destination),
            "source_url": url,
            "retrieved_on": "2026-07-23",
            "credential_source": "synthetic",
        }

    complete = stage_final_test_daymet_grid(**common, downloader=finish_download)

    assert complete["state"] == "subsets_complete"
    assert complete["completed_subset_count"] == 6
    assert first_name not in resumed_calls
    assert len(resumed_calls) == 5

    cached = stage_final_test_daymet_grid(
        **{
            key: value
            for key, value in common.items()
            if key != "credential"
        },
        credential_provider=lambda: pytest.fail("completed cache must not request token"),
        downloader=lambda *_args, **_kwargs: pytest.fail(
            "completed cache must not download"
        ),
    )
    assert cached["state"] == "subsets_complete"

