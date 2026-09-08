from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from la_heat.multicity import m3_blind_predictor_sentinel_static_runtime_v1 as runtime


def test_launch_authorization_is_exact_and_value_free(monkeypatch: Any) -> None:
    root = Path(__file__).resolve().parents[1]
    original_open = Path.open
    original_stat = Path.stat

    def guarded_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        assert path.suffix.lower() not in runtime._VALUE_SUFFIXES
        return original_open(path, *args, **kwargs)

    def guarded_stat(path: Path, *args: Any, **kwargs: Any) -> Any:
        assert path.suffix.lower() not in runtime._VALUE_SUFFIXES
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(Path, "stat", guarded_stat)
    payload = runtime.build_launch_authorization(root)
    assert payload["task_plan"]["sentinel_acquisitions"] == 539
    assert payload["task_plan"]["static_tasks"] == 14
    assert payload["task_plan"]["total"] == 553
    assert payload["runtime_contract"]["maximum_download_threads"] == 2
    assert payload["permissions"]["read_exact_scope_bound_sentinel_and_static_values"]
    assert payload["permissions"]["read_daymet_values_or_network"] is False
    assert payload["permissions"]["read_landsat_asset_href_thermal_qa_or_target"] is False


@pytest.mark.parametrize(
    "url",
    [
        "http://planetarycomputer.microsoft.com/a.tif",
        "https://evil.example/a.tif",
        "https://blob.core.windows.net.evil.example/a.tif",
    ],
)
def test_sentinel_url_allowlist_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(runtime.M3BlindPredictorSentinelStaticRuntimeError):
        runtime._validate_sentinel_url(url)


def test_status_before_start_is_nonmutating(tmp_path: Path) -> None:
    assert runtime.read_status(tmp_path)["state"] == "not_started"
    assert runtime.read_status(tmp_path)["counts"] == {"complete": 0, "total": 553}


def test_retry_boundary_is_bounded() -> None:
    attempts = 0

    def fail_twice() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OSError("temporary")
        return "ok"

    assert runtime._with_attempts(fail_twice, maximum_attempts=3) == "ok"
    assert attempts == 3
