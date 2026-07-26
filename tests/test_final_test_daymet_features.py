from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import rasterio

import la_heat.final_test_daymet_features as final_stage
from la_heat.daymet_grid import (
    DAYMET_VARIABLES,
    DaymetGridAuditError,
    DaymetNetCDFSpec,
)
from la_heat.final_test_daymet_features import (
    FinalTestDaymetFeatureError,
    _locked_daymet_feature_names,
    compile_final_test_daymet_feature_tables,
    verify_locked_subset_files,
)
from la_heat.phase2_registry import (
    LOCKED_DAYMET_VARIABLES,
    daymet_feature_registry_rows,
)
from la_heat.provenance import (
    atomic_json,
    canonical_sha256,
    parquet_file_record,
    sha256_file,
)

GEOIDS = ("06037101110", "06037101120")
TARGET_DATES = (pd.Timestamp("2025-01-08"), pd.Timestamp("2025-01-09"))


def _keys() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for date_index, target_date in enumerate(TARGET_DATES, start=1):
        for geoid_index, geoid in enumerate(GEOIDS, start=1):
            rows.append(
                {
                    "tract_geoid": geoid,
                    "target_date": target_date,
                    "overpass_id": f"landsat-9_{date_index}",
                    "platform": "landsat-9",
                    "spatial_block": f"block-{geoid_index}",
                    "latitude_quartile": geoid_index,
                    "longitude_quartile": geoid_index,
                }
            )
    return pd.DataFrame(rows)


def _weights() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "tract_geoid": GEOIDS,
            "daymet_cell_id": ("cell-1", "cell-2"),
            "daymet_row": (0, 1),
            "daymet_col": (0, 0),
            "daymet_x_m": (500.0, 500.0),
            "daymet_y_m": (1500.0, 500.0),
            "eligible_pixel_count": (1, 1),
            "eligible_area_m2": (900.0, 900.0),
            "static_denominator_m2": (900.0, 900.0),
            "weight": (1.0, 1.0),
        }
    )


def _records(tmp_path: Path) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "path": tmp_path / f"{variable}.nc",
                "variable": variable,
                "year": 2025,
            }
            for variable in LOCKED_DAYMET_VARIABLES
        ]
    )


def _specs(tmp_path: Path) -> tuple[DaymetNetCDFSpec, ...]:
    dates = tuple(pd.Timestamp(value) for value in pd.date_range("2025-01-01", periods=365))
    transform = rasterio.Affine(1000.0, 0.0, 0.0, 0.0, -1000.0, 2000.0)
    crs_wkt = rasterio.crs.CRS.from_epsg(32611).to_wkt()
    return tuple(
        DaymetNetCDFSpec(
            path=tmp_path / f"{variable}.nc",
            variable=variable,
            year=2025,
            subdataset_uri=f"synthetic:{variable}",
            shape=(2, 1),
            transform=transform,
            crs_wkt=crs_wkt,
            dates=dates,
            nodata=-9999.0,
            scales=(1.0,) * 365,
            offsets=(0.0,) * 365,
            units=DAYMET_VARIABLES[variable].units,
        )
        for variable in LOCKED_DAYMET_VARIABLES
    )


def _reader(*, changed_dates: set[pd.Timestamp] | None = None):
    changed = changed_dates or set()
    base_values = {
        "dayl": 36_000.0,
        "prcp": 1.0,
        "srad": 100.0,
        "tmax": 30.0,
        "tmin": 20.0,
        "vp": 1_000.0,
    }

    def read(spec: DaymetNetCDFSpec, *, cells: pd.DataFrame) -> pd.DataFrame:
        rows: list[dict[str, object]] = []
        column = DAYMET_VARIABLES[spec.variable].column
        for date in spec.dates:
            for cell in cells.itertuples(index=False):
                value = base_values[spec.variable]
                if spec.variable == "prcp" and pd.Timestamp(date) in changed:
                    value = 10_000.0
                rows.append(
                    {
                        "daymet_cell_id": str(cell.daymet_cell_id),
                        "date": pd.Timestamp(date),
                        column: value,
                    }
                )
        return pd.DataFrame(rows)

    return read


