from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd
import pytest
import requests
from affine import Affine
from rasterio.transform import from_origin
from scipy.io import netcdf_file

import la_heat.daymet_grid as daymet_grid
from la_heat.daymet_grid import (
    DAYMET_CMR_COLLECTION_ID,
    DAYMET_FULL_GRID_TRANSFORM,
    DaymetAuthenticationError,
    DaymetGranule,
    DaymetGridAuditError,
    EarthdataBearerToken,
    aggregate_daymet_cells_to_tract_daily,
    authenticated_netcdf_download,
    build_daymet_direct_subset_url,
    build_fixed_eligible_cell_weights,
    build_lagged_tract_daymet_features,
    discover_daymet_v4r1_granules,
    inspect_daymet_netcdf,
    load_earthdata_bearer_token,
    prompt_earthdata_bearer_token,
    read_daymet_netcdf_cells,
    request_daymet_subset_url,
    validate_daymet_direct_subset_spec,
    validate_daymet_netcdf_grid_specs,
    validate_fixed_cell_weights,
)


class _FakeResponse:
    def __init__(
        self,
        *,
        payload: object | None = None,
        status_code: int = 200,
        chunks: tuple[bytes, ...] = (),
        headers: dict[str, str] | None = None,
        url: str = "https://example.invalid",
    ) -> None:
        self.payload = payload
        self.status_code = status_code
        self.chunks = chunks
        self.headers = {} if headers is None else headers
        self.url = url
        self.closed = False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> object:
        return self.payload

    def iter_content(self, chunk_size: int) -> Any:
        assert chunk_size > 0
        yield from self.chunks

    def close(self) -> None:
        self.closed = True


def test_select_daymet_subdataset_accepts_dap4_group_path(monkeypatch) -> None:
    expected = 'NETCDF:"subset.nc":/generated_group/tmax'

    class _Root:
        subdatasets = (
            'NETCDF:"subset.nc":/generated_group/lat',
            expected,
        )

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(daymet_grid.rasterio, "open", lambda _path: _Root())

    assert daymet_grid._select_daymet_subdataset(Path("subset.nc"), "tmax") == expected


class _RecordingClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append((url, kwargs))
        return self.response


def _cmr_entry(variable: str, year: int, number: int) -> dict[str, object]:
    filename = f"daymet_v4_daily_na_{variable}_{year}.nc"
    return {
        "id": f"G{number}-ORNL_CLOUD",
        "title": f"Daymet_Daily_V4R1.{filename}",
        "granule_size": "123.5",
        "updated": "2025-09-12T00:00:00Z",
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
                    f"{DAYMET_CMR_COLLECTION_ID}/granules/"
                    f"Daymet_Daily_V4R1.{filename}"
                ),
            },
        ],
    }


def _granule() -> DaymetGranule:
    entry = _cmr_entry("tmax", 2020, 123)
    links = entry["links"]
    assert isinstance(links, list)
    return DaymetGranule(
        concept_id="G123-ORNL_CLOUD",
        title=str(entry["title"]),
        variable="tmax",
        year=2020,
        size_mb=123.5,
        https_url=str(links[0]["href"]),
        opendap_url=str(links[1]["href"]),
        updated_at="2025-09-12T00:00:00Z",
    )


def test_public_cmr_discovery_is_exact_v4r1_and_target_year_locked() -> None:
    entries = [
        _cmr_entry("tmax", 2020, 1),
        _cmr_entry("tmin", 2020, 2),
        _cmr_entry("tmax", 2021, 3),
        _cmr_entry("tmin", 2021, 4),
        _cmr_entry("swe", 2020, 5),
    ]
    client = _RecordingClient(_FakeResponse(payload={"feed": {"entry": entries}}))

    discovered = discover_daymet_v4r1_granules(
        years=(2020, 2021),
        variables=("tmax", "tmin"),
        http_client=client,
    )

    assert [(item.year, item.variable) for item in discovered] == [
        (2020, "tmax"),
        (2020, "tmin"),
        (2021, "tmax"),
        (2021, "tmin"),
    ]
    params = client.calls[0][1]["params"]
    assert isinstance(params, dict)
    assert params["collection_concept_id"] == DAYMET_CMR_COLLECTION_ID
    assert params["page_size"] == 2000
    assert all("1840" not in item.https_url for item in discovered)

    with pytest.raises(PermissionError, match="locked final-test year"):
        discover_daymet_v4r1_granules(
            years=(2025,),
            variables=("tmax",),
            http_client=client,
        )
    assert len(client.calls) == 1


