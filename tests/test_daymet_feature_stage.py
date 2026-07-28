from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from rasterio.transform import from_origin
from scipy.io import netcdf_file

import la_heat.daymet_feature_stage as stage
from la_heat.daymet_feature_stage import (
    DAYMET_AUDIT_FILENAME,
    DAYMET_FEATURE_FILENAME,
    DAYMET_PRIMARY_WINDOWS,
    DAYMET_PROVENANCE_FILENAME,
    DAYMET_WEIGHTS_FILENAME,
    build_daymet_feature_artifacts,
    compile_daymet_feature_tables,
)
from la_heat.daymet_grid import DAYMET_GRID_CRS, DaymetGridAuditError
from la_heat.phase2_registry import daymet_feature_registry_rows
from la_heat.provenance import canonical_sha256

CONFIG = Path(__file__).parents[1] / "configs" / "research.toml"
GEOIDS = ("06037101110", "06037101120")
UNITS = {
    "dayl": "s/day",
    "prcp": "mm/day",
    "srad": "W/m^2",
    "tmax": "degrees C",
    "tmin": "degrees C",
    "vp": "Pa",
}


def _locked_config_copy(tmp_path: Path) -> Path:
    config_path = tmp_path / "configs" / "research.toml"
    config_path.parent.mkdir(parents=True)
    payload = CONFIG.read_bytes()
    unlocked_setting = b"unlock_final_test = true"
    assert payload.count(unlocked_setting) == 1
    config_path.write_bytes(
        payload.replace(unlocked_setting, b"unlock_final_test = false")
    )
    return config_path


def _write_variable(path: Path, variable: str, *, year: int = 2023) -> None:
    with netcdf_file(path, "w") as dataset:
        dataset.Conventions = "CF-1.6"
        dataset.createDimension("time", 365)
        dataset.createDimension("y", 2)
        dataset.createDimension("x", 2)
        x = dataset.createVariable("x", "f8", ("x",))
        x.standard_name = "projection_x_coordinate"
        x.units = "m"
        x[:] = np.array([500.0, 1500.0])
        y = dataset.createVariable("y", "f8", ("y",))
        y.standard_name = "projection_y_coordinate"
        y.units = "m"
        y[:] = np.array([1500.0, 500.0])
        time = dataset.createVariable("time", "f8", ("time",))
        time.standard_name = "time"
        time.units = f"days since {year:04d}-01-01 00:00:00"
        time.calendar = "standard"
        time[:] = np.arange(365, dtype=float)
        projection = dataset.createVariable("lambert_conformal_conic", "i4", ())
        projection.grid_mapping_name = "lambert_conformal_conic"
        projection.standard_parallel = np.array([25.0, 60.0])
        projection.longitude_of_central_meridian = -100.0
        projection.latitude_of_projection_origin = 42.5
        projection.false_easting = 0.0
        projection.false_northing = 0.0
        projection.semi_major_axis = 6_378_137.0
        projection.inverse_flattening = 298.257223563
        projection[...] = np.int32(0)
        output = dataset.createVariable(variable, "f4", ("time", "y", "x"))
        output.units = UNITS[variable]
        output.grid_mapping = "lambert_conformal_conic"
        output._FillValue = np.float32(-9999.0)
        day = np.arange(365, dtype=np.float32)[:, None, None]
        if variable == "tmax":
            values = np.broadcast_to(20.0 + day, (365, 2, 2)).copy()
        elif variable == "tmin":
            values = np.broadcast_to(10.0 + day, (365, 2, 2)).copy()
        elif variable == "prcp":
            values = np.ones((365, 2, 2), dtype=np.float32)
        elif variable == "srad":
            values = np.full((365, 2, 2), 100.0, dtype=np.float32)
        elif variable == "vp":
            values = np.full((365, 2, 2), 1000.0, dtype=np.float32)
        else:
            values = np.full((365, 2, 2), 36_000.0, dtype=np.float32)
        output[:] = values


def _subset_records(tmp_path: Path) -> pd.DataFrame:
    tmp_path.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    for variable in sorted(UNITS):
        path = tmp_path / f"daymet_v4r1_daily_na_{variable}_2023_la_subset.nc"
        _write_variable(path, variable)
        records.append({"path": path, "variable": variable, "year": 2023})
    return pd.DataFrame(records)


def _keys() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "tract_geoid": pd.Series(
                [GEOIDS[0], GEOIDS[1], GEOIDS[0], GEOIDS[1]], dtype="string"
            ),
            "target_date": pd.to_datetime(
                ["2023-01-08", "2023-01-08", "2023-01-09", "2023-01-09"]
            ),
        }
    )


def _compile(tmp_path: Path):
    return compile_daymet_feature_tables(
        _subset_records(tmp_path),
        _keys(),
        zone_raster=np.array([[1, 1], [2, 2]], dtype=np.int32),
        eligible_land_mask=np.ones((2, 2), dtype=bool),
        tract_geoids=GEOIDS,
        target_transform=from_origin(0, 2000, 1000, 1000),
        target_crs=DAYMET_GRID_CRS,
    )


