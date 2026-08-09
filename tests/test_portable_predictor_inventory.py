import pandas as pd

from la_heat.multicity.portable_predictor_inventory import _key_universe


def test_key_universe_crosses_only_primary_dates_with_city_tracts() -> None:
    tracts = pd.DataFrame({"GEOID": ["0002", "0001"]})
    overpasses = pd.DataFrame(
        {
            "local_date": ["2025-05-01", "2025-05-17"],
            "overpass_id": ["a", "b"],
            "platform": ["landsat-8", "landsat-9"],
            "primary_eligible": [True, False],
        }
    )

    keys = _key_universe("phoenix_az", tracts, overpasses)  # type: ignore[arg-type]

    assert keys[["city_id", "tract_geoid"]].astype(str).values.tolist() == [
        ["phoenix_az", "0001"],
        ["phoenix_az", "0002"],
    ]
    assert keys["target_date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2025-05-01",
        "2025-05-01",
    ]