def test_cmr_discovery_fails_on_missing_or_duplicate_granule() -> None:
    missing_client = _RecordingClient(
        _FakeResponse(payload={"feed": {"entry": [_cmr_entry("tmax", 2020, 1)]}})
    )
    with pytest.raises(DaymetGridAuditError, match="incomplete"):
        discover_daymet_v4r1_granules(
            years=(2020,),
            variables=("tmax", "tmin"),
            http_client=missing_client,
        )

    duplicate_client = _RecordingClient(
        _FakeResponse(
            payload={
                "feed": {
                    "entry": [
                        _cmr_entry("tmax", 2020, 1),
                        _cmr_entry("tmax", 2020, 2),
                    ]
                }
            }
        )
    )
    with pytest.raises(DaymetGridAuditError, match="duplicate"):
        discover_daymet_v4r1_granules(
            years=(2020,),
            variables=("tmax",),
            http_client=duplicate_client,
        )


def test_credentials_are_fail_closed_and_redacted() -> None:
    with pytest.raises(DaymetAuthenticationError, match="requires"):
        load_earthdata_bearer_token(environment={})

    token = load_earthdata_bearer_token(
        environment={"EARTHDATA_TOKEN": "super-secret-test-token"}
    )
    assert token.source_environment_variable == "EARTHDATA_TOKEN"
    assert "super-secret" not in repr(token)

    with pytest.raises(DaymetAuthenticationError, match="Multiple"):
        load_earthdata_bearer_token(
            environment={"EARTHDATA_TOKEN": "one", "EDL_TOKEN": "two"}
        )


def test_interactive_earthdata_token_is_hidden_and_fail_closed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    prompts: list[str] = []

    def prompt(message: str) -> str:
        prompts.append(message)
        return "interactive-secret"

    token = prompt_earthdata_bearer_token(
        environment={},
        prompt_function=prompt,
    )
    assert token.value == "interactive-secret"
    assert token.source_environment_variable == "interactive_prompt"
    assert prompts == ["Earthdata bearer token: "]
    assert "interactive-secret" not in repr(token)
    captured = capsys.readouterr()
    assert "interactive-secret" not in captured.out
    assert "interactive-secret" not in captured.err


@pytest.mark.parametrize(
    "environment",
    [
        {"EARTHDATA_TOKEN": "already-set"},
        {"NASA_EARTHDATA_TOKEN": ""},
        {"EDL_TOKEN": "one", "EARTHDATA_TOKEN": "two"},
    ],
)
def test_interactive_earthdata_token_rejects_any_environment_conflict(
    environment: dict[str, str],
) -> None:
    with pytest.raises(DaymetAuthenticationError, match="cannot be combined"):
        prompt_earthdata_bearer_token(
            environment=environment,
            prompt_function=lambda _: pytest.fail("prompt must not be called"),
        )


def test_interactive_earthdata_token_requires_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        daymet_grid.sys,
        "stdin",
        SimpleNamespace(isatty=lambda: False),
    )
    with pytest.raises(DaymetAuthenticationError, match="requires a real terminal"):
        prompt_earthdata_bearer_token(environment={})


@pytest.mark.parametrize("value", ["", " ", " token ", "\t"])
def test_interactive_earthdata_token_rejects_empty_or_untrimmed(value: str) -> None:
    with pytest.raises(ValueError, match="non-empty and trimmed"):
        prompt_earthdata_bearer_token(
            environment={},
            prompt_function=lambda _: value,
        )


