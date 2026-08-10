from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import rasterio

import la_heat.multicity.portable_predictor_daymet as stage
from la_heat.daymet_grid import EarthdataBearerToken
from la_heat.provenance import atomic_json, canonical_sha256, sha256_file
from la_heat.weather_daymet import DEFAULT_DAYMET_VARIABLES


def _write_source_footprint(root: Path, city_id: str) -> None:
    manifest_relative = stage._SOURCE_FOOTPRINT_MANIFESTS[city_id]
    table_relative = Path("source") / city_id / "daymet_granules.parquet"
    table_path = root / table_relative
    table_path.parent.mkdir(parents=True, exist_ok=True)
    records = []
    for index, variable in enumerate(DEFAULT_DAYMET_VARIABLES, start=1):
        title = f"Daymet_Daily_V4R1.daymet_v4_daily_na_{variable}_2025.nc"
        records.append(
            {
                "concept_id": f"G{index}-ORNL_CLOUD",
                "title": title,
                "variable": variable,
                "year": 2025,
                "size_mb": 1.0,
                "updated_at": None,
                "https_url": (
                    "https://data.ornldaac.earthdata.nasa.gov/protected/daymet/"
                    f"Daymet_Daily_V4R1/data/daymet_v4_daily_na_{variable}_2025.nc"
                ),
                "opendap_url": (
                    "https://opendap.earthdata.nasa.gov/collections/"
                    "C2532426483-ORNL_CLOUD/granules/"
                    f"{title}"
                ),
            }
        )
    pd.DataFrame(records).to_parquet(table_path, index=False)
    payload = {
        "state": "test",
        "city": {
            "id": city_id,
            "target_values_status": "sealed",
        },
        "geography_input": {"bbox_wgs84": [-112.0, 33.0, -111.0, 34.0]},
        "source_families": {
            "daymet_cells": {
                "window": {
                    "y_indices_inclusive": [10, 20],
                    "x_indices_inclusive": [30, 40],
                }
            }
        },
        "output_tables": {
            "daymet_granules": {
                "path": table_relative.as_posix(),
                "bytes": table_path.stat().st_size,
                "sha256": sha256_file(table_path),
            }
        },
        "access_contract": {
            "landsat_thermal_values_read": False,
            "landsat_target_qa_values_read": False,
            "external_lst_values_read": False,
        },
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, root / manifest_relative)


def _project_with_source_footprints(tmp_path: Path) -> Path:
    for city_id in stage.EXTERNAL_CITY_IDS:
        _write_source_footprint(tmp_path, city_id)
    return tmp_path


def test_builds_exact_frozen_external_download_tasks(tmp_path: Path) -> None:
    root = _project_with_source_footprints(tmp_path)

    tasks = stage.build_external_download_tasks(root)

    assert len(tasks) == 18
    assert len({task.task_id for task in tasks}) == 18
    assert tuple(task.city_id for task in tasks[::6]) == stage.EXTERNAL_CITY_IDS
    assert tuple(task.variable for task in tasks[:6]) == DEFAULT_DAYMET_VARIABLES
    assert all(task.year == 2025 for task in tasks)
    assert all("dap4.ce=" in task.source_url for task in tasks)
    assert all(task.destination.is_relative_to(root) for task in tasks)


def test_missing_downloads_report_credential_gap_with_ui_record(
    tmp_path: Path, monkeypatch
) -> None:
    root = _project_with_source_footprints(tmp_path)
    monkeypatch.setattr(stage, "completed_external_download_tasks", lambda _root: ())
    progress: list[dict[str, object]] = []

    result = stage.download_missing_external_subsets(
        root,
        credential=None,
        progress_callback=progress.append,
    )

    assert result["state"] == "credential_required"
    assert result["complete"] is False
    assert result["completed"] == 0
    assert result["total"] == 18
    assert result["remaining"] == 18
    assert progress
    assert set(progress[-1]) == {
        "city_id",
        "variable",
        "completed",
        "total",
        "message",
        "task_complete",
    }
    assert progress[-1]["task_complete"] is False


