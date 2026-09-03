from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from la_heat.multicity.m3_source_predictor_sentinel_game_laptop_v1 import (
    CODE_PATHS,
    DOWNLOAD_THREADS,
    _code_records,
)


def test_game_laptop_contract_is_four_asset_threads() -> None:
    assert DOWNLOAD_THREADS == 4
    assert CODE_PATHS[-1] == Path("START_M3_PREDICTOR_GAME_LAPTOP.cmd")


def test_archived_launcher_can_restore_exact_historical_code_identity(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    authorization = json.loads((
        root / "manifests/multicity/next_experiment/"
        "M3_SOURCE_PREDICTOR_SENTINEL_GAME_LAPTOP_V1_AUTHORIZATION.json"
    ).read_text(encoding="utf-8"))
    expected = authorization["code_identity"]["files"]
    for record in expected:
        relative = Path(record["path"])
        source = root / relative
        if relative == CODE_PATHS[-1]:
            source = root / "tools/windows/legacy" / relative
        content = source.read_bytes()
        assert len(content) == record["bytes"]
        assert hashlib.sha256(content).hexdigest() == record["sha256"]
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)

    # Verify original path/byte bindings only: no worker, queue, or values opened.
    assert _code_records(tmp_path) == expected