def test_service_bridge_requires_clean_single_spatial_subset_url() -> None:
    granule = _granule()
    subset_url = (
        granule.opendap_url
        + ".dap.nc4?dap4.ce=/y[1:1:2];/x[3:1:4];/tmax[0:1:364][1:1:2][3:1:4]"
    )
    client = _RecordingClient(
        _FakeResponse(payload={"hits": 1, "items": [subset_url], "warnings": None})
    )
    credential = EarthdataBearerToken("token", "EARTHDATA_TOKEN")

    actual = request_daymet_subset_url(
        granule,
        bbox_wgs84=(-118.67, 33.70, -118.15, 34.34),
        credential=credential,
        http_client=client,
    )

    assert actual == subset_url
    request = client.calls[0][1]
    assert request["headers"] == {
        "Echo-Token": "token",
        "Accept": "application/vnd.cmr-service-bridge.v3+json",
    }
    params = request["params"]
    assert isinstance(params, dict)
    assert params["granules"] == granule.concept_id
    assert params["bounding-box"] == "-118.67,33.7,-118.15,34.34"

    warning_client = _RecordingClient(
        _FakeResponse(
            payload={
                "hits": 1,
                "items": [subset_url],
                "warnings": ["spatial subset ignored"],
            }
        )
    )
    with pytest.raises(DaymetGridAuditError, match="warnings"):
        request_daymet_subset_url(
            granule,
            bbox_wgs84=(-118.67, 33.70, -118.15, 34.34),
            credential=credential,
            http_client=warning_client,
        )

    secret_url = granule.opendap_url + ".dap.nc4?access_token=secret-value"
    secret_client = _RecordingClient(
        _FakeResponse(payload={"hits": 1, "items": [secret_url], "warnings": None})
    )
    with pytest.raises(DaymetAuthenticationError, match="credential-like"):
        request_daymet_subset_url(
            granule,
            bbox_wgs84=(-118.67, 33.70, -118.15, 34.34),
            credential=credential,
            http_client=secret_client,
        )

    alternate_url = (
        "https://example.earthdata.nasa.gov/alternate.nc"
        "?dap4.ce=/tmax[0:1:364][1:1:2][3:1:4]"
    )
    multiple_client = _RecordingClient(
        _FakeResponse(
            payload={
                "hits": 2,
                "items": [alternate_url, subset_url],
                "warnings": None,
            }
        )
    )
    assert (
        request_daymet_subset_url(
            granule,
            bbox_wgs84=(-118.67, 33.70, -118.15, 34.34),
            credential=credential,
            http_client=multiple_client,
        )
        == subset_url
    )

    coordinate_only_constraint = (
        granule.opendap_url
        + ".dap.nc4?dap4.ce=/y[1:1:2];/x[3:1:4]"
    )
    coordinate_only_client = _RecordingClient(
        _FakeResponse(
            payload={
                "hits": 1,
                "items": [coordinate_only_constraint],
                "warnings": None,
            }
        )
    )
    assert (
        request_daymet_subset_url(
            granule,
            bbox_wgs84=(-118.67, 33.70, -118.15, 34.34),
            credential=credential,
            http_client=coordinate_only_client,
        )
        == coordinate_only_constraint
    )

    opaque_query_url = granule.opendap_url + ".dap.nc4?server-generated=subset"
    opaque_query_client = _RecordingClient(
        _FakeResponse(
            payload={"hits": 1, "items": [opaque_query_url], "warnings": None}
        )
    )
    assert (
        request_daymet_subset_url(
            granule,
            bbox_wgs84=(-118.67, 33.70, -118.15, 34.34),
            credential=credential,
            http_client=opaque_query_client,
        )
        == opaque_query_url
    )

    unconstrained_url = granule.opendap_url + ".dap.nc4"
    unconstrained_client = _RecordingClient(
        _FakeResponse(
            payload={"hits": 1, "items": [unconstrained_url], "warnings": None}
        )
    )
    with pytest.raises(DaymetGridAuditError, match="matching_count=0"):
        request_daymet_subset_url(
            granule,
            bbox_wgs84=(-118.67, 33.70, -118.15, 34.34),
            credential=credential,
            http_client=unconstrained_client,
        )

    duplicate_client = _RecordingClient(
        _FakeResponse(
            payload={
                "hits": 2,
                "items": [subset_url, subset_url],
                "warnings": None,
            }
        )
    )
    with pytest.raises(DaymetGridAuditError, match="matching_count=2"):
        request_daymet_subset_url(
            granule,
            bbox_wgs84=(-118.67, 33.70, -118.15, 34.34),
            credential=credential,
            http_client=duplicate_client,
        )


