import json

import pandas as pd

from la_heat.calendar_feature_stage import (
    CALENDAR_FEATURE_FILENAME,
    CALENDAR_PROVENANCE_FILENAME,
    build_calendar_feature_artifacts,
)
from la_heat.provenance import canonical_sha256


def test_calendar_stage_commits_exact_full_keys_and_provenance(tmp_path) -> None:
    keys = pd.DataFrame(
        {
            "tract_geoid": pd.Series(["a", "b", "a", "b"], dtype="string"),
            "target_date": pd.to_datetime(
                ["2023-07-01", "2023-07-01", "2024-07-01", "2024-07-01"]
            ),
        }
    )
    universe_path = tmp_path / "feature_key_universe.parquet"
    output = tmp_path / "calendar"
    keys.to_parquet(universe_path, index=False)

    payload = build_calendar_feature_artifacts(universe_path, output)

    table = pd.read_parquet(output / CALENDAR_FEATURE_FILENAME)
    pd.testing.assert_frame_equal(
        table[["tract_geoid", "target_date"]], keys, check_dtype=False
    )
    assert payload["row_count"] == 4
    assert payload["date_count"] == 2
    assert payload["tract_count"] == 2
    assert payload["phase2_promoted"] is False
    marker = json.loads(
        (output / CALENDAR_PROVENANCE_FILENAME).read_text(encoding="utf-8")
    )
    commit = marker.pop("commit_sha256")
    assert canonical_sha256(marker) == commit