def test_compile_daymet_features_is_exact_target_blind_key_table(tmp_path: Path) -> None:
    result = _compile(tmp_path)
    names = tuple(daymet_feature_registry_rows()["feature_name"].astype(str))

    assert list(result.features.columns) == ["tract_geoid", "target_date", *names]
    assert len(result.features) == 4
    assert result.features[list(names)].notna().all().all()
    first = result.features.loc[result.features["target_date"] == "2023-01-08"].iloc[0]
    assert first["daymet_tmax_c_mean_prev_1d"] == pytest.approx(26.0)
    assert first["daymet_prcp_mm_sum_prev_7d"] == pytest.approx(7.0)
    assert first["daymet_srad_energy_mj_m2_sum_prev_7d"] == pytest.approx(25.2)

    assert not set(names).intersection(result.audit.columns)
    assert result.audit["daymet_source_end_date"].equals(
        result.audit["target_date"] - pd.Timedelta(days=1)
    )
    assert result.audit["daymet_source_days_complete"].eq(7).all()
    assert result.audit["daymet_all_primary_windows_complete"].all()
    for window_days in DAYMET_PRIMARY_WINDOWS:
        suffix = f"prev_{window_days}d"
        assert result.audit[f"daymet_source_start_date_{suffix}"].equals(
            result.audit["target_date"] - pd.Timedelta(days=window_days)
        )
        assert result.audit[f"daymet_source_end_date_{suffix}"].equals(
            result.audit["target_date"] - pd.Timedelta(days=1)
        )
        assert result.audit[f"daymet_source_days_expected_{suffix}"].eq(
            window_days
        ).all()
        assert result.audit[f"daymet_source_days_complete_{suffix}"].eq(
            window_days
        ).all()
    assert result.weights.groupby("tract_geoid")["static_denominator_m2"].nunique().eq(1).all()
    assert result.weights.groupby("tract_geoid")["static_denominator_m2"].first().eq(
        2_000_000.0
    ).all()


def test_compile_daymet_features_rejects_locked_target_date(tmp_path: Path) -> None:
    keys = _keys()
    keys.loc[0, "target_date"] = pd.Timestamp("2025-01-08")
    with pytest.raises(PermissionError, match="locked year 2025"):
        compile_daymet_feature_tables(
            _subset_records(tmp_path),
            keys,
            zone_raster=np.array([[1, 1], [2, 2]], dtype=np.int32),
            eligible_land_mask=np.ones((2, 2), dtype=bool),
            tract_geoids=GEOIDS,
            target_transform=from_origin(0, 2000, 1000, 1000),
            target_crs=DAYMET_GRID_CRS,
        )


def test_stage_writes_feature_only_audit_weights_and_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = _subset_records(tmp_path / "subsets")
    universe = tmp_path / "feature_key_universe.parquet"
    _keys().to_parquet(universe, index=False)
    output = tmp_path / "output"
    target = SimpleNamespace(
        tracts=pd.DataFrame({"GEOID": GEOIDS}),
        zones=np.array([[1, 1], [2, 2]], dtype=np.int32),
        eligible_land=np.ones((2, 2), dtype=bool),
        grid=SimpleNamespace(
            transform=from_origin(0, 2000, 1000, 1000), crs=DAYMET_GRID_CRS
        ),
        locks={"synthetic_target_support": True},
    )
    monkeypatch.setattr(stage, "EXPECTED_PRODUCTION_ROWS", 4)
    monkeypatch.setattr(stage, "EXPECTED_PRODUCTION_DATES", 2)
    monkeypatch.setattr(stage, "EXPECTED_PRODUCTION_TRACTS", 2)
    monkeypatch.setattr(
        stage,
        "load_verified_daymet_subset_records",
        lambda config, project_root: (records, {"synthetic_downloads": True}),
    )
    monkeypatch.setattr(
        stage, "_target_support_from_config", lambda config, project_root: target
    )

    payload = build_daymet_feature_artifacts(
        _locked_config_copy(tmp_path),
        universe,
        output,
    )

    assert payload["row_count"] == 4
    assert payload["feature_count"] == 21
    assert payload["target_blind"] is True
    assert payload["target_or_qa_tables_read"] == []
    assert payload["target_or_qa_value_columns_read"] == []
    assert payload["source_end_offset_days"] == -1
    assert payload["latest_source_offset_days"] == -1
    assert (output / DAYMET_FEATURE_FILENAME).is_file()
    assert (output / DAYMET_AUDIT_FILENAME).is_file()
    assert (output / DAYMET_WEIGHTS_FILENAME).is_file()
    marker = json.loads((output / DAYMET_PROVENANCE_FILENAME).read_text(encoding="utf-8"))
    commit = marker.pop("commit_sha256")
    assert canonical_sha256(marker) == commit


def test_primary_windows_cannot_drift(tmp_path: Path) -> None:
    with pytest.raises(DaymetGridAuditError, match="locked to 1, 3, and 7"):
        compile_daymet_feature_tables(
            _subset_records(tmp_path),
            _keys(),
            zone_raster=np.array([[1, 1], [2, 2]], dtype=np.int32),
            eligible_land_mask=np.ones((2, 2), dtype=bool),
            tract_geoids=GEOIDS,
            target_transform=from_origin(0, 2000, 1000, 1000),
            target_crs=DAYMET_GRID_CRS,
            windows=(1, 3, 9),
        )


def test_interactive_prompt_is_a_valid_nonsecret_credential_provenance_label() -> None:
    assert "interactive_prompt" in stage.DAYMET_ALLOWED_CREDENTIAL_SOURCES
    assert "not_reloaded_from_cache" in stage.DAYMET_ALLOWED_CREDENTIAL_SOURCES
    assert "token" not in stage.DAYMET_ALLOWED_CREDENTIAL_SOURCES