def test_authenticated_download_is_atomic_and_rejects_login_page(tmp_path: Path) -> None:
    granule = _granule()
    url = granule.opendap_url + ".dap.nc4?dap4.ce=tmax"
    destination = tmp_path / "subset.nc"
    credential = EarthdataBearerToken("token", "EARTHDATA_TOKEN")
    netcdf = b"CDF\x01" + b"valid-netcdf-test-bytes"
    response = _FakeResponse(
        chunks=(netcdf[:7], netcdf[7:]),
        headers={"Content-Length": str(len(netcdf))},
        url=url,
    )
    client = _RecordingClient(response)

    record = authenticated_netcdf_download(
        url,
        destination,
        credential=credential,
        http_client=client,
    )

    assert destination.read_bytes() == netcdf
    assert not (tmp_path / "subset.nc.partial").exists()
    assert record["bytes"] == len(netcdf)
    assert record["credential_source"] == "EARTHDATA_TOKEN"
    assert response.closed
    assert client.calls[0][1]["headers"] == {"Authorization": "Bearer token"}

    bad_destination = tmp_path / "login.nc"
    bad_client = _RecordingClient(_FakeResponse(chunks=(b"<html>login</html>",)))
    with pytest.raises(DaymetGridAuditError, match="not a NetCDF"):
        authenticated_netcdf_download(
            url,
            bad_destination,
            credential=credential,
            http_client=bad_client,
        )
    assert not bad_destination.exists()
    assert not (tmp_path / "login.nc.partial").exists()


def test_authenticated_download_retries_transient_connection_failures(
    tmp_path: Path,
) -> None:
    granule = _granule()
    url = granule.opendap_url + ".dap.nc4?dap4.ce=tmax"
    destination = tmp_path / "retried.nc"
    credential = EarthdataBearerToken("token", "EARTHDATA_TOKEN")
    netcdf = b"CDF\x01" + b"valid-netcdf-test-bytes"
    response = _FakeResponse(chunks=(netcdf,))

    class _FlakyClient:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, request_url: str, **kwargs: object) -> _FakeResponse:
            assert request_url == url
            assert kwargs
            self.calls += 1
            if self.calls < 3:
                raise requests.ConnectTimeout("synthetic transient timeout")
            return response

    client = _FlakyClient()
    sleeps: list[float] = []
    record = authenticated_netcdf_download(
        url,
        destination,
        credential=credential,
        http_client=client,
        maximum_attempts=3,
        retry_backoff_seconds=0.25,
        sleep_function=sleeps.append,
    )

    assert client.calls == 3
    assert sleeps == [0.25, 0.5]
    assert record["bytes"] == len(netcdf)
    assert destination.read_bytes() == netcdf