def _compile(tmp_path: Path, *, changed_dates: set[pd.Timestamp] | None = None):
    return compile_final_test_daymet_feature_tables(
        _records(tmp_path),
        _keys(),
        _weights(),
        specs=_specs(tmp_path),
        cell_reader=_reader(changed_dates=changed_dates),
    )


def test_compile_is_exact_21_feature_table_with_d_minus_one_lineage(
    tmp_path: Path,
) -> None:
    result = _compile(tmp_path)
    names = tuple(daymet_feature_registry_rows()["feature_name"].astype(str))

    assert list(result.features.columns) == ["tract_geoid", "target_date", *names]
    assert result.features.shape == (4, 23)
    assert result.features[list(names)].notna().all().all()
    assert result.audit["daymet_all_primary_windows_complete"].all()
    for window in (1, 3, 7):
        suffix = f"prev_{window}d"
        assert pd.to_datetime(result.audit[f"daymet_source_end_date_{suffix}"]).equals(
            result.audit["target_date"] - pd.Timedelta(days=1)
        )
        assert pd.to_datetime(result.audit[f"daymet_source_start_date_{suffix}"]).equals(
            result.audit["target_date"] - pd.Timedelta(days=window)
        )


@pytest.mark.parametrize("table_name", ("features", "audit"))
def test_compiled_tables_accept_only_semantic_string_dtype_round_trip(
    tmp_path: Path,
    table_name: str,
) -> None:
    compilation = _compile(tmp_path)
    source = getattr(compilation, table_name)
    parquet_path = tmp_path / f"{table_name}.parquet"
    source.to_parquet(parquet_path, index=False)
    frozen = pd.read_parquet(parquet_path)

    assert source["tract_geoid"].dtype == np.dtype("object")
    assert isinstance(frozen["tract_geoid"].dtype, pd.StringDtype)
    final_stage._assert_parquet_round_trip(
        frozen,
        source,
        label=f"synthetic {table_name}",
    )


@pytest.mark.parametrize("change", ("numeric_dtype", "numeric_value"))
def test_parquet_round_trip_keeps_numeric_dtypes_and_values_strict(
    change: str,
) -> None:
    source = pd.DataFrame(
        {
            "tract_geoid": pd.Series(["06037101110"], dtype=object),
            "daymet_value": pd.Series([1.0], dtype="float64"),
        }
    )
    frozen = source.copy()
    frozen["tract_geoid"] = frozen["tract_geoid"].astype("str")
    if change == "numeric_dtype":
        frozen["daymet_value"] = frozen["daymet_value"].astype("float32")
    else:
        frozen.loc[0, "daymet_value"] = np.nextafter(1.0, 2.0)

    with pytest.raises(FinalTestDaymetFeatureError, match="Parquet round trip"):
        final_stage._assert_parquet_round_trip(
            frozen,
            source,
            label="synthetic feature table",
        )


def test_target_day_and_future_values_cannot_change_target_features(
    tmp_path: Path,
) -> None:
    baseline = _compile(tmp_path).features
    changed = _compile(
        tmp_path,
        changed_dates={pd.Timestamp("2025-01-08"), pd.Timestamp("2025-01-10")},
    ).features
    first_date = baseline["target_date"].eq(pd.Timestamp("2025-01-08"))
    second_date = baseline["target_date"].eq(pd.Timestamp("2025-01-09"))

    pd.testing.assert_frame_equal(
        baseline.loc[first_date].reset_index(drop=True),
        changed.loc[first_date].reset_index(drop=True),
    )
    assert not np.array_equal(
        baseline.loc[second_date, ["daymet_prcp_mm_sum_prev_1d"]].to_numpy(),
        changed.loc[second_date, ["daymet_prcp_mm_sum_prev_1d"]].to_numpy(),
    )