def test_downloader_pauses_after_one_atomic_file(
    tmp_path: Path, monkeypatch
) -> None:
    root = _project_with_source_footprints(tmp_path)
    tasks = stage.build_external_download_tasks(root)
    monkeypatch.setattr(stage, "completed_external_download_tasks", lambda _root: ())
    monkeypatch.setattr(stage, "_validate_downloaded_task", lambda _task, _path: None)

    def fake_download(_url, destination, **_kwargs):
        path = Path(destination)
        assert path.suffix == ".nc"
        assert path.name.endswith(".validating.nc")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"netcdf-test")
        return {"path": path.as_posix()}

    monkeypatch.setattr(stage, "authenticated_netcdf_download", fake_download)
    pause_checks = iter((False, True))

    progress: list[dict[str, object]] = []
    result = stage.download_missing_external_subsets(
        root,
        credential=EarthdataBearerToken("secret", "test_token"),
        progress_callback=progress.append,
        pause_callback=lambda: next(pause_checks),
    )

    assert result["state"] == "incomplete"
    assert result["complete"] is False
    assert result["paused"] is True
    assert result["completed"] == 1
    assert result["downloaded"] == 1
    assert tasks[0].destination.read_bytes() == b"netcdf-test"
    validating = tasks[0].destination.with_name(
        f"{tasks[0].destination.stem}.validating{tasks[0].destination.suffix}"
    )
    assert not validating.exists()
    assert not tasks[1].destination.exists()
    assert "secret" not in repr(result)
    assert progress[-1]["task_complete"] is True


def test_los_angeles_reuses_thirty_existing_subsets(tmp_path: Path) -> None:
    directory = tmp_path / stage.LOS_ANGELES_SUBSET_ROOT
    directory.mkdir(parents=True)
    for year in stage.LOS_ANGELES_YEARS:
        for variable in DEFAULT_DAYMET_VARIABLES:
            (directory / f"daymet_v4r1_daily_na_{variable}_{year}_la_subset.nc").touch()

    records = stage._subset_records(tmp_path, "los_angeles_ca")

    assert len(records) == 30
    assert set(records["year"]) == set(stage.LOS_ANGELES_YEARS)
    assert set(records["variable"]) == set(DEFAULT_DAYMET_VARIABLES)


def test_compile_city_publishes_four_atomic_component_tables(
    tmp_path: Path, monkeypatch
) -> None:
    city_id = "phoenix_az"
    key_path = tmp_path / stage.INVENTORY_ROOT / city_id / "predictor_keys.parquet"
    key_path.parent.mkdir(parents=True)
    keys = pd.DataFrame(
        {
            "city_id": [city_id, city_id],
            "tract_geoid": ["a", "b"],
            "target_date": pd.to_datetime(["2025-07-01", "2025-07-01"]),
        }
    )
    keys.to_parquet(key_path, index=False)
    support = stage._SupportView(
        zones=np.array([[1, 2]], dtype=np.int32),
        eligible_land=np.array([[True, True]]),
        tract_geoids=("a", "b"),
        transform=rasterio.Affine.identity(),
        crs="EPSG:32612",
    )
    monkeypatch.setattr(stage, "_load_city_support", lambda _root, _city: support)
    monkeypatch.setattr(
        stage,
        "_subset_records",
        lambda _root, _city: pd.DataFrame(
            {"path": [tmp_path / "fake.nc"], "variable": ["tmax"], "year": [2025]}
        ),
    )
    observed: dict[str, object] = {}

    def fake_compile(_records, feature_keys, **kwargs):
        observed.update(kwargs)
        feature_keys = feature_keys.copy()
        return SimpleNamespace(
            features=feature_keys.assign(daymet_tmax_c_mean_prev_1d=1.0),
            audit=feature_keys.assign(daymet_source_days_complete=7),
            weights=pd.DataFrame(
                {"tract_geoid": ["a", "b"], "daymet_cell_id": ["x", "y"]}
            ),
            tract_daily=pd.DataFrame(
                {
                    "tract_geoid": ["a", "b"],
                    "date": pd.to_datetime(["2025-06-30", "2025-06-30"]),
                }
            ),
        )

    monkeypatch.setattr(stage, "compile_daymet_feature_tables", fake_compile)

    result = stage.compile_city_daymet(tmp_path, city_id)

    assert result["state"] == "complete"
    assert result["row_count"] == 2
    assert observed["final_test_year"] == 2026
    assert observed["windows"] == (1, 3, 7)
    paths = result["paths"]
    assert set(paths) == {"features", "audit", "weights", "tract_daily"}
    assert all(path.is_file() for path in paths.values())
    assert len(pd.read_parquet(paths["features"])) == 2
