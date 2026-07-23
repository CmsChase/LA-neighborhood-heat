"""Configuration loading and final-test access controls."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ResearchConfig:
    """Thin typed wrapper around the versioned TOML configuration."""

    raw: dict[str, Any]
    path: Path

    @property
    def final_test_year(self) -> int:
        return int(self.raw["study"]["final_test_year"])

    @property
    def final_test_unlocked(self) -> bool:
        return bool(self.raw["study"]["unlock_final_test"])

    def require_final_test_access(self) -> None:
        if not self.final_test_unlocked:
            raise PermissionError(
                f"Final-test labels for {self.final_test_year} are locked. "
                "Freeze the complete analysis plan and record the unlock in "
                "docs/DECISION_LOG.md before changing unlock_final_test."
            )


def load_config(path: str | Path) -> ResearchConfig:
    config_path = Path(path)
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    return ResearchConfig(raw=raw, path=config_path.resolve())