def test_key_schema_rejects_target_or_qa_columns_before_read(
    tmp_path: Path,
) -> None:
    keys = _keys()
    keys["target_lst_c"] = 40.0
    called = False

    def forbidden_reader(spec: DaymetNetCDFSpec, *, cells: pd.DataFrame) -> pd.DataFrame:
        nonlocal called
        called = True
        raise AssertionError((spec, cells))

    with pytest.raises(FinalTestDaymetFeatureError, match="schema"):
        compile_final_test_daymet_feature_tables(
            _records(tmp_path),
            keys,
            _weights(),
            specs=_specs(tmp_path),
            cell_reader=forbidden_reader,
        )
    assert called is False


def test_grid_specs_must_share_one_fixed_grid(tmp_path: Path) -> None:
    specs = list(_specs(tmp_path))
    original = specs[-1]
    specs[-1] = DaymetNetCDFSpec(
        path=original.path,
        variable=original.variable,
        year=original.year,
        subdataset_uri=original.subdataset_uri,
        shape=original.shape,
        transform=original.transform * rasterio.Affine.translation(1, 0),
        crs_wkt=original.crs_wkt,
        dates=original.dates,
        nodata=original.nodata,
        scales=original.scales,
        offsets=original.offsets,
        units=original.units,
    )
    with pytest.raises(DaymetGridAuditError, match="fixed native grid"):
        compile_final_test_daymet_feature_tables(
            _records(tmp_path),
            _keys(),
            _weights(),
            specs=specs,
            cell_reader=_reader(),
        )


def _download_manifest(tmp_path: Path) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for variable in LOCKED_DAYMET_VARIABLES:
        path = tmp_path / f"daymet_v4r1_daily_na_{variable}_2025_la_subset.nc"
        path.write_bytes(f"netcdf-{variable}".encode())
        content = path.read_bytes()
        rows.append(
            {
                "concept_id": f"concept-{variable}",
                "variable": variable,
                "year": 2025,
                "access_route": "direct_dap4_fixed_indices_v1",
                "subset_y_start": 1,
                "subset_y_stop": 2,
                "subset_x_start": 3,
                "subset_x_stop": 4,
                "path": str(path),
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "source_url": f"https://example.invalid/{variable}",
                "retrieved_on": "2026-07-23",
                "credential_source": "interactive_prompt",
            }
        )
    return pd.DataFrame(rows)


def test_subset_files_fail_closed_on_hash_or_missing_file(tmp_path: Path) -> None:
    downloads = _download_manifest(tmp_path)
    records = verify_locked_subset_files(
        downloads,
        raw_subset_directory=tmp_path,
    )
    assert len(records) == 6

    tampered = Path(str(downloads.iloc[0]["path"]))
    tampered.write_bytes(b"tampered")
    with pytest.raises(FinalTestDaymetFeatureError, match="byte-size|SHA-256"):
        verify_locked_subset_files(downloads, raw_subset_directory=tmp_path)

    downloads = _download_manifest(tmp_path)
    missing = Path(str(downloads.iloc[-1]["path"]))
    missing.unlink()
    with pytest.raises(FileNotFoundError, match="Missing frozen 2025 Daymet subset"):
        verify_locked_subset_files(downloads, raw_subset_directory=tmp_path)


def test_formal_b1_m2_daymet_feature_order_must_match_registry() -> None:
    names = list(daymet_feature_registry_rows()["feature_name"].astype(str))
    formal = {
        "models": {
            "B1": {"feature_names": ["calendar_doy_sin", "calendar_doy_cos", *names]},
            "M2": {"feature_names": ["static", *names, "sentinel_ndvi_lag60"]},
        }
    }
    assert _locked_daymet_feature_names(formal) == tuple(names)

    formal["models"]["M2"]["feature_names"] = ["static", *names[:-1]]
    with pytest.raises(FinalTestDaymetFeatureError, match="M2 Daymet feature order"):
        _locked_daymet_feature_names(formal)