def test_direct_dap4_subset_url_is_exact_and_credential_free() -> None:
    granule = _granule()
    url = build_daymet_direct_subset_url(
        granule,
        y_indices=(5666, 5745),
        x_indices=(2900, 2963),
    )
    parsed = urlparse(url)
    constraint = parse_qs(parsed.query)["dap4.ce"]

    assert parsed.scheme == "https"
    assert parsed.hostname == "opendap.earthdata.nasa.gov"
    assert parsed.path == urlparse(granule.opendap_url).path + ".dap.nc4"
    assert len(constraint) == 1
    assert "/y[5666:1:5745]" in constraint[0]
    assert "/x[2900:1:2963]" in constraint[0]
    assert "/tmax[0:1:364][5666:1:5745][2900:1:2963]" in constraint[0]
    assert "token" not in url.casefold()

    with pytest.raises(ValueError, match="outside the frozen full grid"):
        build_daymet_direct_subset_url(
            granule,
            y_indices=(0, 8075),
            x_indices=(0, 1),
        )
    with pytest.raises(DaymetGridAuditError, match="exact official"):
        build_daymet_direct_subset_url(
            replace(granule, title="wrong.nc"),
            y_indices=(5666, 5745),
            x_indices=(2900, 2963),
        )


def _write_daymet_netcdf(
    path: Path,
    *,
    variable: str = "tmax",
    year: int = 2023,
    units: str = "degrees C",
    fill_band: int | None = None,
    time_center_offset_days: float = 0.0,
) -> None:
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
        time[:] = np.arange(365, dtype=float) + time_center_offset_days

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

        values = dataset.createVariable(variable, "f4", ("time", "y", "x"))
        values.long_name = variable
        values.units = units
        values.grid_mapping = "lambert_conformal_conic"
        values._FillValue = np.float32(-9999.0)
        data = np.arange(365 * 4, dtype=np.float32).reshape(365, 2, 2)
        if fill_band is not None:
            data[fill_band, 0, 0] = -9999.0
        values[:] = data


def test_netcdf_decoder_audits_grid_time_units_and_fill(tmp_path: Path) -> None:
    path = tmp_path / "daymet_v4r1_daily_na_tmax_2023_la_subset.nc"
    _write_daymet_netcdf(path, fill_band=3)

    spec = inspect_daymet_netcdf(path, variable="tmax", year=2023)
    cells = pd.DataFrame(
        {
            "daymet_cell_id": ["x500.000_y1500.000", "x1500.000_y500.000"],
            "daymet_row": [0, 1],
            "daymet_col": [0, 1],
        }
    )
    decoded = read_daymet_netcdf_cells(spec, cells=cells)

    assert spec.shape == (2, 2)
    assert spec.transform == from_origin(0, 2000, 1000, 1000)
    assert len(decoded) == 730
    assert decoded["date"].min() == pd.Timestamp("2023-01-01")
    assert decoded["date"].max() == pd.Timestamp("2023-12-31")
    assert decoded["tmax_c"].isna().sum() == 1
    assert decoded.duplicated(["daymet_cell_id", "date"]).sum() == 0
    assert validate_daymet_netcdf_grid_specs((spec,)) == spec

    noon_path = tmp_path / "daymet_v4r1_daily_na_tmax_2023_noon_subset.nc"
    _write_daymet_netcdf(noon_path, time_center_offset_days=0.5)
    noon_spec = inspect_daymet_netcdf(noon_path, variable="tmax", year=2023)
    assert noon_spec.dates == spec.dates

    direct_spec = replace(
        spec,
        shape=(80, 64),
        transform=DAYMET_FULL_GRID_TRANSFORM * Affine.translation(2900, 5666),
    )
    assert (
        validate_daymet_direct_subset_spec(
            direct_spec,
            y_indices=(5666, 5745),
            x_indices=(2900, 2963),
            bbox_wgs84=(-118.67, 33.70, -118.15, 34.34),
        )
        == direct_spec
    )
    with pytest.raises(DaymetGridAuditError, match="shape/transform"):
        validate_daymet_direct_subset_spec(
            replace(direct_spec, shape=(79, 64)),
            y_indices=(5666, 5745),
            x_indices=(2900, 2963),
            bbox_wgs84=(-118.67, 33.70, -118.15, 34.34),
        )

    changed = cells.copy()
    changed.loc[0, "daymet_cell_id"] = "x0.000_y0.000"
    with pytest.raises(DaymetGridAuditError, match="disagree"):
        read_daymet_netcdf_cells(spec, cells=changed)


