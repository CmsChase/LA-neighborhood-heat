"""Focused checks for the six-date target-blind Colorado predictor trial."""

import ctypes
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from rasterio.errors import WarpOperationError

from experiments import source_city_predictor_trial as trial


def test_official_metadata_impact_isolates_blocked_date() -> None:
    products = [
        {
            "product_uri": "good",
            "category": "conversion_complete",
            "target_dates": ["2021-10-18", "2022-10-29"],
        },
        {
            "product_uri": "missing",
            "category": "required_offset_missing",
            "target_dates": ["2021-10-18"],
        },
    ]
    result = trial._official_metadata_impact(products, ("2021-10-18", "2022-10-29"))
    assert result["2021-10-18"] == {"product_count": 2, "blocked_products": ["missing"]}
    assert result["2022-10-29"] == {"product_count": 1, "blocked_products": []}


def test_official_batch_entry_refuses_current_preparation_stage(monkeypatch, tmp_path) -> None:
    stage = tmp_path / "ACTIVE_STAGE.json"
    stage.write_text(
        json.dumps(
            {
                "state": "running_colorado_official_predictor_preparation_only",
                "source_city_colorado_formal_build_plan": {"sentinel_full_batch_approved": False},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(trial, "ACTIVE", stage)
    with pytest.raises(RuntimeError, match="NOT authorized"):
        trial.run_official_sentinel_batch()


def test_official_resume_opens_only_batch_gate_and_closes_it(monkeypatch, tmp_path) -> None:
    import shutil

    output = tmp_path / "output"
    output.mkdir()
    plan = output / "sentinel_batch_plan.json"
    plan.write_text('{"state":"prepared_not_executed"}', encoding="utf-8")
    active = tmp_path / "ACTIVE_STAGE.json"
    stage = {
        "state": "paused_colorado_official_sentinel_batch",
        "permissions": {
            "read_public_predictor_sources": False,
            "build_predictor": False,
            "read_new_candidate_public_metadata": False,
            "read_new_candidate_targets": False,
            "fit_model": False,
            "score_model": False,
        },
        "source_city_colorado_formal_build_plan": {
            "sentinel_resume_pre_authorized": True,
            "sentinel_full_batch_approved": False,
            "official_sentinel_batch_plan_sha256": trial.sha256_file(plan),
            "official_predictor_preparation_scope": {
                "full_sentinel_image_batch_allowed": False
            },
            "temporary_permissions_closed": True,
        },
    }
    active.write_text(json.dumps(stage), encoding="utf-8")
    monkeypatch.setattr(trial, "ACTIVE", active)
    monkeypatch.setattr(trial, "OFFICIAL_OUTPUT", output)
    monkeypatch.setattr(trial, "ROOT", tmp_path)
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: SimpleNamespace(free=40 * 1024**3))

    def fake_batch():
        current = json.loads(active.read_text(encoding="utf-8"))
        assert current["state"] == "running_colorado_official_sentinel_batch_only"
        assert current["permissions"]["read_public_predictor_sources"]
        assert not current["permissions"]["read_new_candidate_targets"]
        assert current["source_city_colorado_formal_build_plan"]["sentinel_full_batch_approved"]
        return {"state": "test_checkpoint_only"}

    monkeypatch.setattr(trial, "run_official_sentinel_batch", fake_batch)
    assert trial.run_official_sentinel_resume()["state"] == "test_checkpoint_only"
    closed = json.loads(active.read_text(encoding="utf-8"))
    assert closed["state"] == "paused_colorado_official_sentinel_batch"
    assert not any(closed["permissions"].values())
    assert not closed["source_city_colorado_formal_build_plan"]["sentinel_full_batch_approved"]


def test_isolated_sentinel_dates_exclude_only_frozen_blocked_window() -> None:
    rows = [
        {"target_date": day, "physical_acquisition_id": f"{day}-{index}"}
        for day in trial.DATES
        for index in range(24)
    ]
    membership = pd.DataFrame(rows)
    blocked_id = "2021-10-18-0"
    preflight = {
        "unique_product_count": 562,
        "by_target_date": {
            day: {
                "required_offset_missing": int(day == "2021-10-18"),
                "read_or_identity_failure": 0,
            }
            for day in trial.DATES
        },
        "products": [
            {
                "category": "required_offset_missing",
                "target_dates": ["2021-10-18"],
                "physical_acquisitions": [blocked_id],
            }
        ],
    }
    assert trial._isolated_sentinel_dates(preflight, membership) == tuple(
        day for day in trial.DATES if day != "2021-10-18"
    )
    membership.loc[membership.physical_acquisition_id == blocked_id, "target_date"] = "2022-10-29"
    with np.testing.assert_raises_regex(RuntimeError, "membership disagrees"):
        trial._isolated_sentinel_dates(preflight, membership)


def test_remote_cog_read_has_one_bounded_serial_retry(monkeypatch) -> None:
    monkeypatch.setattr(trial.clock, "sleep", lambda _seconds: None)
    calls = []

    def recover(threads):
        calls.append(threads)
        if threads == 2:
            raise WarpOperationError("signed URL must not appear")

    trial._process_with_bounded_remote_retry(recover, "frozen-acquisition")
    assert calls == [2, 1]

    def fail(threads):
        raise WarpOperationError("signed URL must not appear")

    with pytest.raises(RuntimeError, match="failed twice") as error:
        trial._process_with_bounded_remote_retry(fail, "frozen-acquisition")
    assert "signed URL" not in str(error.value)


def test_official_batch_retries_observed_transient_proxy_and_warp_errors() -> None:
    assert "ProxyError" in trial.OFFICIAL_SENTINEL_RETRYABLE_ERRORS
    assert "WarpOperationError" in trial.OFFICIAL_SENTINEL_RETRYABLE_ERRORS
    assert "ValueError" not in trial.OFFICIAL_SENTINEL_RETRYABLE_ERRORS
    assert trial.OFFICIAL_SENTINEL_MAX_ATTEMPTS_PER_ACQUISITION == 6
    assert trial.OFFICIAL_SENTINEL_RETRY_DELAYS_SECONDS == (5, 15, 30, 60, 120)


def test_official_resume_recovers_orphaned_running_stage(monkeypatch, tmp_path) -> None:
    import shutil

    output = tmp_path / "output"
    output.mkdir()
    plan = output / "sentinel_batch_plan.json"
    plan.write_text('{"state":"prepared_not_executed"}', encoding="utf-8")
    active = tmp_path / "ACTIVE_STAGE.json"
    stage = {
        "state": "running_colorado_official_sentinel_batch_only",
        "permissions": {
            "read_public_predictor_sources": True,
            "build_predictor": True,
            "read_new_candidate_public_metadata": True,
            "read_new_candidate_targets": False,
            "fit_model": False,
            "score_model": False,
        },
        "source_city_colorado_formal_build_plan": {
            "sentinel_resume_pre_authorized": True,
            "sentinel_full_batch_approved": True,
            "official_sentinel_batch_plan_sha256": trial.sha256_file(plan),
            "official_predictor_preparation_scope": {
                "full_sentinel_image_batch_allowed": True
            },
            "temporary_permissions_closed": False,
        },
    }
    active.write_text(json.dumps(stage), encoding="utf-8")
    monkeypatch.setattr(trial, "ACTIVE", active)
    monkeypatch.setattr(trial, "OFFICIAL_OUTPUT", output)
    monkeypatch.setattr(trial, "ROOT", tmp_path)
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: SimpleNamespace(free=40 * 1024**3))
    monkeypatch.setattr(trial, "_windows_pid_is_running", lambda _pid: False)
    monkeypatch.setattr(
        trial, "run_official_sentinel_batch", lambda: {"state": "orphan_recovered"}
    )
    assert trial.run_official_sentinel_resume()["state"] == "orphan_recovered"
    closed = json.loads(active.read_text(encoding="utf-8"))
    assert closed["state"] == "paused_colorado_official_sentinel_batch"
    assert not any(closed["permissions"].values())
    assert not (output / "sentinel_batch_worker.json").exists()


def test_network_stats_discards_signed_file_urls() -> None:
    class Library:
        def __init__(self) -> None:
            self.data = ctypes.create_string_buffer(
                json.dumps(
                    {
                        "methods": {"GET": {"count": 3, "downloaded_bytes": 1200}},
                        "handlers": {"vsicurl": {"files": {"https://example/sig=secret": {}}}},
                    }
                ).encode("utf-8")
            )

        def VSINetworkStatsGetAsSerializedJSON(self, _options):  # noqa: N802
            return ctypes.addressof(self.data)

        def VSIFree(self, _pointer):  # noqa: N802
            return None

    observed = trial._gdal_network_method_totals(Library())
    assert observed == {"GET": {"count": 3, "downloaded_bytes": 1200}}
    assert "secret" not in str(observed)


def test_daymet_probe_checks_frozen_subset_without_persisting_body(
    monkeypatch, tmp_path, capsys
) -> None:
    from la_heat import daymet_grid

    inventory = {
        "dates": list(trial.DATES),
        "granule_count": 30,
        "granules": [
            {
                "year": 2020,
                "variable": "dayl",
                "title": "Daymet_Daily_V4R1.daymet_v4_daily_na_dayl_2020.nc",
                "concept_id": "G123",
                "full_granule_size_mb_metadata_only": 100.0,
            }
        ],
        "subset_window": {
            "y_indices_inclusive": [5338, 5376],
            "x_indices_inclusive": [4150, 4181],
        },
    }
    (tmp_path / "daymet_inventory.json").write_text(json.dumps(inventory), encoding="utf-8")
    monkeypatch.setattr(trial, "OUTPUT", tmp_path)
    monkeypatch.setattr(trial, "_preflight", lambda **_kwargs: None)
    monkeypatch.setattr(
        daymet_grid,
        "load_earthdata_bearer_token",
        lambda: SimpleNamespace(value="test-only-token"),
    )

    class Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_content(self, *, chunk_size):
            assert chunk_size == 64 * 1024
            yield b""
            yield b"CDF\x01\x00\x00\x00\x00"

    monkeypatch.setattr(trial.requests, "get", lambda *_args, **_kwargs: Response())
    assert trial.probe_daymet_access()
    output = capsys.readouterr().out.strip().splitlines()
    assert output[-1] == "DAYMET_ACCESS_NETCDF_OK"
    assert (
        json.loads(output[0].removeprefix("DAYMET_PROBE_DIAGNOSTIC "))["payload_kind"]
        == "netcdf_classic"
    )
    assert sorted(path.name for path in tmp_path.iterdir()) == ["daymet_inventory.json"]


def test_daymet_probe_diagnostic_redacts_redirect_and_rejects_login_html() -> None:
    request_url = "https://opendap.earthdata.nasa.gov/collections/frozen/granules/file.dap.nc4"

    class Response:
        status_code = 200
        url = "https://urs.earthdata.nasa.gov/login?code=secret-query-value"
        history = [
            SimpleNamespace(
                request=SimpleNamespace(headers={"Authorization": "Bearer secret-bearer-value"})
            )
        ]
        request = SimpleNamespace(headers={})
        headers = {
            "Content-Type": "text/html; charset=utf-8",
            "Content-Length": str(len(b"<html>Earthdata login</html>")),
        }

        def iter_content(self, *, chunk_size):
            assert chunk_size == 64 * 1024
            yield b"<html>Earthdata login</html>"

    diagnostic, valid = trial._daymet_probe_diagnostic(Response(), request_url)
    assert not valid
    assert diagnostic["payload_kind"] == "login_page"
    assert diagnostic["redirected"] is True
    assert diagnostic["final_host"] == "urs.earthdata.nasa.gov"
    assert diagnostic["final_path_redacted"] == "/login"
    assert diagnostic["initial_auth_scheme"] == "bearer"
    assert diagnostic["final_auth_scheme"] == "none"
    assert "secret-query-value" not in json.dumps(diagnostic)
    assert "secret-bearer-value" not in json.dumps(diagnostic)
    assert trial._safe_daymet_probe_location(
        "https://secret-value.example.com/login?token=secret-value", request_url
    ) == ("<non-Earthdata-host>", "/login")


def test_daymet_probe_diagnostic_accepts_decoded_gzip_hdf5_userblock() -> None:
    request_url = "https://opendap.earthdata.nasa.gov/collections/frozen/granules/file.dap.nc4"

    class Response:
        status_code = 200
        url = request_url
        history = []
        headers = {
            "Content-Type": "application/x-netcdf",
            "Content-Encoding": "gzip",
            "Content-Length": "42",
        }

        def iter_content(self, *, chunk_size):
            assert chunk_size == 64 * 1024
            yield b""
            yield b"\x00" * 512
            yield b"\x89HDF\r\n\x1a\n" + b"test-binary-data"

    diagnostic, valid = trial._daymet_probe_diagnostic(Response(), request_url)
    assert valid
    assert diagnostic["payload_kind"] == "netcdf4_hdf5"
    assert diagnostic["actual_decoded_bytes_read"] > diagnostic["declared_wire_length_bytes"]
    assert diagnostic["uncompressed_length_mismatch"] is False


def test_daymet_probe_diagnostic_rejects_unrequested_partial_content() -> None:
    request_url = "https://opendap.earthdata.nasa.gov/collections/frozen/granules/file.dap.nc4"

    class Response:
        status_code = 206
        url = request_url
        history = []
        headers = {"Content-Type": "application/x-netcdf", "Content-Range": "bytes 0-7/100"}

        def iter_content(self, *, chunk_size):
            assert chunk_size == 64 * 1024
            yield b"CDF\x02xxxx"

    diagnostic, valid = trial._daymet_probe_diagnostic(Response(), request_url)
    assert not valid
    assert diagnostic["http_status"] == 206
    assert diagnostic["content_range_present_without_range_request"] is True


def test_local_daymet_failed_private_probe_never_downloads(monkeypatch, tmp_path) -> None:
    from la_heat import daymet_grid
    from la_heat.multicity import source_footprints

    rows = [
        {
            "year": year,
            "variable": variable,
            "title": f"{year}-{variable}",
            "concept_id": "frozen",
            "full_granule_size_mb_metadata_only": 1.0,
        }
        for year in range(2020, 2025)
        for variable in ("dayl", "prcp", "srad", "tmax", "tmin", "vp")
    ]
    window = {"y_indices_inclusive": [5338, 5376], "x_indices_inclusive": [4150, 4181]}
    (tmp_path / "daymet_inventory.json").write_text(
        json.dumps({"dates": list(trial.DATES), "granules": rows, "subset_window": window}),
        encoding="utf-8",
    )
    monkeypatch.setattr(trial, "OUTPUT", tmp_path)
    monkeypatch.setattr(trial, "_preflight", lambda **_kwargs: None)
    monkeypatch.setattr(
        trial.gpd,
        "read_file",
        lambda _path: SimpleNamespace(total_bounds=np.array([-105, 38, -104, 39])),
    )
    monkeypatch.setattr(
        source_footprints, "derive_daymet_index_window", lambda *_args, **_kwargs: window
    )
    monkeypatch.setattr(
        trial,
        "support",
        lambda **_kwargs: SimpleNamespace(tract_geoids=tuple(str(i) for i in range(110))),
    )
    monkeypatch.setattr(
        daymet_grid,
        "prompt_earthdata_bearer_token",
        lambda: SimpleNamespace(value="private-test-only"),
    )
    monkeypatch.setattr(trial, "probe_daymet_access", lambda **_kwargs: False)
    monkeypatch.setattr(
        daymet_grid, "authenticated_netcdf_download", lambda *_args, **_kwargs: 1 / 0
    )
    import pytest

    with pytest.raises(RuntimeError, match="probe failed"):
        trial.run_daymet_local()
    assert not (tmp_path / "daymet_subsets").exists()


def _frozen_local_daymet_fixture(monkeypatch, tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    inventory = {
        "dates": list(trial.DATES),
        "granules": [
            {"year": year, "variable": variable}
            for year in range(2020, 2025)
            for variable in ("dayl", "prcp", "srad", "tmax", "tmin", "vp")
        ],
        "subset_window": {"y_indices_inclusive": [1, 2], "x_indices_inclusive": [3, 4]},
    }
    (output / "daymet_inventory.json").write_text(json.dumps(inventory), encoding="utf-8")
    support_dir = tmp_path / "support"
    support_dir.mkdir()
    (support_dir / "fixed_support.npz").write_bytes(b"frozen-test-support")
    stage = {
        "state": "paused_colorado_six_date_exploratory_predictor_trial_only",
        "permissions": {
            name: False
            for name in (
                "read_public_predictor_sources",
                "build_predictor",
                "read_source_targets",
                "fit_model",
                "score_model",
                "read_external_targets",
                "read_new_candidate_targets",
            )
        },
        "source_city_exploratory_predictor_trial": {
            "allowed_city_id": trial.CITY,
            "allowed_target_dates": list(trial.DATES),
            "full_predictor_key_count": 660,
            "local_daymet_entrypoint": {
                "approved_for_private_interactive_run": True,
                "daymet_inventory_sha256": trial.sha256_file(output / "daymet_inventory.json"),
            },
        },
    }
    active = tmp_path / "ACTIVE_STAGE.json"
    active.write_text(json.dumps(stage), encoding="utf-8")
    monkeypatch.setattr(trial, "ACTIVE", active)
    monkeypatch.setattr(trial, "OUTPUT", output)
    monkeypatch.setattr(trial, "SUPPORT_DIR", support_dir)
    monkeypatch.setattr(trial, "SUPPORT_SHA", trial.sha256_file(support_dir / "fixed_support.npz"))
    return stage, active


def test_local_daymet_paused_stage_reaches_private_prompt_without_network(
    monkeypatch, tmp_path
) -> None:
    import pytest

    from la_heat import daymet_grid
    from la_heat.multicity import source_footprints

    stage, _active = _frozen_local_daymet_fixture(monkeypatch, tmp_path)
    trial._preflight(local_daymet=True)
    assert not any(stage["permissions"].values())
    monkeypatch.setattr(
        trial.gpd,
        "read_file",
        lambda _path: SimpleNamespace(total_bounds=np.array([-105, 38, -104, 39])),
    )
    monkeypatch.setattr(
        source_footprints,
        "derive_daymet_index_window",
        lambda *_args, **_kwargs: {
            "y_indices_inclusive": [1, 2],
            "x_indices_inclusive": [3, 4],
        },
    )
    monkeypatch.setattr(trial, "support", lambda **_kwargs: SimpleNamespace(tract_geoids=()))
    monkeypatch.setattr(trial, "keys", lambda _support: pd.DataFrame())

    class PromptReached(Exception):
        pass

    def stop_at_prompt():
        raise PromptReached()

    monkeypatch.setattr(daymet_grid, "prompt_earthdata_bearer_token", stop_at_prompt)
    monkeypatch.setattr(
        daymet_grid, "authenticated_netcdf_download", lambda *_args, **_kwargs: 1 / 0
    )
    with pytest.raises(PromptReached):
        trial.run_daymet_local()


def test_local_daymet_preflight_names_permission_and_inventory_drift(monkeypatch, tmp_path) -> None:
    import pytest

    stage, active = _frozen_local_daymet_fixture(monkeypatch, tmp_path)
    stage["permissions"]["fit_model"] = True
    stage["source_city_exploratory_predictor_trial"]["local_daymet_entrypoint"][
        "daymet_inventory_sha256"
    ] = "incorrect-frozen-hash"
    active.write_text(json.dumps(stage), encoding="utf-8")
    with pytest.raises(RuntimeError) as error:
        trial._preflight(local_daymet=True)
    assert "fit_model" in str(error.value)
    assert "Daymet inventory SHA-256" in str(error.value)


def test_sentinel_preflight_requires_exact_product_identity_and_offset() -> None:
    import pytest

    uri = "S2A_MSIL2A_20211005T174211_N0400_R098_T13SED_20220512T201134.SAFE"
    snapshot = {
        "properties": {
            "s2:product_uri": uri,
            "s2:processing_baseline": "04.00",
            "s2:mgrs_tile": "13SED",
            "platform": "Sentinel-2A",
            "datetime": "2021-10-05T17:42:11.024Z",
            "s2:generation_time": "2022-05-12T20:11:34.553Z",
        }
    }
    xml = f"""<root><PRODUCT_URI>{uri}</PRODUCT_URI>
        <PROCESSING_BASELINE>04.00</PROCESSING_BASELINE>
        <SPACECRAFT_NAME>Sentinel-2A</SPACECRAFT_NAME>
        <PRODUCT_START_TIME>2021-10-05T17:42:11.024Z</PRODUCT_START_TIME>
        <GENERATION_TIME>2022-05-12T20:11:34.553Z</GENERATION_TIME>
        <BOA_QUANTIFICATION_VALUE>10000</BOA_QUANTIFICATION_VALUE></root>"""
    assert trial._classify_sentinel_product_xml(snapshot, xml.encode())["category"] == (
        "required_offset_missing"
    )
    other = xml.replace("N0400_R098", "N0510_R098")
    with pytest.raises(ValueError, match="identity disagrees"):
        trial._classify_sentinel_product_xml(snapshot, other.encode())


def test_full_keys_do_not_depend_on_label_availability() -> None:
    geoids = tuple(f"08041{i:06d}" for i in range(110))
    frame = trial.keys(SimpleNamespace(tract_geoids=geoids))
    assert len(frame) == 660
    assert frame["tract_geoid"].nunique() == 110
    assert frame["target_date"].nunique() == 6
    assert not frame.duplicated(["city_id", "tract_geoid", "target_date"]).any()


def test_existing_label_join_reads_only_keys_and_availability(tmp_path) -> None:
    geoids = [f"08041{i:06d}" for i in range(110)]
    for target_date in trial.DATES:
        pd.DataFrame(
            {
                "tract_geoid": geoids,
                "target_available_exploratory": [True] * 109 + [False],
                "target_lst_c_development_only": ["do not parse"] * 110,
            }
        ).to_csv(tmp_path / f"{target_date}_development_labels.csv", index=False)
    observed = trial.existing_label_keys(tmp_path)
    assert list(observed.columns) == ["tract_geoid", "target_available_exploratory", "target_date"]
    assert len(observed) == 660
    assert int(observed.target_available_exploratory.sum()) == 654
