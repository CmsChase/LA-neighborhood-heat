from __future__ import annotations

from la_heat.multicity.m3_blind_target_v1 import EXPECTED_DATES


def test_combined_claim_has_exact_frozen_date_count() -> None:
    assert EXPECTED_DATES == {
        "seattle_wa": 54,
        "denver_co": 31,
        "atlanta_ga": 28,
        "miami_fl": 30,
    }
    assert sum(EXPECTED_DATES.values()) == 143
