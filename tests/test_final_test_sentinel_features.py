from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from la_heat.final_test_sentinel_features import (
    FinalTestSentinelEngineAlreadyRunningError,
    FinalTestSentinelEngineLock,
    FinalTestSentinelFeatureError,
    _authenticate_snapshot_files,
    _fixed_support_grid_identity,
    decode_cog_reflectance,
    execute_acquisition_queue,
    validate_exact_lag_membership,
    validate_fixed_support_arrays,
)
from la_heat.landsat import zonal_mask_identity_hashes
from la_heat.provenance import canonical_sha256


def test_cog_calibration_is_scale_then_offset_and_keeps_negative_values() -> None:
    values = np.array([[0, 500, 1_000, 65_535]], dtype=np.float32)
    decoded = decode_cog_reflectance(values, scale=0.0001, offset=-0.1)
    assert np.isnan(decoded[0, 0])
    assert decoded[0, 1] == pytest.approx(-0.05)
    assert decoded[0, 2] == pytest.approx(0.0, abs=1e-7)
    assert np.isnan(decoded[0, 3])


@pytest.mark.parametrize(
    ("acquired", "lag"),
    [("2025-05-06", 0), ("2025-05-07", -1), ("2025-03-06", 61)],
)
def test_exact_lag_membership_rejects_same_day_future_and_day_61(
    acquired: str, lag: int
) -> None:
    membership = pd.DataFrame(
        {
            "target_date": ["2025-05-06"],
            "physical_acquisition_id": ["a"],
            "acquisition_local_date": [acquired],
            "lag_days": [lag],
        }
    )
    with pytest.raises(FinalTestSentinelFeatureError, match="d-60"):
        validate_exact_lag_membership(membership)


def test_exact_lag_membership_accepts_boundaries() -> None:
    membership = pd.DataFrame(
        {
            "target_date": ["2025-05-06", "2025-05-06"],
            "physical_acquisition_id": ["a", "b"],
            "acquisition_local_date": ["2025-05-05", "2025-03-07"],
            "lag_days": [1, 60],
        }
    )
    validate_exact_lag_membership(membership)


def test_exact_lag_membership_rejects_missing_expected_pair() -> None:
    membership = pd.DataFrame(
        {
            "target_date": ["2025-05-06"],
            "physical_acquisition_id": ["a"],
            "acquisition_local_date": ["2025-05-05"],
            "lag_days": [1],
        }
    )
    with pytest.raises(FinalTestSentinelFeatureError, match="exact frozen"):
        validate_exact_lag_membership(
            membership,
            acquisition_dates={"a": "2025-05-05", "b": "2025-05-04"},
            target_dates=["2025-05-06"],
        )


def test_exact_lag_membership_rejects_foreign_acquisition() -> None:
    membership = pd.DataFrame(
        {
            "target_date": ["2025-05-06"],
            "physical_acquisition_id": ["foreign"],
            "acquisition_local_date": ["2025-05-05"],
            "lag_days": [1],
        }
    )
    with pytest.raises(FinalTestSentinelFeatureError, match="exact frozen"):
        validate_exact_lag_membership(
            membership,
            acquisition_dates={"a": "2025-05-05"},
            target_dates=["2025-05-06"],
        )


def test_exact_lag_membership_rejects_wrong_acquisition_date() -> None:
    membership = pd.DataFrame(
        {
            "target_date": ["2025-05-06"],
            "physical_acquisition_id": ["a"],
            "acquisition_local_date": ["2025-05-04"],
            "lag_days": [2],
        }
    )
    with pytest.raises(FinalTestSentinelFeatureError, match="exact frozen"):
        validate_exact_lag_membership(
            membership,
            acquisition_dates={"a": "2025-05-05"},
            target_dates=["2025-05-06"],
        )


def test_engine_lock_is_single_instance_and_released(tmp_path: Path) -> None:
    lock_path = tmp_path / "engine.lock"
    with FinalTestSentinelEngineLock(lock_path):
        with pytest.raises(FinalTestSentinelEngineAlreadyRunningError):
            with FinalTestSentinelEngineLock(lock_path):
                pass
    with FinalTestSentinelEngineLock(lock_path):
        pass


def test_fixed_support_validates_global_and_per_tract_hashes() -> None:
    zones = np.array([[1, 1, 2], [1, 2, 2]], dtype=np.int32)
    eligible = np.array([[True, False, True], [True, True, False]])
    geoids = ("g1", "g2")
    identity = zonal_mask_identity_hashes(
        zones,
        eligible,
        zone_count=2,
        grid_identity="grid",
    )
    audit = pd.DataFrame(
        {
            "tract_geoid": geoids,
            "eligible_pixel_count_static": [2, 2],
            "eligible_pixel_identity_sha256": identity,
        }
    )
    counts, observed_identity = validate_fixed_support_arrays(
        zones=zones,
        eligible_land=eligible,
        tract_geoids=geoids,
        audit=audit,
        grid_identity="grid",
        expected_zone_sha256=hashlib.sha256(zones.tobytes()).hexdigest(),
        expected_land_sha256=hashlib.sha256(
            np.packbits(eligible.ravel()).tobytes()
        ).hexdigest(),
    )
    assert counts == {"g1": 2, "g2": 2}
    assert observed_identity == dict(zip(geoids, identity, strict=True))