def _staged_publication(
    tmp_path: Path,
) -> tuple[
    Path,
    Path,
    Path,
    dict[str, object],
    dict[str, object],
    str,
    dict[str, object],
]:
    output = tmp_path / "daymet-output"
    marker = tmp_path / "manifests" / "DAYMET_FEATURES.json"
    staging = output.parent / f".{output.name}.staging-test"
    staging.mkdir()
    feature_frame = pd.DataFrame(
        {
            "tract_geoid": ["06037101110"],
            "target_date": [pd.Timestamp("2025-01-08")],
            "daymet_test": [1.0],
        }
    )
    audit_frame = pd.DataFrame(
        {
            "tract_geoid": ["06037101110"],
            "target_date": [pd.Timestamp("2025-01-08")],
            "daymet_all_primary_windows_complete": [True],
        }
    )
    staged_feature = staging / final_stage.OUTPUT_FILENAME
    staged_audit = staging / final_stage.AUDIT_FILENAME
    feature_frame.to_parquet(staged_feature, index=False)
    audit_frame.to_parquet(staged_audit, index=False)
    immutable = tmp_path / "frozen-input.bin"
    immutable.write_bytes(b"frozen")
    immutable_record = {
        "path": str(immutable),
        "sha256": sha256_file(immutable),
        "bytes": immutable.stat().st_size,
    }
    request = final_stage._request_payload(
        formal_path=(tmp_path / "formal.json").resolve(),
        inventory_directory=(tmp_path / "inventory").resolve(),
        manifest_directory=(tmp_path / "daymet-grid").resolve(),
        output=output.resolve(),
        marker=marker.resolve(),
        inspector=final_stage.inspect_exact_final_test_daymet_netcdf,
        cell_reader=final_stage.read_daymet_netcdf_cells,
    )
    pipeline_sha256 = "a" * 64
    pipeline = {"algorithm_version": "synthetic-pipeline"}
    payload: dict[str, object] = {
        "schema_version": 1,
        "algorithm_version": final_stage.ALGORITHM_VERSION,
        "state": "complete_target_blind",
        "final_test_year": 2025,
        "target_blind": True,
        "target_or_qa_tables_read": [],
        "target_values_read": False,
        "models_loaded": False,
        "model_scores_read": False,
        "one_time_evaluation_consumed": False,
        "publication_protocol": "staged_directory_atomic_replace_v1",
        "request": request,
        "request_sha256": canonical_sha256(request),
        "immutable_input_files": [immutable_record],
        "pipeline_sha256": pipeline_sha256,
        "pipeline_fingerprint": pipeline,
        "output_files": {
            final_stage.OUTPUT_FILENAME: {
                "path": str(output / final_stage.OUTPUT_FILENAME),
                **parquet_file_record(staged_feature, feature_frame),
            },
            final_stage.AUDIT_FILENAME: {
                "path": str(output / final_stage.AUDIT_FILENAME),
                **parquet_file_record(staged_audit, audit_frame),
            },
        },
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, staging / final_stage.INTERNAL_PROVENANCE_FILENAME)
    return (
        staging,
        output,
        marker,
        request,
        payload,
        pipeline_sha256,
        pipeline,
    )


def test_atomic_publish_recovers_after_external_marker_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        staging,
        output,
        marker,
        request,
        payload,
        pipeline_sha256,
        pipeline,
    ) = _staged_publication(tmp_path)
    monkeypatch.setattr(
        final_stage,
        "_current_pipeline",
        lambda root: (pipeline_sha256, pipeline),
    )

    def fail_marker(payload: dict[str, object], path: Path) -> None:
        raise OSError((payload, path))

    with pytest.raises(OSError):
        final_stage._publish_staged_output(
            staging,
            output,
            payload,
            marker,
            marker_writer=fail_marker,
        )
    assert not staging.exists()
    assert not marker.exists()
    assert (output / final_stage.OUTPUT_FILENAME).is_file()
    assert (output / final_stage.AUDIT_FILENAME).is_file()
    assert (output / final_stage.INTERNAL_PROVENANCE_FILENAME).is_file()

    recovered = final_stage._recover_published_output(
        output,
        marker,
        expected_request=request,
        root=tmp_path,
    )
    assert marker.is_file()
    assert recovered["commit_sha256"] == payload["commit_sha256"]