def test_netcdf_decoder_rejects_wrong_units_and_calendar(tmp_path: Path) -> None:
    official_dayl_units = tmp_path / "official-dayl-units.nc"
    _write_daymet_netcdf(official_dayl_units, variable="dayl", units="s")
    inspect_daymet_netcdf(official_dayl_units, variable="dayl", year=2023)

    wrong_units = tmp_path / "wrong-units.nc"
    _write_daymet_netcdf(wrong_units, units="kelvin")
    with pytest.raises(DaymetGridAuditError, match="unexpected units"):
        inspect_daymet_netcdf(wrong_units, variable="tmax", year=2023)

    wrong_year = tmp_path / "wrong-year.nc"
    _write_daymet_netcdf(wrong_year, year=2022)
    with pytest.raises(DaymetGridAuditError, match="exact 365-day calendar"):
        inspect_daymet_netcdf(wrong_year, variable="tmax", year=2023)


def test_fixed_eligible_weights_are_static_and_cover_every_pixel() -> None:
    zones = np.array([[1, 1, 1, 1], [2, 2, 2, 2]], dtype=np.int32)
    eligible = np.array(
        [[True, True, True, False], [True, True, True, True]], dtype=bool
    )
    weights = build_fixed_eligible_cell_weights(
        zone_raster=zones,
        eligible_land_mask=eligible,
        tract_geoids=("A", "B"),
        target_transform=from_origin(0, 60, 30, 30),
        target_crs="EPSG:32611",
        daymet_transform=from_origin(0, 60, 60, 60),
        daymet_crs="EPSG:32611",
        daymet_shape=(1, 2),
    )

    assert weights.groupby("tract_geoid")["eligible_pixel_count"].sum().to_dict() == {
        "A": 3,
        "B": 4,
    }
    assert weights.groupby("tract_geoid")["static_denominator_m2"].first().to_dict() == {
        "A": 2700.0,
        "B": 3600.0,
    }
    assert weights.groupby("tract_geoid")["weight"].sum().to_numpy() == pytest.approx(
        [1.0, 1.0]
    )

    changed = weights.copy()
    changed.loc[changed.index[0], "static_denominator_m2"] += 900
    with pytest.raises(DaymetGridAuditError, match="changes within a tract"):
        validate_fixed_cell_weights(changed)

    missing_tract = eligible.copy()
    missing_tract[1, :] = False
    with pytest.raises(DaymetGridAuditError, match="missing tracts"):
        build_fixed_eligible_cell_weights(
            zone_raster=zones,
            eligible_land_mask=missing_tract,
            tract_geoids=("A", "B"),
            target_transform=from_origin(0, 60, 30, 30),
            target_crs="EPSG:32611",
            daymet_transform=from_origin(0, 60, 60, 60),
            daymet_crs="EPSG:32611",
            daymet_shape=(1, 2),
        )

    fractional_count = weights.copy()
    fractional_count["eligible_pixel_count"] = fractional_count[
        "eligible_pixel_count"
    ].astype(float)
    fractional_count.loc[fractional_count.index[0], "eligible_pixel_count"] = 1.5
    with pytest.raises(DaymetGridAuditError, match="positive integers"):
        validate_fixed_cell_weights(fractional_count)


def _weights() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "tract_geoid": ["A", "A"],
            "daymet_cell_id": ["c1", "c2"],
            "eligible_pixel_count": [1, 3],
            "eligible_area_m2": [900.0, 2700.0],
            "static_denominator_m2": [3600.0, 3600.0],
            "weight": [0.25, 0.75],
        }
    )