def test_fixed_support_rejects_one_changed_land_pixel() -> None:
    zones = np.array([[1, 2]], dtype=np.int32)
    eligible = np.array([[True, True]])
    audit = pd.DataFrame(
        {
            "tract_geoid": ["g1", "g2"],
            "eligible_pixel_count_static": [1, 1],
            "eligible_pixel_identity_sha256": zonal_mask_identity_hashes(
                zones,
                eligible,
                zone_count=2,
                grid_identity="grid",
            ),
        }
    )
    changed = eligible.copy()
    changed[0, 1] = False
    with pytest.raises(FinalTestSentinelFeatureError, match="eligible-land hash"):
        validate_fixed_support_arrays(
            zones=zones,
            eligible_land=changed,
            tract_geoids=("g1", "g2"),
            audit=audit,
            grid_identity="grid",
            expected_zone_sha256=hashlib.sha256(zones.tobytes()).hexdigest(),
            expected_land_sha256=hashlib.sha256(
                np.packbits(eligible.ravel()).tobytes()
            ).hexdigest(),
        )


def test_fixed_support_recreates_unhashed_frozen_grid_identity() -> None:
    identity = "grid|zone=zones|land=land"
    expected = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    assert (
        _fixed_support_grid_identity(
            grid_definition_sha256="grid",
            zone_raster_sha256="zones",
            static_land_mask_sha256="land",
            expected_identity_sha256=expected,
        )
        == identity
    )
    with pytest.raises(FinalTestSentinelFeatureError, match="Combined fixed-support"):
        _fixed_support_grid_identity(
            grid_definition_sha256="grid",
            zone_raster_sha256="zones",
            static_land_mask_sha256="land",
            expected_identity_sha256="0" * 64,
        )


def test_fixed_support_rejects_identity_sha_as_zonal_seed() -> None:
    zones = np.array([[1, 1, 2], [1, 2, 2]], dtype=np.int32)
    eligible = np.array([[True, False, True], [True, True, False]])
    identity = "grid|zone=zones|land=land"
    audit = pd.DataFrame(
        {
            "tract_geoid": ["g1", "g2"],
            "eligible_pixel_count_static": [2, 2],
            "eligible_pixel_identity_sha256": zonal_mask_identity_hashes(
                zones,
                eligible,
                zone_count=2,
                grid_identity=identity,
            ),
        }
    )
    with pytest.raises(FinalTestSentinelFeatureError, match="pixel identities"):
        validate_fixed_support_arrays(
            zones=zones,
            eligible_land=eligible,
            tract_geoids=("g1", "g2"),
            audit=audit,
            grid_identity=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
            expected_zone_sha256=hashlib.sha256(zones.tobytes()).hexdigest(),
            expected_land_sha256=hashlib.sha256(
                np.packbits(eligible.ravel()).tobytes()
            ).hexdigest(),
        )


def test_snapshot_authentication_rejects_tampering(tmp_path: Path) -> None:
    snapshot = tmp_path / "item.json"
    snapshot.write_text('{"id":"item"}\n', encoding="utf-8")
    record = {
        "item_id": "item",
        "filename": snapshot.name,
        "sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        "bytes": snapshot.stat().st_size,
    }
    assert _authenticate_snapshot_files([record], raw_directory=tmp_path) == {
        "item": snapshot.resolve()
    }
    snapshot.write_text('{"id":"changed"}\n', encoding="utf-8")
    with pytest.raises(FinalTestSentinelFeatureError, match="byte/set lock"):
        _authenticate_snapshot_files([record], raw_directory=tmp_path)
    assert canonical_sha256([record])


def test_queue_resumes_cache_and_continues_after_one_failure(
    tmp_path: Path,
) -> None:
    rows = [SimpleNamespace(identifier=value) for value in ("cached", "bad", "good")]
    called: list[str] = []

    def process(row: SimpleNamespace) -> None:
        called.append(row.identifier)
        if row.identifier == "bad":
            raise RuntimeError("synthetic")

    status = execute_acquisition_queue(
        rows,
        physical_id=lambda row: row.identifier,
        cache_is_current=lambda row: row.identifier == "cached",
        process=process,
        status_path=tmp_path / "status.json",
        pause_marker=tmp_path / "PAUSE_REQUESTED",
        workers=6,
        max_attempts=1,
    )
    assert status["state"] == "incomplete_with_failures"
    assert status["completed"] == 2
    assert status["failed"] == 1
    assert set(called) == {"bad", "good"}
    persisted = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert persisted["total"] == 3
    assert persisted["running"] == 0
    assert persisted["current"] == []


def test_queue_honors_existing_pause_marker_without_starting_work(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "PAUSE_REQUESTED"
    marker.touch()
    called: list[str] = []
    status = execute_acquisition_queue(
        [SimpleNamespace(identifier="a")],
        physical_id=lambda row: row.identifier,
        cache_is_current=lambda _row: False,
        process=lambda row: called.append(row.identifier),
        status_path=tmp_path / "status.json",
        pause_marker=marker,
        workers=8,
        max_attempts=2,
    )
    assert status["state"] == "paused"
    assert status["completed"] == 0
    assert status["running"] == 0
    assert called == []