def test_existing_marker_rejects_different_cli_request_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        staging,
        output,
        marker,
        request,
        payload,
        pipeline_sha256,
        pipeline,
    ) = _staged_publication(tmp_path)
    monkeypatch.setattr(
        final_stage,
        "_current_pipeline",
        lambda root: (pipeline_sha256, pipeline),
    )
    final_stage._publish_staged_output(staging, output, payload, marker)
    assert (
        final_stage._authenticate_existing(
            marker,
            expected_request=request,
            root=tmp_path,
        )
        is not None
    )

    for field in (
        "formal_model_lock_path",
        "landsat_inventory_directory",
        "daymet_grid_path",
        "daymet_subset_manifest_path",
        "output_directory",
    ):
        mismatched = dict(request)
        mismatched[field] = f"{mismatched[field]}.other"
        with pytest.raises(FinalTestDaymetFeatureError, match="another request"):
            final_stage._authenticate_existing(
                marker,
                expected_request=mismatched,
                root=tmp_path,
            )


def test_prepublication_snapshot_rechecks_inputs_and_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen = tmp_path / "frozen.bin"
    frozen.write_bytes(b"first")
    record = {
        "path": str(frozen),
        "sha256": sha256_file(frozen),
        "bytes": frozen.stat().st_size,
    }
    pipeline_sha256 = "b" * 64
    pipeline = {"algorithm_version": "pipeline-a"}
    monkeypatch.setattr(
        final_stage,
        "_current_pipeline",
        lambda root: (pipeline_sha256, pipeline),
    )
    final_stage._verify_publish_snapshot(
        [record],
        pipeline_sha256=pipeline_sha256,
        pipeline_fingerprint=pipeline,
        root=tmp_path,
    )

    frozen.write_bytes(b"other")
    with pytest.raises(FinalTestDaymetFeatureError, match="SHA-256"):
        final_stage._verify_publish_snapshot(
            [record],
            pipeline_sha256=pipeline_sha256,
            pipeline_fingerprint=pipeline,
            root=tmp_path,
        )

    frozen.write_bytes(b"first")
    monkeypatch.setattr(
        final_stage,
        "_current_pipeline",
        lambda root: ("c" * 64, {"algorithm_version": "pipeline-b"}),
    )
    with pytest.raises(FinalTestDaymetFeatureError, match="pipeline changed"):
        final_stage._verify_publish_snapshot(
            [record],
            pipeline_sha256=pipeline_sha256,
            pipeline_fingerprint=pipeline,
            root=tmp_path,
        )


def test_publish_rejects_staged_parquet_tampering(tmp_path: Path) -> None:
    staging, output, marker, _, payload, _, _ = _staged_publication(tmp_path)
    feature_path = staging / final_stage.OUTPUT_FILENAME
    with feature_path.open("ab") as handle:
        handle.write(b"tampered")

    with pytest.raises(FinalTestDaymetFeatureError, match="byte-size|SHA-256"):
        final_stage._publish_staged_output(staging, output, payload, marker)
    assert staging.is_dir()
    assert not output.exists()
    assert not marker.exists()