def _cell_daily(dates: pd.DatetimeIndex) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for cell_index, cell in enumerate(("c1", "c2"), start=1):
        for day_index, day in enumerate(dates, start=1):
            records.append(
                {
                    "daymet_cell_id": cell,
                    "date": day,
                    "tmax_c": float(20 + day_index + cell_index),
                    "tmin_c": float(10 + day_index),
                    "prcp_mm_day": float(cell_index),
                    "srad_w_m2": 100.0 if cell == "c1" else 200.0,
                    "vp_pa": 1000.0 + cell_index,
                    "dayl_s": 36_000.0 if cell == "c1" else 18_000.0,
                }
            )
    return pd.DataFrame(records)


def test_spatial_aggregation_is_cell_first_and_never_date_renormalized() -> None:
    dates = pd.date_range("2021-07-01", periods=2, freq="D")
    cells = _cell_daily(dates)
    cells.loc[
        (cells["daymet_cell_id"] == "c2") & (cells["date"] == dates[1]),
        "tmax_c",
    ] = np.nan

    daily = aggregate_daymet_cells_to_tract_daily(cells, _weights())

    first = daily.loc[daily["date"] == dates[0]].iloc[0]
    assert first["srad_energy_mj_m2_day"] == pytest.approx(3.6)
    assert first["srad_w_m2"] == pytest.approx(175.0)
    assert first["dayl_s"] == pytest.approx(22_500.0)
    assert first["srad_w_m2"] * first["dayl_s"] / 1_000_000 != pytest.approx(
        first["srad_energy_mj_m2_day"]
    )
    second = daily.loc[daily["date"] == dates[1]].iloc[0]
    assert pd.isna(second["tmax_c"])
    assert second["daymet_grid_cells_expected"] == 2
    assert second["daymet_grid_cells_present"] == 1
    assert daily.attrs["date_specific_weight_renormalization"] is False


def test_tract_windows_end_d_minus_one_and_ignore_target_future_changes() -> None:
    dates = pd.date_range("2021-01-01", periods=12, freq="D")
    cells = _cell_daily(dates)
    tract_daily = aggregate_daymet_cells_to_tract_daily(cells, _weights())
    target_date = pd.Timestamp("2021-01-09")
    baseline = build_lagged_tract_daymet_features(
        tract_daily,
        target_dates=(target_date,),
    ).iloc[0]

    changed_cells = cells.copy()
    changed_cells.loc[changed_cells["date"] >= target_date, "tmax_c"] = 9999.0
    changed_daily = aggregate_daymet_cells_to_tract_daily(changed_cells, _weights())
    changed = build_lagged_tract_daymet_features(
        changed_daily,
        target_dates=(target_date,),
    ).iloc[0]

    feature_columns = [
        column
        for column in baseline.index
        if column.startswith("daymet_") and "source_" not in column
    ]
    pd.testing.assert_series_equal(
        baseline[feature_columns], changed[feature_columns], check_names=False
    )
    assert baseline["daymet_source_end_date"] == target_date - pd.Timedelta(days=1)
    assert baseline["daymet_source_start_date"] == target_date - pd.Timedelta(days=7)
    assert baseline["daymet_tmax_c_mean_prev_1d"] == pytest.approx(29.75)
    assert baseline["daymet_all_primary_windows_complete"]

    with pytest.raises(PermissionError, match="locked final-test year"):
        build_lagged_tract_daymet_features(
            tract_daily,
            target_dates=(pd.Timestamp("2025-07-01"),),
        )


def test_granule_identity_cannot_be_swapped_by_service_bridge() -> None:
    granule = _granule()
    other = replace(granule, variable="tmin")
    wrong_url = other.opendap_url.replace("tmax", "tmin") + ".dap.nc4?dap4.ce=tmin"
    client = _RecordingClient(
        _FakeResponse(payload={"hits": 1, "items": [wrong_url], "warnings": None})
    )
    with pytest.raises(DaymetGridAuditError, match="different Daymet granule"):
        request_daymet_subset_url(
            granule,
            bbox_wgs84=(-118.67, 33.70, -118.15, 34.34),
            credential=EarthdataBearerToken("token", "EARTHDATA_TOKEN"),
            http_client=client,
        )
