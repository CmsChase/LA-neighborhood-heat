"""Path isolation for the continuation study.

The continuation must never write into the completed LA final-test transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from la_heat.multicity.config import MulticityPlan

PROHIBITED_PATH_PARTS: Final = {
    "final_test_2025",
    "final_evaluation",
    "model_lock",
}


@dataclass(frozen=True)
class CityWorkspace:
    raw: Path
    interim: Path
    processed: Path
    manifests: Path


@dataclass(frozen=True)
class MulticityWorkspace:
    project_root: Path
    experiment_id: str
    raw_root: Path
    interim_root: Path
    processed_root: Path
    manifest_root: Path
    report_root: Path
    export_root: Path

    @classmethod
    def from_plan(cls, plan: MulticityPlan) -> MulticityWorkspace:
        project_root = plan.path.parents[2].resolve()
        paths = plan.raw["paths"]
        workspace = cls(
            project_root=project_root,
            experiment_id=plan.experiment_id,
            raw_root=(project_root / paths["raw_root"]).resolve(),
            interim_root=(project_root / paths["interim_root"]).resolve(),
            processed_root=(project_root / paths["processed_root"]).resolve(),
            manifest_root=(project_root / paths["manifest_root"]).resolve(),
            report_root=(project_root / paths["report_root"]).resolve(),
            export_root=(project_root / paths["export_root"]).resolve(),
        )
        workspace.assert_isolated()
        return workspace

    def assert_isolated(self) -> None:
        for path in (
            self.raw_root,
            self.interim_root,
            self.processed_root,
            self.manifest_root,
            self.report_root,
            self.export_root,
        ):
            if not path.is_relative_to(self.project_root):
                raise ValueError(f"Continuation path escapes project root: {path}")
            lowered = {part.lower() for part in path.parts}
            overlap = lowered & PROHIBITED_PATH_PARTS
            if overlap:
                raise ValueError(
                    f"Continuation path overlaps frozen Phase I names: {sorted(overlap)}"
                )

    def city(self, city_id: str) -> CityWorkspace:
        return CityWorkspace(
            raw=self.raw_root / city_id,
            interim=self.interim_root / city_id,
            processed=self.processed_root / city_id,
            manifests=self.manifest_root / "cities" / city_id,
        )

    @property
    def experiment_manifests(self) -> Path:
        return self.manifest_root / "experiments" / self.experiment_id

    @property
    def experiment_reports(self) -> Path:
        return self.report_root / self.experiment_id

    @property
    def experiment_exports(self) -> Path:
        return self.export_root / self.experiment_id