def test_build_rejects_pipeline_change_during_compile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    formal_path = tmp_path / "formal.json"
    inventory_directory = tmp_path / "inventory"
    manifest_directory = tmp_path / "daymet-grid"
    output = tmp_path / "output"
    marker = tmp_path / "provenance" / "DAYMET_FEATURES.json"
    inventory_directory.mkdir()
    manifest_directory.mkdir()
    formal_path.write_bytes(b"formal")
    inventory_path = inventory_directory / final_stage.LANDSAT_PROVENANCE_FILENAME
    inventory_path.write_bytes(b"inventory")
    key_path = inventory_directory / final_stage.KEY_UNIVERSE_FILENAME
    key_path.write_bytes(b"keys")
    grid_path = manifest_directory / final_stage.GRID_PROVENANCE_FILENAME
    grid_path.write_bytes(b"grid")

    def locked(path: Path) -> dict[str, object]:
        path.write_bytes(path.name.encode())
        return {
            "path": str(path),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }

    grid_files = [
        locked(tmp_path / "granules.csv"),
        locked(tmp_path / "requirements.csv"),
        locked(tmp_path / "subsets.csv"),
    ]
    subset_files = [locked(tmp_path / "subset.nc")]
    chain_record = locked(tmp_path / "phase2.json")
    daymet_record = locked(tmp_path / "daymet-development.json")
    weights_record = locked(tmp_path / "weights.parquet")
    key_record = {
        "path": str(key_path),
        "sha256": sha256_file(key_path),
        "bytes": key_path.stat().st_size,
    }
    keys = _keys().iloc[:1].copy()
    features = keys.loc[:, ["tract_geoid", "target_date"]].copy()
    features["daymet_x"] = 1.0
    compilation = final_stage.FinalTestDaymetCompilation(
        features=features,
        audit=pd.DataFrame(),
        tract_daily=pd.DataFrame(),
    )
    compile_called = False

    def compile_stub(*args: object, **kwargs: object):
        nonlocal compile_called
        compile_called = True
        return compilation

    pipeline_a = ("a" * 64, {"algorithm_version": "pipeline-a"})
    pipeline_b = ("b" * 64, {"algorithm_version": "pipeline-b"})
    pipeline_states = iter((pipeline_a, pipeline_b))
    monkeypatch.setattr(
        final_stage,
        "_current_pipeline",
        lambda root: next(pipeline_states),
    )
    monkeypatch.setattr(
        final_stage,
        "authenticate_formal_model_lock",
        lambda path: (
            {"commit_sha256": "c" * 64},
            sha256_file(formal_path),
        ),
    )
    monkeypatch.setattr(
        final_stage,
        "_locked_daymet_feature_names",
        lambda formal: ("daymet_x",),
    )
    monkeypatch.setattr(final_stage, "_read_json", lambda path, label: {})
    monkeypatch.setattr(
        final_stage,
        "_validate_blind_inventory",
        lambda *args, **kwargs: (keys, key_path, key_record, "d" * 64),
    )
    monkeypatch.setattr(final_stage, "_validate_final_keys", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        final_stage,
        "_validate_grid_and_subsets",
        lambda *args, **kwargs: (
            pd.DataFrame(),
            (),
            {
                "subset_files": subset_files,
                "locked_manifest_files": grid_files,
            },
        ),
    )
    monkeypatch.setattr(
        final_stage,
        "_authenticate_frozen_weights",
        lambda *args, **kwargs: (
            pd.DataFrame(),
            {
                "formal_chain": {"phase2": chain_record},
                "development_daymet_provenance": daymet_record,
                "fixed_cell_weights": weights_record,
            },
        ),
    )
    monkeypatch.setattr(
        final_stage,
        "compile_final_test_daymet_feature_tables",
        compile_stub,
    )

    with pytest.raises(FinalTestDaymetFeatureError, match="pipeline changed"):
        final_stage.build_final_test_daymet_feature_artifacts(
            formal_lock_path=formal_path,
            landsat_inventory_directory=inventory_directory,
            daymet_manifest_directory=manifest_directory,
            output_directory=output,
            provenance_path=marker,
        )
    assert compile_called is True
    assert not output.exists()
    assert not marker.exists()
    assert not list(tmp_path.glob(".output.staging-*"))
