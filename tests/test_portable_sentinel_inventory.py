from __future__ import annotations

from datetime import UTC, date, datetime

import geopandas as gpd
import numpy as np
from affine import Affine
from shapely.geometry import box

from la_heat.multicity.portable_sentinel_inventory import (
    PlanetaryComputerExactItemClient,
    acquisition_local_date,
    build_city_window_membership,
    eligible_tile_overlap_counts,
)
from la_heat.sentinel_inventory import PhysicalAcquisitionKey


def _key(acquired: datetime) -> PhysicalAcquisitionKey:
    return PhysicalAcquisitionKey(
        platform="sentinel-2a",
        acquired_utc=acquired,
        relative_orbit="1",
        normalized_datatake_id="TEST",
    )


def test_city_timezone_controls_local_date_and_lag_membership() -> None:
    acquired = datetime(2025, 1, 1, 7, 30, tzinfo=UTC)
    assert acquisition_local_date(acquired, "America/Phoenix") == date(2025, 1, 1)
    assert acquisition_local_date(acquired, "America/Los_Angeles") == date(
        2024, 12, 31
    )

    phoenix = build_city_window_membership(
        [date(2025, 1, 2)], [_key(acquired)], timezone="America/Phoenix"
    )
    los_angeles = build_city_window_membership(
        [date(2025, 1, 2)], [_key(acquired)], timezone="America/Los_Angeles"
    )
    assert [value.lag_days for value in phoenix] == [1]
    assert [value.lag_days for value in los_angeles] == [2]


def test_city_membership_uses_exact_d60_through_d1_window() -> None:
    targets = [date(2025, 5, 1)]
    acquisitions = [
        _key(datetime(2025, 3, 2, 18, tzinfo=UTC)),
        _key(datetime(2025, 4, 30, 18, tzinfo=UTC)),
        _key(datetime(2025, 5, 1, 18, tzinfo=UTC)),
    ]
    membership = build_city_window_membership(
        targets, acquisitions, timezone="America/Phoenix"
    )
    assert [value.lag_days for value in membership] == [60, 1]


def test_zero_support_tile_is_auditable_but_not_a_contributor() -> None:
    items = gpd.GeoDataFrame(
        {
            "item_id": ["outside", "inside"],
            "mgrs_tile": ["14RQT", "15RTN"],
        },
        geometry=[box(10, 10, 11, 11), box(0, 0, 1, 2)],
        crs="EPSG:3857",
    )
    counts = eligible_tile_overlap_counts(
        items,
        eligible_mask=np.ones((2, 2), dtype=bool),
        transform=Affine(1, 0, 0, 0, -1, 2),
        support_crs="EPSG:3857",
    )
    assert counts == {"14RQT": 0, "15RTN": 2}
    assert set(items["mgrs_tile"]) == {"14RQT", "15RTN"}
    assert {tile for tile, count in counts.items() if count > 0} == {"15RTN"}


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {"features": [{"id": "one"}, {"id": "two"}]}


class _Session:
    def post(self, *args: object, **kwargs: object) -> _Response:
        return _Response()


def test_exact_item_client_restores_requested_order() -> None:
    client = PlanetaryComputerExactItemClient(attempts=1)
    client.session = _Session()  # type: ignore[assignment]
    result = client.fetch_exact_items(["two", "one"])
    assert [feature["id"] for feature in result] == ["two", "one"]
