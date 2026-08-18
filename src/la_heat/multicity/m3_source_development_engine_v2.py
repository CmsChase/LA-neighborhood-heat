"""Offline executor for the authenticated M3 source integrity overlay."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import pandas as pd

from la_heat.config import load_config
from la_heat.multicity import m3_source_development_engine as engine_v1
from la_heat.multicity.m3_development_protocol_lock import (
    authenticate_m3_development_protocol_lock,
)
from la_heat.multicity.m3_source_acquisition_amendment import (
    authenticate_m3_source_acquisition_amendment,
)
from la_heat.multicity.m3_source_asset_cache import authenticate_plan
from la_heat.multicity.m3_source_development_engine import M3SourceDevelopmentEngine
from la_heat.multicity.m3_source_development_runtime import (
    BLIND_CITY_IDS,
    QA_CANDIDATES,
    SOURCE_CITY_IDS,
)
from la_heat.multicity.m3_source_development_runtime_v2 import (
    RunnerSettingsV2,
    load_runner_settings_v2,
)
from la_heat.multicity.m3_source_development_worker import OFFLINE_PHASE
from la_heat.multicity.m3_source_integrity_v2 import (
    AUTHORIZATION_PATH,
    EXPECTED_CONTENT_COUNT,
    EXPECTED_OVERPASS_COUNT,
    EXPECTED_SCENE_COUNT,
    _inside,
    _read_committed_json,
    _write_once,
    authenticate_logical_global_cache,
    authenticate_m3_source_integrity_v2_authorization,
    authenticate_m3_source_integrity_v2_value_gate,
    finalize_logical_global_cache,
    finalize_retained_scene,
    load_retained_scene_arrays,
)
from la_heat.multicity.m3_source_offline_qa import reconstruct_overpass_candidates
from la_heat.multicity.target_context import load_target_city_context
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_sha256,
    parquet_file_record,
)

ALGORITHM_VERSION: Final = "m3-source-development-engine-v2"
LOGICAL_PHASE: Final = "logical_cache_finalize"
QA_PHASE: Final = "offline_qa_rebuild"
PHASES: Final = (LOGICAL_PHASE, QA_PHASE)


class M3SourceDevelopmentV2Error(RuntimeError):
    """Raised when the v2 executor crosses its local-only authorization."""


def _semantic_sha256(frame: pd.DataFrame) -> str:
    serializable = json.loads(
        frame.reset_index(drop=True).to_json(
            orient="split",
            date_format="iso",
            date_unit="ns",
            double_precision=15,
            force_ascii=False,
        )
    )
    return canonical_sha256(serializable)


def _v2_file_record(path: Path, frame: pd.DataFrame) -> dict[str, Any]:
    return {
        **parquet_file_record(path, frame),
        "semantic_sha256": _semantic_sha256(frame),
    }


def _write_v2_frames(
    directory: Path,
    frames: Mapping[str, pd.DataFrame],
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for relative, frame in frames.items():
        path = engine_v1._relative_output(directory, relative)
        atomic_parquet(frame, path)
        observed = pd.read_parquet(path)
        records[relative] = _v2_file_record(path, observed)
    return records


def _expected_output_paths(*, city_level: bool) -> tuple[str, ...]:
    names = (
        ("targets.parquet", "date_summary.parquet", "scene_contributions.parquet")
        if city_level
        else (
            "tract_date_qa.parquet",
            "date_summary.parquet",
            "scene_contributions.parquet",
        )
    )
    return tuple(f"{candidate_id}/{name}" for candidate_id in QA_CANDIDATES for name in names)


def _reject_remote_or_blind_frame(frame: pd.DataFrame) -> None:
    def check(value: object) -> None:
        if isinstance(value, str):
            lowered = value.lower()
            if lowered.startswith(("http://", "https://")) or value in BLIND_CITY_IDS:
                raise M3SourceDevelopmentV2Error(
                    "Offline QA output contains a remote reference or blind city."
                )
        elif isinstance(value, Mapping):
            for nested in value.values():
                check(nested)
        elif isinstance(value, (list, tuple, set)):
            for nested in value:
                check(nested)
        elif hasattr(value, "tolist"):
            converted = value.tolist()
            if converted is not value:
                check(converted)

    for value in frame.select_dtypes(include=["object", "string"]).to_numpy().ravel():
        check(value)


def _read_v2_output(
    directory: Path,
    filename: str,
    *,
    expected_state: str,
    expected_paths: Sequence[str],
) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    commit = engine_v1._read_output_commit(directory, filename)
    if commit.get("state") != expected_state:
        raise M3SourceDevelopmentV2Error("QA output state changed.")
    raw_records = commit.get("output_files")
    if not isinstance(raw_records, Mapping) or tuple(raw_records) != tuple(expected_paths):
        raise M3SourceDevelopmentV2Error("QA output file set or order changed.")
    engine_v1._reject_remote_strings(commit)
    serialized = json.dumps(commit, ensure_ascii=False)
    if any(city_id in serialized for city_id in BLIND_CITY_IDS):
        raise M3SourceDevelopmentV2Error("QA commit contains a blind city.")
    frames: dict[str, pd.DataFrame] = {}
    for relative in expected_paths:
        path = engine_v1._relative_output(directory, relative)
        frame = pd.read_parquet(path)
        record = raw_records[relative]
        if not isinstance(record, Mapping) or dict(record) != _v2_file_record(path, frame):
            raise M3SourceDevelopmentV2Error(
                f"QA parquet byte/schema/semantic record changed: {path}"
            )
        _reject_remote_or_blind_frame(frame)
        frames[relative] = frame
    return commit, frames


def _require_constant_column(
    frame: pd.DataFrame,
    column: str,
    expected: object,
    *,
    allow_empty: bool = True,
) -> None:
    if column not in frame.columns or (frame.empty and not allow_empty):
        raise M3SourceDevelopmentV2Error(f"QA frame lacks required {column!r} values.")
    if not frame.empty and set(frame[column].astype(str)) != {str(expected)}:
        raise M3SourceDevelopmentV2Error(f"QA frame changed {column!r} identity.")


def _validate_candidate_frame(
    frame: pd.DataFrame,
    *,
    relative: str,
    city_id: str,
    candidate_id: str,
    expected_dates: set[str],
    expected_overpasses: set[str],
    expected_scenes: set[str],
    expected_config_sha256: str,
    city_level: bool,
) -> None:
    _require_constant_column(frame, "city_id", city_id)
    _require_constant_column(frame, "candidate_id", candidate_id)
    if "target_date" not in frame or "overpass_id" not in frame:
        raise M3SourceDevelopmentV2Error("QA frame lacks its date/overpass identity.")
    dates = set(frame["target_date"].astype(str))
    overpasses = set(frame["overpass_id"].astype(str))
    if not dates.issubset(expected_dates) or not overpasses.issubset(expected_overpasses):
        raise M3SourceDevelopmentV2Error("QA frame crossed its frozen source cohort.")
    name = relative.rsplit("/", 1)[-1]
    if name == "date_summary.parquet":
        expected_rows = len(expected_overpasses) if city_level else 1
        if (
            len(frame) != expected_rows
            or not pd.api.types.is_bool_dtype(frame["date_usable"])
            or not frame["date_usable"].isin([True, False]).all()
        ):
            raise M3SourceDevelopmentV2Error("QA date-summary semantics changed.")
        observed_keys = set(
            zip(
                frame["target_date"].astype(str),
                frame["overpass_id"].astype(str),
                strict=True,
            )
        )
        if len(observed_keys) != expected_rows:
            raise M3SourceDevelopmentV2Error("QA date summaries are duplicated.")
    elif name in {"tract_date_qa.parquet", "targets.parquet"}:
        if "tract_geoid" not in frame or frame.duplicated(
            ["city_id", "tract_geoid", "target_date"]
        ).any():
            raise M3SourceDevelopmentV2Error("QA tract/date keys changed or duplicated.")
    elif name == "scene_contributions.parquet":
        if "scene_id" not in frame or not set(frame["scene_id"].astype(str)).issubset(
            expected_scenes
        ):
            raise M3SourceDevelopmentV2Error("QA scene contributions changed cohort.")
    else:  # pragma: no cover - exact paths are checked by the caller.
        raise M3SourceDevelopmentV2Error("Unknown QA output file.")
    if "config_sha256" in frame and not frame.empty:
        if set(frame["config_sha256"].astype(str)) != {expected_config_sha256}:
            raise M3SourceDevelopmentV2Error("QA candidate configuration changed.")


def _authenticate_overpass_output(
    engine: M3SourceDevelopmentEngineV2,
    row: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    engine.before_value_access()
    city_id = str(row["city_id"])
    overpass_id = str(row["overpass_id"])
    directory = engine.settings.qa_output_root / "by_overpass" / city_id / overpass_id
    commit, frames = _read_v2_output(
        directory,
        engine_v1.OVERPASS_COMMIT,
        expected_state="qa_overpass_complete",
        expected_paths=_expected_output_paths(city_level=False),
    )
    expected_lock = engine._overpass_lock(row)
    if (
        commit.get("schema_version") != 2
        or commit.get("algorithm_version") != ALGORITHM_VERSION
        or commit.get("cache_lock") != expected_lock
        or tuple(commit.get("candidate_ids", ())) != QA_CANDIDATES
        or commit.get("raw_scene_arrays_loaded_once_per_overpass") is not True
        or commit.get("network_requests_performed") != 0
        or commit.get("href_reads_performed") != 0
        or commit.get("physical_cache_mutated") is not False
        or commit.get("model_fit_or_selection_performed") is not False
        or commit.get("blind_test_city_accessed") is not False
    ):
        raise M3SourceDevelopmentV2Error("Overpass QA commit audit changed.")
    expected_date = engine_v1._target_date(row)
    expected_scenes = {str(value) for value in row["scene_ids"]}
    config_hashes = expected_lock["candidate_config_sha256s"]
    for relative, frame in frames.items():
        candidate_id = relative.split("/", 1)[0]
        _validate_candidate_frame(
            frame,
            relative=relative,
            city_id=city_id,
            candidate_id=candidate_id,
            expected_dates={expected_date},
            expected_overpasses={overpass_id},
            expected_scenes=expected_scenes,
            expected_config_sha256=str(config_hashes[candidate_id]),
            city_level=False,
        )
    available: dict[str, set[str]] = {}
    for candidate_id in QA_CANDIDATES:
        targets = frames[f"{candidate_id}/tract_date_qa.parquet"]
        if "target_available" not in targets:
            raise M3SourceDevelopmentV2Error("QA target availability is missing.")
        available[candidate_id] = set(
            targets.loc[targets["target_available"].fillna(False), "tract_geoid"].astype(
                str
            )
        )
    if not (
        available["3k"]
        <= available["4k"]
        <= available["6k"]
        <= available["none"]
    ):
        raise M3SourceDevelopmentV2Error("QA support is not monotonically nested.")
    return commit, frames


def _compiled_city_frames(
    grouped: Mapping[str, Mapping[str, Sequence[pd.DataFrame]]],
) -> tuple[dict[str, pd.DataFrame], dict[str, int]]:
    frames: dict[str, pd.DataFrame] = {}
    usable_dates: dict[str, int] = {}
    for candidate_id in QA_CANDIDATES:
        targets = engine_v1._sort_if_present(
            pd.concat(grouped[candidate_id]["targets"], ignore_index=True),
            ("city_id", "target_date", "tract_geoid"),
        )
        summaries = engine_v1._sort_if_present(
            pd.concat(grouped[candidate_id]["summary"], ignore_index=True),
            ("city_id", "target_date", "overpass_id"),
        )
        contributions = engine_v1._sort_if_present(
            pd.concat(grouped[candidate_id]["contributions"], ignore_index=True),
            ("city_id", "target_date", "overpass_id", "tract_geoid", "scene_id"),
        )
        if targets.duplicated(["city_id", "tract_geoid", "target_date"]).any():
            raise M3SourceDevelopmentV2Error("Compiled city target keys are duplicated.")
        if summaries.duplicated(["city_id", "target_date"]).any():
            raise M3SourceDevelopmentV2Error("Compiled city summaries are duplicated.")
        usable_dates[candidate_id] = int(
            summaries["date_usable"].fillna(False).astype(bool).sum()
        )
        frames[f"{candidate_id}/targets.parquet"] = targets
        frames[f"{candidate_id}/date_summary.parquet"] = summaries
        frames[f"{candidate_id}/scene_contributions.parquet"] = contributions
    return frames, usable_dates


def _authenticate_city_output(
    engine: M3SourceDevelopmentEngineV2,
    city_id: str,
    rows: Sequence[Mapping[str, Any]],
    overpass_commits: Sequence[str],
    expected_frames: Mapping[str, pd.DataFrame],
    expected_usable_dates: Mapping[str, int],
) -> dict[str, Any]:
    engine.before_value_access()
    destination = engine.settings.qa_output_root / "cities" / city_id
    commit, frames = _read_v2_output(
        destination,
        engine_v1.CITY_COMMIT,
        expected_state="city_qa_candidates_complete",
        expected_paths=_expected_output_paths(city_level=True),
    )
    expected_lock = {
        **engine._base_lock(city_id),
        "overpass_commits_sha256": canonical_sha256(list(overpass_commits)),
        "overpass_count": len(rows),
    }
    if (
        commit.get("schema_version") != 2
        or commit.get("algorithm_version") != ALGORITHM_VERSION
        or commit.get("cache_lock") != expected_lock
        or tuple(commit.get("candidate_ids", ())) != QA_CANDIDATES
        or commit.get("usable_date_counts") != dict(expected_usable_dates)
        or commit.get("none_support_gate_passed")
        is not (
            int(expected_usable_dates["none"])
            >= engine_v1.MINIMUM_USABLE_DATES_PER_CITY
        )
        or commit.get("network_requests_performed") != 0
        or commit.get("href_reads_performed") != 0
        or commit.get("physical_cache_mutated") is not False
        or commit.get("model_fit_or_selection_performed") is not False
        or commit.get("blind_test_city_accessed") is not False
    ):
        raise M3SourceDevelopmentV2Error("City QA commit audit changed.")
    dates = {engine_v1._target_date(row) for row in rows}
    overpasses = {str(row["overpass_id"]) for row in rows}
    pairs = {
        (engine_v1._target_date(row), str(row["overpass_id"])) for row in rows
    }
    scenes = {str(scene) for row in rows for scene in row["scene_ids"]}
    config_hashes = engine._overpass_lock(rows[0])["candidate_config_sha256s"]
    for relative, frame in frames.items():
        candidate_id = relative.split("/", 1)[0]
        _validate_candidate_frame(
            frame,
            relative=relative,
            city_id=city_id,
            candidate_id=candidate_id,
            expected_dates=dates,
            expected_overpasses=overpasses,
            expected_scenes=scenes,
            expected_config_sha256=str(config_hashes[candidate_id]),
            city_level=True,
        )
        if _semantic_sha256(frame) != _semantic_sha256(expected_frames[relative]):
            raise M3SourceDevelopmentV2Error("City QA output differs from overpass chain.")
        if relative.endswith("date_summary.parquet"):
            observed_pairs = set(
                zip(
                    frame["target_date"].astype(str),
                    frame["overpass_id"].astype(str),
                    strict=True,
                )
            )
            if observed_pairs != pairs:
                raise M3SourceDevelopmentV2Error("City QA summary cohort changed.")
    return commit


class M3SourceDevelopmentEngineV2(M3SourceDevelopmentEngine):
    """Reuse v1 QA assembly while resolving scenes through the logical overlay."""

    __slots__ = ("v2_phase", "logical_global_cache_commit")

    @classmethod
    def create(
        cls,
        project_root: str | Path,
        *,
        phase: str,
        config_path: str | Path = "configs/research.toml",
        context_loader: Any = load_target_city_context,
        before_value_access: Any | None = None,
        reconstructor: Any = reconstruct_overpass_candidates,
    ) -> M3SourceDevelopmentEngineV2:
        if phase not in PHASES:
            raise M3SourceDevelopmentV2Error("Unknown v2 execution phase.")
        settings = load_runner_settings_v2(project_root)
        authorization = authenticate_m3_source_integrity_v2_authorization(
            settings.root, settings.authorization
        )
        protocol = authenticate_m3_development_protocol_lock(
            settings.root, settings.protocol_lock
        )
        amendment = authenticate_m3_source_acquisition_amendment(
            settings.root, settings.amendment
        )
        try:
            raw_plan = json.loads(
                (settings.cache_root / "SCENE_PLAN.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as error:
            raise M3SourceDevelopmentV2Error("Physical scene plan is unreadable.") from error
        physical_plan = authenticate_plan(raw_plan)
        if (
            physical_plan["commit_sha256"]
            != authorization["physical_scene_plan_commit_sha256"]
        ):
            raise M3SourceDevelopmentV2Error("Physical scene plan binding changed.")
        raw_config = Path(config_path)
        resolved_config = (
            raw_config.resolve()
            if raw_config.is_absolute()
            else (settings.root / raw_config).resolve()
        )
        if not resolved_config.is_relative_to(settings.root):
            raise M3SourceDevelopmentV2Error("Research config escaped the project.")
        contexts = {
            city_id: context_loader(settings.root, city_id) for city_id in SOURCE_CITY_IDS
        }

        def default_gate() -> None:
            authenticate_m3_source_integrity_v2_value_gate(
                settings.root,
                authorization,
                settings.authorization,
            )

        engine = cls(
            settings=settings,
            phase=OFFLINE_PHASE,
            protocol=protocol,
            amendment=amendment,
            inventory=dict(authorization["logical_overlay"]),
            authorization=authorization,
            plan=physical_plan,
            config=load_config(resolved_config),
            contexts=contexts,
            hydrator=None,
            signer=lambda value: value,
            before_value_access=(
                default_gate if before_value_access is None else before_value_access
            ),
            reconstructor=reconstructor,
        )
        engine.v2_phase = phase
        engine.logical_global_cache_commit = None
        if phase == QA_PHASE:
            engine.logical_global_cache_commit = authenticate_logical_global_cache(
                settings.root, authorization
            )
        return engine

    @property
    def settings_v2(self) -> RunnerSettingsV2:
        return self.settings  # type: ignore[return-value]

    def _ensure_offline_cache(self) -> dict[str, Any]:
        if self.v2_phase != QA_PHASE:
            raise M3SourceDevelopmentV2Error(
                "QA reads are sealed until logical cache finalization completes."
            )
        if self.logical_global_cache_commit is None:
            self.logical_global_cache_commit = authenticate_logical_global_cache(
                self.settings.root, self.authorization
            )
        return self.logical_global_cache_commit

    def _base_lock(self, city_id: str) -> dict[str, Any]:
        global_commit = self._ensure_offline_cache()
        return {
            "protocol_commit_sha256": self.protocol["commit_sha256"],
            "amendment_commit_sha256": self.amendment["commit_sha256"],
            "base_expanded_inventory_commit_sha256": self.authorization[
                "expanded_source_inventory_commit_sha256"
            ],
            "source_integrity_availability_amendment_commit_sha256": (
                self.authorization[
                    "source_integrity_availability_amendment_commit_sha256"
                ]
            ),
            "source_integrity_logical_overlay_commit_sha256": self.authorization[
                "source_integrity_logical_overlay_commit_sha256"
            ],
            "integrity_execution_authorization_commit_sha256": self.authorization[
                "commit_sha256"
            ],
            "physical_scene_plan_commit_sha256": self.plan["commit_sha256"],
            "execution_overlay_commit_sha256": self.inventory["commit_sha256"],
            "logical_global_cache_commit_sha256": global_commit["commit_sha256"],
            "city_id": city_id,
            "target_context_locks_sha256": canonical_sha256(
                self.contexts[city_id].locks
            ),
            "physical_cache_read_only": True,
            "network_or_href_reads": 0,
        }

    def execute_finalize_retained_scene(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        if self.v2_phase != LOGICAL_PHASE:
            raise M3SourceDevelopmentV2Error("Logical scene task is phase-forbidden.")
        city_id = engine_v1._identifier(payload.get("city_id"), label="city_id")
        scene_id = engine_v1._identifier(payload.get("scene_id"), label="scene_id")
        commit = finalize_retained_scene(
            self.settings.root,
            self.authorization,
            self.plan,
            city_id,
            scene_id,
            before_value_access=self.before_value_access,
        )
        return {
            "state": "logical_retained_scene_complete",
            "city_id": city_id,
            "scene_id": scene_id,
            "commit_sha256": commit["commit_sha256"],
            "physical_cache_mutated": False,
            "network_or_href_reads": 0,
        }

    def execute_finalize_logical_cache(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        if self.v2_phase != LOGICAL_PHASE:
            raise M3SourceDevelopmentV2Error("Logical global task is phase-forbidden.")
        if (
            payload.get("expected_scene_count") != EXPECTED_SCENE_COUNT
            or payload.get("expected_content_count") != EXPECTED_CONTENT_COUNT
        ):
            raise M3SourceDevelopmentV2Error("Logical cache task counts changed.")
        commit = finalize_logical_global_cache(self.settings.root, self.authorization)
        return {
            "state": "logical_source_cache_complete",
            "scene_count": commit["scene_count"],
            "content_count": commit["content_count"],
            "commit_sha256": commit["commit_sha256"],
            "physical_cache_mutated": False,
            "network_or_href_reads": 0,
        }

    def execute_qa_overpass(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._ensure_offline_cache()
        city_id = engine_v1._identifier(payload.get("city_id"), label="city_id")
        overpass_id = engine_v1._identifier(
            payload.get("overpass_id"), label="overpass_id"
        )
        if tuple(payload.get("qa_candidate_ids", ())) != QA_CANDIDATES:
            raise M3SourceDevelopmentV2Error("QA candidate order or membership changed.")
        row = self._overpass(city_id, overpass_id)
        for key, expected in (
            ("scene_ids", row["scene_ids"]),
            ("target_date", engine_v1._target_date(row)),
            ("platform", engine_v1._platform(row)),
        ):
            if key in payload and payload[key] != expected:
                raise M3SourceDevelopmentV2Error(f"Overpass task changed {key}.")
        context = self.contexts[city_id]
        expected_lock = self._overpass_lock(row)
        directory = self.settings.qa_output_root / "by_overpass" / city_id / overpass_id
        if (directory / engine_v1.OVERPASS_COMMIT).is_file():
            observed, _frames = _authenticate_overpass_output(self, row)
            return {
                "state": "qa_overpass_complete",
                "cache": "hit",
                "commit_sha256": observed["commit_sha256"],
            }

        def local_loader(requested_city: str, scene_id: str) -> Mapping[str, Any]:
            if requested_city != city_id:
                raise M3SourceDevelopmentV2Error("Local loader crossed a source city.")
            return load_retained_scene_arrays(
                self.settings.root,
                self.authorization,
                self.plan,
                requested_city,
                scene_id,
                before_value_access=self.before_value_access,
            )

        results = self.reconstructor(
            city_id=city_id,
            scene_ids=tuple(str(value) for value in row["scene_ids"]),
            loader=local_loader,
            context=context,
            base_config=self.config,
            target_date=engine_v1._target_date(row),
            overpass_id=overpass_id,
            platform=engine_v1._platform(row),
            union_city_coverage_fraction=engine_v1._coverage(row),
            tract_manifest_sha256=str(
                row.get("tract_manifest_sha256", canonical_sha256(context.locks))
            ),
        )
        if tuple(results) != QA_CANDIDATES:
            raise M3SourceDevelopmentV2Error("Offline reconstructor changed candidates.")
        frames: dict[str, pd.DataFrame] = {}
        for candidate_id, result in results.items():
            candidate_frames = {
                "tract_date_qa.parquet": result.tract_date_qa.copy(),
                "date_summary.parquet": pd.DataFrame([result.summary]),
                "scene_contributions.parquet": result.scene_contributions.copy(),
            }
            for filename, frame in candidate_frames.items():
                frame.insert(0, "candidate_id", candidate_id)
                frame.insert(0, "city_id", city_id)
                frames[f"{candidate_id}/{filename}"] = frame
        directory.mkdir(parents=True, exist_ok=True)
        output_files = _write_v2_frames(directory, frames)
        commit = engine_v1._with_commit(
            {
                "schema_version": 2,
                "algorithm_version": ALGORITHM_VERSION,
                "state": "qa_overpass_complete",
                "cache_lock": expected_lock,
                "candidate_ids": list(QA_CANDIDATES),
                "raw_scene_arrays_loaded_once_per_overpass": True,
                "network_requests_performed": 0,
                "href_reads_performed": 0,
                "physical_cache_mutated": False,
                "output_files": output_files,
                "model_fit_or_selection_performed": False,
                "blind_test_city_accessed": False,
            }
        )
        atomic_json(commit, directory / engine_v1.OVERPASS_COMMIT)
        return {
            "state": "qa_overpass_complete",
            "cache": "built",
            "commit_sha256": commit["commit_sha256"],
        }

    def _city_chain(
        self, city_id: str
    ) -> tuple[
        tuple[dict[str, Any], ...],
        list[str],
        dict[str, pd.DataFrame],
        dict[str, int],
    ]:
        rows = tuple(
            row
            for row in engine_v1._overpasses(self.inventory)
            if row["city_id"] == city_id
        )
        if not rows:
            raise M3SourceDevelopmentV2Error("City compile has no source overpasses.")
        grouped: dict[str, dict[str, list[pd.DataFrame]]] = {
            candidate_id: {"targets": [], "summary": [], "contributions": []}
            for candidate_id in QA_CANDIDATES
        }
        commits: list[str] = []
        for row in rows:
            commit, frames = _authenticate_overpass_output(self, row)
            commits.append(str(commit["commit_sha256"]))
            for candidate_id in QA_CANDIDATES:
                grouped[candidate_id]["targets"].append(
                    frames[f"{candidate_id}/tract_date_qa.parquet"]
                )
                grouped[candidate_id]["summary"].append(
                    frames[f"{candidate_id}/date_summary.parquet"]
                )
                grouped[candidate_id]["contributions"].append(
                    frames[f"{candidate_id}/scene_contributions.parquet"]
                )
        frames, usable_dates = _compiled_city_frames(grouped)
        return rows, commits, frames, usable_dates

    def execute_compile_city(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._ensure_offline_cache()
        city_id = engine_v1._identifier(payload.get("city_id"), label="city_id")
        if (
            city_id not in SOURCE_CITY_IDS
            or tuple(payload.get("qa_candidate_ids", ())) != QA_CANDIDATES
        ):
            raise M3SourceDevelopmentV2Error("City compile task changed its cohort.")
        rows, overpass_commits, frames, usable_dates = self._city_chain(city_id)
        destination = self.settings.qa_output_root / "cities" / city_id
        if (destination / engine_v1.CITY_COMMIT).is_file():
            observed = _authenticate_city_output(
                self,
                city_id,
                rows,
                overpass_commits,
                frames,
                usable_dates,
            )
            return {
                "state": "city_qa_candidates_complete",
                "cache": "hit",
                "commit_sha256": observed["commit_sha256"],
            }
        destination.mkdir(parents=True, exist_ok=True)
        output_files = _write_v2_frames(destination, frames)
        city_lock = {
            **self._base_lock(city_id),
            "overpass_commits_sha256": canonical_sha256(overpass_commits),
            "overpass_count": len(rows),
        }
        commit = engine_v1._with_commit(
            {
                "schema_version": 2,
                "algorithm_version": ALGORITHM_VERSION,
                "state": "city_qa_candidates_complete",
                "cache_lock": city_lock,
                "candidate_ids": list(QA_CANDIDATES),
                "usable_date_counts": usable_dates,
                "none_support_gate_passed": (
                    usable_dates["none"]
                    >= engine_v1.MINIMUM_USABLE_DATES_PER_CITY
                ),
                "network_requests_performed": 0,
                "href_reads_performed": 0,
                "physical_cache_mutated": False,
                "output_files": output_files,
                "model_fit_or_selection_performed": False,
                "blind_test_city_accessed": False,
            }
        )
        atomic_json(commit, destination / engine_v1.CITY_COMMIT)
        observed = _authenticate_city_output(
            self,
            city_id,
            rows,
            overpass_commits,
            frames,
            usable_dates,
        )
        return {
            "state": "city_qa_candidates_complete",
            "cache": "built",
            "commit_sha256": observed["commit_sha256"],
        }

    def _build_qa_completion(self) -> dict[str, Any]:
        self._ensure_offline_cache()
        city_commits: list[dict[str, str]] = []
        counts: dict[str, dict[str, int]] = {}
        for city_id in SOURCE_CITY_IDS:
            rows, overpass_commits, frames, usable_dates = self._city_chain(city_id)
            commit = _authenticate_city_output(
                self,
                city_id,
                rows,
                overpass_commits,
                frames,
                usable_dates,
            )
            city_commits.append(
                {"city_id": city_id, "commit_sha256": commit["commit_sha256"]}
            )
            counts[city_id] = usable_dates
        none_counts = {city_id: values["none"] for city_id, values in counts.items()}
        per_city_pass = all(
            value >= engine_v1.MINIMUM_USABLE_DATES_PER_CITY
            for value in none_counts.values()
        )
        total_pass = (
            sum(none_counts.values()) >= engine_v1.MINIMUM_TOTAL_USABLE_CITY_DATES
        )
        support_pass = per_city_pass and total_pass
        completion = engine_v1._with_commit(
            {
                "schema_version": 2,
                "algorithm_version": ALGORITHM_VERSION,
                "state": "source_qa_candidates_complete",
                "claim_id": self.authorization["claim_id"],
                "m3_protocol_lock_commit_sha256": self.protocol["commit_sha256"],
                "source_acquisition_amendment_commit_sha256": self.amendment[
                    "commit_sha256"
                ],
                "expanded_source_inventory_commit_sha256": self.authorization[
                    "expanded_source_inventory_commit_sha256"
                ],
                "source_integrity_availability_amendment_commit_sha256": (
                    self.authorization[
                        "source_integrity_availability_amendment_commit_sha256"
                    ]
                ),
                "source_integrity_logical_overlay_commit_sha256": self.authorization[
                    "source_integrity_logical_overlay_commit_sha256"
                ],
                "integrity_execution_authorization_commit_sha256": (
                    self.authorization["commit_sha256"]
                ),
                "physical_scene_plan_commit_sha256": self.plan["commit_sha256"],
                "execution_overlay_commit_sha256": self.inventory["commit_sha256"],
                "logical_global_cache_commit_sha256": self._ensure_offline_cache()[
                    "commit_sha256"
                ],
                "source_city_ids": list(SOURCE_CITY_IDS),
                "candidate_ids": list(QA_CANDIDATES),
                "overpass_count": EXPECTED_OVERPASS_COUNT,
                "city_commits": city_commits,
                "usable_date_counts": counts,
                "support_gate": {
                    "minimum_usable_dates_per_city": (
                        engine_v1.MINIMUM_USABLE_DATES_PER_CITY
                    ),
                    "minimum_total_usable_city_dates": (
                        engine_v1.MINIMUM_TOTAL_USABLE_CITY_DATES
                    ),
                    "none_candidate_counts": none_counts,
                    "per_city_passed": per_city_pass,
                    "total_passed": total_pass,
                    "passed": support_pass,
                },
                "decision": (
                    "eligible_for_separate_nested_loso_authorization"
                    if support_pass
                    else "stop_source_development_support_gate_failed"
                ),
                "offline_audit": {
                    "network_requests_performed": 0,
                    "href_reads_performed": 0,
                    "physical_cache_mutated": False,
                    "old_queue_or_cache_mutated": False,
                    "blind_test_city_accessed": False,
                    "predictor_values_read_or_built": False,
                    "model_fit_selection_prediction_or_scoring_performed": False,
                },
                "nested_loso_performed": False,
                "model_fit_performed": False,
                "model_or_st_qa_selected": False,
                "next_safe_stage": (
                    "separately_authorize_nested_source_loso_fit_and_selection"
                    if support_pass
                    else "review_source_support_without_model_fit_or_selection"
                ),
            }
        )
        return completion

    def execute_finalize_qa_candidates(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        if (
            tuple(payload.get("source_city_ids", ())) != SOURCE_CITY_IDS
            or tuple(payload.get("qa_candidate_ids", ())) != QA_CANDIDATES
            or payload.get("expected_overpass_count") != EXPECTED_OVERPASS_COUNT
        ):
            raise M3SourceDevelopmentV2Error("Final QA task changed its cohort.")
        completion = self._build_qa_completion()
        path = _inside(
            self.settings.root,
            str(self.authorization["source_qa_candidates_completion"]),
            label="QA completion",
        )
        if path.name != engine_v1.FINAL_COMMIT:
            raise M3SourceDevelopmentV2Error("QA completion filename changed.")
        _write_once(completion, path)
        return completion

    def execute(self, kind: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if self.v2_phase == LOGICAL_PHASE:
            allowed = {
                "finalize_retained_scene": self.execute_finalize_retained_scene,
                "finalize_logical_cache": self.execute_finalize_logical_cache,
            }
        else:
            allowed = {
                "qa_overpass": self.execute_qa_overpass,
                "compile_qa_city": self.execute_compile_city,
                "finalize_qa_candidates": self.execute_finalize_qa_candidates,
            }
        if kind not in allowed:
            raise M3SourceDevelopmentV2Error(
                f"Task {kind!r} is forbidden during {self.v2_phase!r}."
            )
        return allowed[kind](payload)


def authenticate_source_qa_candidates_completion_v2(
    project_root: str | Path,
    completion_path: str | Path | None = None,
    *,
    authorization_path: str | Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    """Rebuild the complete 317-overpass/4-city QA commit chain read-only."""

    root = Path(project_root).resolve()
    settings = load_runner_settings_v2(root)
    requested_authorization = _inside(
        root, authorization_path, label="Integrity v2 authorization"
    )
    if requested_authorization != settings.authorization:
        raise M3SourceDevelopmentV2Error("Completion uses an unexpected authorization.")
    engine = M3SourceDevelopmentEngineV2.create(root, phase=QA_PHASE)
    authorization = engine.authorization
    expected_path = _inside(
        root,
        str(authorization["source_qa_candidates_completion"]),
        label="QA completion",
    )
    requested_completion = (
        expected_path
        if completion_path is None
        else _inside(root, completion_path, label="QA completion")
    )
    if requested_completion != expected_path or requested_completion.name != engine_v1.FINAL_COMMIT:
        raise M3SourceDevelopmentV2Error("QA completion path changed.")
    observed = _read_committed_json(
        requested_completion,
        label="M3 source QA candidates completion v2",
    )
    if observed.get("state") != "source_qa_candidates_complete":
        raise M3SourceDevelopmentV2Error("QA completion state changed.")
    expected = engine._build_qa_completion()
    if observed != expected:
        raise M3SourceDevelopmentV2Error(
            "QA completion differs from its overpass/city/support audit."
        )
    return observed
