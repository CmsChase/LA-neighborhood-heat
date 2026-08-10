"""Resumable execution of authorized multicity Landsat target work units."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import pandas as pd

from la_heat.aligned_landsat import read_aligned_scene_from_hrefs
from la_heat.config import ResearchConfig, load_config
from la_heat.multicity.portable_predictor_inventory import (
    verify_portable_predictor_inventory,
)
from la_heat.multicity.target_authorization import (
    TargetExecutionAuthorization,
    ValuesAccessGate,
    authenticate_target_execution_authorization,
)
from la_heat.multicity.target_context import TargetCityContext, load_target_city_context
from la_heat.multicity.target_processor import (
    PlanetaryComputerSceneHydrator,
    SceneHydrator,
    SceneReader,
    aggregate_authorized_overpass,
    multicity_target_config_sha256,
)
from la_heat.multicity.target_transaction import stage_multicity_target_build_plan
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_sha256,
    code_runtime_fingerprint,
    parquet_file_record,
    sha256_file,
)

ALGORITHM_VERSION: Final = "multicity-target-engine-v1"
CACHE_OUTPUTS: Final = (
    "tract_date_qa.parquet",
    "date_summary.parquet",
    "scene_contributions.parquet",
)
CACHE_COMMIT: Final = "CACHE_COMMIT.json"
CITY_COMMIT: Final = "CITY_TARGETS_COMPLETE.json"
PIPELINE_FILES: Final = (
    "pyproject.toml",
    "src/la_heat/aligned_landsat.py",
    "src/la_heat/landsat.py",
    "src/la_heat/mosaic.py",
    "src/la_heat/multicity/target_authorization.py",
    "src/la_heat/multicity/target_context.py",
    "src/la_heat/multicity/target_engine.py",
    "src/la_heat/multicity/target_processor.py",
    "src/la_heat/target_aggregation.py",
    "src/la_heat/targets.py",
)


class TargetEngineError(RuntimeError):
    """Raised when an authorized work unit cannot preserve its frozen locks."""


def _claim_token(claim_id: str) -> str:
    return hashlib.sha256(claim_id.encode("utf-8")).hexdigest()[:20]


def _committed(payload: dict[str, Any]) -> bool:
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    return isinstance(recorded, str) and canonical_sha256(unsigned) == recorded


def _recorded_outputs_current(directory: Path, commit_name: str) -> dict[str, Any] | None:
    path = directory / commit_name
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TargetEngineError(f"Committed target cache is unreadable: {path}") from error
    if not isinstance(payload, dict) or not _committed(payload):
        raise TargetEngineError(f"Committed target cache is invalid: {path}")
    outputs = payload.get("output_files")
    if not isinstance(outputs, dict):
        raise TargetEngineError(f"Target cache output contract is invalid: {path}")
    for name, record in outputs.items():
        output = directory / str(name)
        if (
            not isinstance(record, dict)
            or not output.is_file()
            or output.stat().st_size != record.get("bytes")
            or sha256_file(output) != record.get("sha256")
        ):
            raise TargetEngineError(f"Target cache output failed its lock: {output}")
    return payload


@dataclass(slots=True)
class MulticityTargetEngine:
    project_root: Path
    plan: dict[str, Any]
    inventory: dict[str, Any]
    authorization: TargetExecutionAuthorization
    gate: ValuesAccessGate
    config: ResearchConfig
    pipeline_sha256: str
    cache_root: Path
    hydrator: SceneHydrator
    reader: SceneReader
    contexts: dict[str, TargetCityContext] = field(default_factory=dict)
    overpasses: dict[str, pd.DataFrame] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        project_root: str | Path,
        *,
        lane: str,
        authorization_path: str | Path,
        config_path: str | Path = "configs/research.toml",
        hydrator: SceneHydrator | None = None,
        reader: SceneReader | None = None,
    ) -> MulticityTargetEngine:
        root = Path(project_root).resolve()
        plan = stage_multicity_target_build_plan(root, check_only=True)
        authorization = authenticate_target_execution_authorization(
            root,
            authorization_path,
            expected_lane=lane,
            expected_plan_commit_sha256=str(plan["commit_sha256"]),
        )
        raw_config = Path(config_path)
        resolved_config = (
            raw_config.resolve()
            if raw_config.is_absolute()
            else (root / raw_config).resolve()
        )
        if not resolved_config.is_relative_to(root):
            raise TargetEngineError("Target configuration must stay inside the project.")
        config = load_config(resolved_config)
        if multicity_target_config_sha256(config) != authorization.target_config_sha256:
            raise TargetEngineError("Authorized target configuration changed.")
        inventory = verify_portable_predictor_inventory(
            root / "configs/multicity/portable_predictor_build.toml"
        )
        pipeline_sha256, _ = code_runtime_fingerprint(
            project_root=root,
            relative_paths=PIPELINE_FILES,
            algorithm_version=ALGORITHM_VERSION,
        )
        cache_root = (
            root
            / "data/interim/multicity/targets/claims"
            / f"{lane}-{_claim_token(authorization.claim_id)}"
        )
        return cls(
            project_root=root,
            plan=plan,
            inventory=inventory,
            authorization=authorization,
            gate=ValuesAccessGate(authorization),
            config=config,
            pipeline_sha256=pipeline_sha256,
            cache_root=cache_root,
            hydrator=(
                PlanetaryComputerSceneHydrator(
                    stac_api=str(config.raw["landsat"]["stac_api"])
                )
                if hydrator is None
                else hydrator
            ),
            reader=read_aligned_scene_from_hrefs if reader is None else reader,
        )

    def _context(self, city_id: str) -> TargetCityContext:
        if city_id not in self.authorization.city_ids:
            raise TargetEngineError(f"City is outside this authorization: {city_id}")
        if city_id not in self.contexts:
            self.contexts[city_id] = load_target_city_context(self.project_root, city_id)
        return self.contexts[city_id]

    def _overpass_table(self, city_id: str) -> pd.DataFrame:
        if city_id not in self.overpasses:
            record = self.inventory["output_tables"][f"{city_id}/overpasses"]
            self.overpasses[city_id] = pd.read_parquet(
                self.project_root / str(record["path"])
            )
        return self.overpasses[city_id]

    def _unit(self, unit_id: str) -> dict[str, Any]:
        matches = [
            unit
            for unit in self.plan["work_plan"]["units"]
            if unit.get("unit_id") == unit_id
        ]
        if len(matches) != 1:
            raise TargetEngineError(f"Unknown or duplicated target unit: {unit_id}")
        unit = dict(matches[0])
        if unit.get("lane") != self.authorization.lane:
            raise TargetEngineError("Target unit is outside the authorized lane.")
        return unit

    def _base_lock(self, city_id: str, context: TargetCityContext) -> dict[str, str]:
        return {
            "claim_id": self.authorization.claim_id,
            "authorization_commit_sha256": self.authorization.commit_sha256,
            "plan_commit_sha256": str(self.plan["commit_sha256"]),
            "target_config_sha256": self.authorization.target_config_sha256,
            "target_pipeline_sha256": self.pipeline_sha256,
            "city_id": city_id,
            "target_grid_sha256": context.grid.sha256,
            "target_context_locks_sha256": canonical_sha256(context.locks),
        }

    def _overpass_cache_lock(
        self,
        unit: dict[str, Any],
        context: TargetCityContext,
    ) -> dict[str, str]:
        return {
            **self._base_lock(str(unit["city_id"]), context),
            "overpass_id": str(unit["overpass_id"]),
            "overpass_source_sha256": str(unit["source_lock_sha256"]),
            "relationship_sha256": str(unit["relationship_sha256"]),
        }

    def execute_overpass(self, unit_id: str) -> dict[str, Any]:
        unit = self._unit(unit_id)
        if unit.get("kind") != "overpass_target":
            raise TargetEngineError("execute_overpass requires an overpass unit.")
        city_id = str(unit["city_id"])
        context = self._context(city_id)
        expected_lock = self._overpass_cache_lock(unit, context)
        directory = self.cache_root / "by_overpass" / city_id / str(unit["overpass_id"])
        if (directory / CACHE_COMMIT).exists():
            self.gate.before_first_value_access()
            observed = _recorded_outputs_current(directory, CACHE_COMMIT)
            if observed is None or observed.get("cache_lock") != expected_lock:
                raise TargetEngineError("Overpass cache belongs to another claim or lock.")
            return {"cache": "hit", "commit_sha256": observed["commit_sha256"]}

        table = self._overpass_table(city_id)
        selected = table.loc[
            table["overpass_id"].astype(str).eq(str(unit["overpass_id"]))
        ]
        if len(selected) != 1:
            raise TargetEngineError("Frozen overpass metadata changed.")
        row = selected.iloc[0]
        if (
            str(row["source_lock_sha256"]) != unit["source_lock_sha256"]
            or str(row["local_date"]) != unit["target_date"]
            or str(row["platform"]) != unit["platform"]
        ):
            raise TargetEngineError("Frozen overpass relationship changed.")
        scene_ids = tuple(str(value) for value in unit["scene_ids"])
        aggregated = aggregate_authorized_overpass(
            scene_ids=scene_ids,
            gate=self.gate,
            hydrator=self.hydrator,
            reader=self.reader,
            context=context,
            config=self.config,
            target_date=str(unit["target_date"]),
            overpass_id=str(unit["overpass_id"]),
            platform=str(unit["platform"]),
            union_city_coverage_fraction=float(row["union_city_coverage_fraction"]),
            target_config_sha256=self.authorization.target_config_sha256,
            tract_manifest_sha256=canonical_sha256(context.locks),
        )
        frames = {
            CACHE_OUTPUTS[0]: aggregated.tract_date_qa.copy(),
            CACHE_OUTPUTS[1]: pd.DataFrame([aggregated.summary]),
            CACHE_OUTPUTS[2]: aggregated.scene_contributions.copy(),
        }
        for frame in frames.values():
            frame.insert(0, "city_id", city_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / CACHE_COMMIT).unlink(missing_ok=True)
        output_files: dict[str, Any] = {}
        for filename, frame in frames.items():
            path = directory / filename
            atomic_parquet(frame, path)
            output_files[filename] = parquet_file_record(path, frame)
        commit: dict[str, Any] = {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "complete",
            "cache_lock": expected_lock,
            "output_files": output_files,
        }
        commit["commit_sha256"] = canonical_sha256(commit)
        atomic_json(commit, directory / CACHE_COMMIT)
        return {"cache": "built", "commit_sha256": commit["commit_sha256"]}

    def execute_city_compile(self, unit_id: str) -> dict[str, Any]:
        unit = self._unit(unit_id)
        if unit.get("kind") != "city_compile":
            raise TargetEngineError("execute_city_compile requires a compile unit.")
        city_id = str(unit["city_id"])
        context = self._context(city_id)
        expected_units = [
            candidate
            for candidate in self.plan["work_plan"]["units"]
            if candidate.get("kind") == "overpass_target"
            and candidate.get("city_id") == city_id
        ]
        if len(expected_units) != int(unit["expected_overpass_unit_count"]):
            raise TargetEngineError("City compile dependency count changed.")
        self.gate.before_first_value_access()
        targets: list[pd.DataFrame] = []
        summaries: list[pd.DataFrame] = []
        contributions: list[pd.DataFrame] = []
        overpass_commits: list[str] = []
        for expected in expected_units:
            directory = (
                self.cache_root
                / "by_overpass"
                / city_id
                / str(expected["overpass_id"])
            )
            observed = _recorded_outputs_current(directory, CACHE_COMMIT)
            if observed is None or observed.get("cache_lock") != self._overpass_cache_lock(
                expected, context
            ):
                raise TargetEngineError("City compile is waiting for overpass caches.")
            overpass_commits.append(str(observed["commit_sha256"]))
            targets.append(pd.read_parquet(directory / CACHE_OUTPUTS[0]))
            summaries.append(pd.read_parquet(directory / CACHE_OUTPUTS[1]))
            contributions.append(pd.read_parquet(directory / CACHE_OUTPUTS[2]))
        frames = {
            "targets.parquet": pd.concat(targets, ignore_index=True),
            "date_summary.parquet": pd.concat(summaries, ignore_index=True),
            "scene_contributions.parquet": pd.concat(contributions, ignore_index=True),
        }
        target_frame = frames["targets.parquet"]
        if (
            len(target_frame) != int(unit["expected_target_key_count"])
            or target_frame.duplicated(["city_id", "tract_geoid", "target_date"]).any()
        ):
            raise TargetEngineError("Compiled city target key universe changed.")
        directory = self.cache_root / "cities" / city_id
        compile_lock = {
            **self._base_lock(city_id, context),
            "overpass_commits_sha256": canonical_sha256(overpass_commits),
        }
        existing = None
        if (directory / CITY_COMMIT).exists():
            existing = _recorded_outputs_current(directory, CITY_COMMIT)
        if existing is not None:
            if existing.get("cache_lock") != compile_lock:
                raise TargetEngineError("City target compile belongs to another lock.")
            return {"cache": "hit", "commit_sha256": existing["commit_sha256"]}
        directory.mkdir(parents=True, exist_ok=True)
        output_files: dict[str, Any] = {}
        for filename, frame in frames.items():
            path = directory / filename
            atomic_parquet(frame, path)
            output_files[filename] = parquet_file_record(path, frame)
        commit: dict[str, Any] = {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "complete",
            "cache_lock": compile_lock,
            "output_files": output_files,
        }
        commit["commit_sha256"] = canonical_sha256(commit)
        atomic_json(commit, directory / CITY_COMMIT)
        return {"cache": "built", "commit_sha256": commit["commit_sha256"]}

    def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        kind = payload.get("kind")
        unit_id = str(payload.get("unit_id"))
        if kind == "overpass_target":
            return self.execute_overpass(unit_id)
        if kind == "city_compile":
            return self.execute_city_compile(unit_id)
        raise TargetEngineError("Final four-city merge requires both lane completions.")
