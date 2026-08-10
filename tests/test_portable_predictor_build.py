from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from la_heat.multicity.portable_predictor_build import (
    CITY_IDS,
    StatusTracker,
    _merge_components,
    _safe_message,
    build_work_plan,
)


def _write_support_counts(root: Path, counts: dict[str, int]) -> None:
    support_root = root / (
        "data/processed/multicity/missing_support_calibration_evidence_v1/worldcover"
    )
    for city_id, count in counts.items():
        path = support_root / city_id / "tract_eligible_support.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"eligible_cell_count": [count]}).to_parquet(path, index=False)


def test_work_plan_uses_real_distance_chunk_counts(tmp_path: Path) -> None:
    _write_support_counts(
        tmp_path,
        {
            "los_angeles_ca": 100_001,
            "phoenix_az": 1,
            "houston_tx": 200_000,
            "chicago_il": 0,
        },
    )

    plan = build_work_plan(tmp_path)

    distance = [unit.task_id for unit in plan if unit.phase == "water_distance"]
    assert distance == [
        "gshhg:los_angeles_ca:0",
        "gshhg:los_angeles_ca:1",
        "gshhg:phoenix_az:0",
        "gshhg:houston_tx:0",
        "gshhg:houston_tx:1",
    ]
    assert len(plan) == 35 + len(distance)


def test_status_tracker_resumes_completed_units(tmp_path: Path) -> None:
    _write_support_counts(tmp_path, {city_id: 1 for city_id in CITY_IDS})
    plan = build_work_plan(tmp_path)
    runtime = tmp_path / "runtime"
    first = StatusTracker(runtime, plan)
    first.start(plan[0].task_id)
    first.finish(plan[0].task_id)

    resumed = StatusTracker(runtime, plan)
    resumed.write("paused")
    payload = json.loads((runtime / "status.json").read_text(encoding="utf-8"))

    assert resumed.completed == {plan[0].task_id}
    assert payload["completed"] == 1
    assert payload["total"] == len(plan)
    assert payload["external_targets_read"] is False


def test_merge_publishes_exact_41_non_sentinel_features(tmp_path: Path) -> None:
    static_names = [f"static_{index}" for index in range(18)]
    calendar_names = ["calendar_sin", "calendar_cos"]
    daymet_names = [f"daymet_{index}" for index in range(21)]
    sentinel_names = [f"sentinel_{index}" for index in range(5)]
    contract_path = tmp_path / (
        "manifests/multicity/reviews/portable_predictor_contract/"
        "PORTABLE_PREDICTOR_CONTRACT.json"
    )
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(
        json.dumps(
            {
                "feature_registry": {
                    "feature_order": [
                        *static_names,
                        *calendar_names,
                        *daymet_names,
                        *sentinel_names,
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    date = pd.Timestamp("2025-07-01")
    keys = pd.DataFrame(
        {
            "city_id": list(CITY_IDS),
            "tract_geoid": pd.Series([f"g{index}" for index in range(4)], dtype="string"),
            "target_date": [date] * 4,
            "overpass_id": ["pass"] * 4,
            "platform": ["landsat-9"] * 4,
        }
    )
    inventory_path = tmp_path / (
        "data/processed/multicity/portable_predictors/inventory/predictor_keys.parquet"
    )
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    keys.to_parquet(inventory_path, index=False)
    for index, city_id in enumerate(CITY_IDS):
        output = tmp_path / (
            f"data/processed/multicity/portable_predictors/components/{city_id}"
        )
        output.mkdir(parents=True, exist_ok=True)
        key = {"tract_geoid": pd.Series([f"g{index}"], dtype="string")}
        pd.DataFrame({**key, **{name: [1.0] for name in static_names}}).to_parquet(
            output / "static_features.parquet", index=False
        )
        pd.DataFrame(
            {
                **key,
                "target_date": [date],
                **{name: [2.0] for name in calendar_names},
            }
        ).to_parquet(output / "calendar_features.parquet", index=False)
        pd.DataFrame(
            {
                **key,
                "target_date": [date],
                **{name: [3.0] for name in daymet_names},
            }
        ).to_parquet(output / "daymet_features.parquet", index=False)

    payload = _merge_components(tmp_path)
    result = pd.read_parquet(
        tmp_path
        / "data/processed/multicity/portable_predictors/components/"
        "predictors_static_calendar_daymet.parquet"
    )

    assert payload["feature_count"] == 41
    assert len(result) == 4
    assert result[static_names + calendar_names + daymet_names].notna().all().all()
    assert not set(sentinel_names).intersection(result.columns)


def test_safe_message_removes_url_queries_and_bearer_values() -> None:
    message = _safe_message("GET https://example.test/data?token=secret Bearer abc.def")
    assert "secret" not in message
    assert "abc.def" not in message
