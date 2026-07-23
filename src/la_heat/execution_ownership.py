"""Fail-closed ownership guard for one-machine portable model execution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

TRANSFER_OWNERSHIP_PATH: Final = Path(
    "data/interim/model_runs/transfer_ownership.json"
)
DISABLED_SOURCE_MARKER_PATH: Final = Path("RUN_DISABLED_TRANSFERRED_OUT.txt")


class ExecutionTransferredOutError(PermissionError):
    """Raised when this project copy no longer owns grouped-model execution."""


def assert_grouped_execution_authorized(project_root: str | Path) -> None:
    """Refuse execution after an audited handoff to a portable target."""

    root = Path(project_root).resolve()
    disabled_marker = root / DISABLED_SOURCE_MARKER_PATH
    if disabled_marker.is_file():
        raise ExecutionTransferredOutError(
            "Grouped-model execution was transferred to the portable target."
        )

    ownership_path = root / TRANSFER_OWNERSHIP_PATH
    if not ownership_path.exists():
        return
    if not ownership_path.is_file():
        raise ExecutionTransferredOutError(
            "The grouped-model transfer ownership marker is not a regular file."
        )
    try:
        payload = json.loads(ownership_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExecutionTransferredOutError(
            "The grouped-model transfer ownership marker is unreadable."
        ) from error
    if not isinstance(payload, dict) or payload.get("state") != "transferred_out":
        raise ExecutionTransferredOutError(
            "The grouped-model transfer ownership marker has an invalid state."
        )
    raise ExecutionTransferredOutError(
        "Grouped-model execution was transferred to the portable target."
    )
