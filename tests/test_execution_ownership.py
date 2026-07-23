from __future__ import annotations

import json
from pathlib import Path

import pytest

from la_heat.execution_ownership import (
    ExecutionTransferredOutError,
    assert_grouped_execution_authorized,
)


def test_execution_is_allowed_without_a_transfer_marker(tmp_path: Path) -> None:
    assert_grouped_execution_authorized(tmp_path)


@pytest.mark.parametrize(
    "marker_payload",
    [
        {"state": "transferred_out", "transfer_id": "audit-id"},
        {"state": "unexpected"},
        "not-an-object",
    ],
)
def test_transfer_ownership_marker_blocks_execution(
    tmp_path: Path,
    marker_payload: object,
) -> None:
    marker = tmp_path / "data/interim/model_runs/transfer_ownership.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps(marker_payload), encoding="utf-8")
    with pytest.raises(ExecutionTransferredOutError):
        assert_grouped_execution_authorized(tmp_path)


def test_disabled_source_text_marker_blocks_execution(tmp_path: Path) -> None:
    (tmp_path / "RUN_DISABLED_TRANSFERRED_OUT.txt").write_text(
        "transferred",
        encoding="utf-8",
    )
    with pytest.raises(ExecutionTransferredOutError, match="portable target"):
        assert_grouped_execution_authorized(tmp_path)
