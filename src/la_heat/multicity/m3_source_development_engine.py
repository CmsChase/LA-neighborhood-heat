"""Two-phase executor for the authorized M3 source-city QA rebuild.

The online phase only hydrates the exact five frozen Landsat assets into the
portable raw-value cache.  The offline phase first authenticates that complete
cache and then rebuilds all four ST_QA candidates in one pass per overpass.
This module deliberately contains no model fitting or configuration selection.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Final

import pandas as pd
import planetary_computer as pc

from la_heat.aligned_landsat import REQUIRED_ASSETS
from la_heat.config import ResearchConfig, load_config
from la_heat.multicity.m3_development_protocol_lock import (
    authenticate_m3_development_protocol_lock,
)
from la_heat.multicity.m3_source_acquisition_amendment import (
    authenticate_m3_source_acquisition_amendment,
)
from la_heat.multicity.m3_source_asset_cache import (
    authenticate_global_cache,
    build_scene_plan,
    cache_asset_from_href,
    finalize_global_cache,
    finalize_scene_cache,
    load_local_scene_arrays,
    load_scene_plan,
    write_scene_plan,
)
from la_heat.multicity.m3_source_development_runtime import (
    BLIND_CITY_IDS,
    QA_CANDIDATES,
    SOURCE_CITY_IDS,
    RunnerSettings,
    authenticate_expanded_inventory,
    load_runner_settings,
)
from la_heat.multicity.m3_source_development_worker import OFFLINE_PHASE, ONLINE_PHASE
from la_heat.multicity.m3_source_offline_qa import (
    candidate_target_config_sha256,
    reconstruct_overpass_candidates,
)
from la_heat.multicity.m3_source_qa_authorization import (
    authenticate_m3_source_qa_authorization,
)
from la_heat.multicity.target_context import TargetCityContext, load_target_city_context
from la_heat.multicity.target_processor import (
    PlanetaryComputerSceneHydrator,
    SceneHydrator,
)
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_sha256,
    parquet_file_record,
    sha256_file,
)
from la_heat.target_aggregation import TargetAggregationResult

ALGORITHM_VERSION: Final = "m3-source-development-engine-v1"
OVERPASS_COMMIT: Final = "QA_CANDIDATES_COMPLETE.json"
CITY_COMMIT: Final = "CITY_QA_CANDIDATES_COMPLETE.json"
FINAL_COMMIT: Final = "SOURCE_QA_CANDIDATES_COMPLETE.json"
MINIMUM_USABLE_DATES_PER_CITY: Final = 8
MINIMUM_TOTAL_USABLE_CITY_DATES: Final = 30
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_MARKER_LOCK = threading.Lock()


class M3SourceDevelopmentError(RuntimeError):
    """Raised when source development leaves its authorized two-phase contract."""


ValueAccessGate = Callable[[], None]
ContextLoader = Callable[[str | Path, str], TargetCityContext]
Signer = Callable[[str], str]
Reconstructor = Callable[..., dict[str, TargetAggregationResult]]


def _with_commit(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(result)
    return result


def _is_committed(payload: Mapping[str, Any]) -> bool:
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    return isinstance(recorded, str) and recorded == canonical_sha256(unsigned)


def _load_committed_json(path: Path, *, state: str, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise M3SourceDevelopmentError(f"Cannot read {label}: {path}") from error
    if (
        not isinstance(payload, dict)
        or payload.get("state") != state
        or not _is_committed(payload)
    ):
        raise M3SourceDevelopmentError(f"{label} commit or state is invalid.")
    return payload


def _require_sha(value: object, *, label: str) -> str:
    digest = str(value)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise M3SourceDevelopmentError(f"{label} is not a lowercase SHA-256.")
    return digest


def _identifier(value: object, *, label: str) -> str:
    text = str(value)
    if not _IDENTIFIER.fullmatch(text):
        raise M3SourceDevelopmentError(f"Unsafe or missing {label}: {text!r}")
    return text


def _overpasses(inventory: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    raw = inventory.get("overpasses")
    if not isinstance(raw, list) or not raw:
        raise M3SourceDevelopmentError("Expanded source inventory has no overpasses.")
    rows: list[dict[str, Any]] = []
    keys: set[tuple[str, str]] = set()
    for value in raw:
        if not isinstance(value, Mapping):
            raise M3SourceDevelopmentError("Expanded overpass must be a mapping.")
        row = dict(value)
        city_id = _identifier(row.get("city_id"), label="city_id")
        overpass_id = _identifier(row.get("overpass_id"), label="overpass_id")
        if city_id not in SOURCE_CITY_IDS or city_id in BLIND_CITY_IDS:
            raise M3SourceDevelopmentError("Inventory contains a non-source city.")
        scene_ids = row.get("scene_ids")
        if (
            not isinstance(scene_ids, list)
            or not scene_ids
            or len(scene_ids) != len(set(scene_ids))
            or any(
                not isinstance(item, str) or not _IDENTIFIER.fullmatch(item)
                for item in scene_ids
            )
        ):
            raise M3SourceDevelopmentError("Overpass scene IDs are empty, unsafe, or duplicated.")
        key = (city_id, overpass_id)
        if key in keys:
            raise M3SourceDevelopmentError("Expanded inventory duplicated an overpass.")
        keys.add(key)
        _target_date(row)
        _platform(row)
        _coverage(row)
        rows.append(row)
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                str(row["city_id"]),
                _target_date(row),
                str(row["overpass_id"]),
            ),
        )
    )


def _target_date(row: Mapping[str, Any]) -> str:
    value = row.get("target_date", row.get("local_date"))
    text = str(value)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise M3SourceDevelopmentError("Overpass target date must use YYYY-MM-DD.")
    return text


def _platform(row: Mapping[str, Any]) -> str:
    return _identifier(
        row.get("platform", row.get("landsat_platform")),
        label="platform",
    )


def _coverage(row: Mapping[str, Any]) -> float:
    value = row.get(
        "union_city_coverage_fraction",
        row.get("union_aoi_coverage_fraction"),
    )
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise M3SourceDevelopmentError("Overpass union coverage is missing.") from error
    if not 0.0 <= result <= 1.0:
        raise M3SourceDevelopmentError("Overpass union coverage must be in [0, 1].")
    return result


def _scene_optional_metadata(row: Mapping[str, Any], scene_id: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    records = row.get("scene_metadata", row.get("scenes"))
    if isinstance(records, list):
        matches = [
            value
            for value in records
            if isinstance(value, Mapping) and value.get("scene_id") == scene_id
        ]
        if len(matches) > 1:
            raise M3SourceDevelopmentError("Inventory duplicated scene metadata.")
        if matches:
            for key in ("acquired_utc", "wrs_path", "wrs_row", "scene_order"):
                if key in matches[0]:
                    result[key] = matches[0][key]
    return result


def build_cache_plan_from_inventory(
    inventory: Mapping[str, Any],
    *,
    contexts: Mapping[str, TargetCityContext],
    bindings: Mapping[str, str],
) -> dict[str, Any]:
    """Convert authenticated overpass metadata into one exact local cache plan."""

    rows = _overpasses(inventory)
    cities = {str(row["city_id"]) for row in rows}
    if cities != set(SOURCE_CITY_IDS) or set(contexts) != set(SOURCE_CITY_IDS):
        raise M3SourceDevelopmentError("Cache plan requires all four source-city contexts.")
    scene_records: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        city_id = str(row["city_id"])
        context = contexts[city_id]
        recorded_grid = row.get("grid_sha256")
        if recorded_grid is not None and recorded_grid != context.grid.sha256:
            raise M3SourceDevelopmentError("Inventory grid binding changed.")
        recorded_context = row.get("target_context_commit_sha256")
        context_commit = canonical_sha256(context.locks)
        if recorded_context is not None and recorded_context != context_commit:
            raise M3SourceDevelopmentError("Inventory target-context binding changed.")
        for scene_id in row["scene_ids"]:
            record = {
                "city_id": city_id,
                "scene_id": str(scene_id),
                "overpass_id": str(row["overpass_id"]),
                "target_date": _target_date(row),
                "platform": _platform(row),
                **_scene_optional_metadata(row, str(scene_id)),
            }
            prior = seen.get(str(scene_id))
            if prior is not None and prior != record:
                raise M3SourceDevelopmentError(
                    "One scene is assigned to multiple physical overpasses or cities."
                )
            if prior is None:
                seen[str(scene_id)] = record
                scene_records.append(record)
    return build_scene_plan(
        scene_records,
        grids={city_id: contexts[city_id].grid for city_id in SOURCE_CITY_IDS},
        bindings=bindings,
    )


def _relative_output(directory: Path, value: object) -> Path:
    pure = PurePosixPath(str(value))
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise M3SourceDevelopmentError("Output commit contains a non-local path.")
    resolved = (directory / Path(*pure.parts)).resolve()
    if not resolved.is_relative_to(directory.resolve()):
        raise M3SourceDevelopmentError("Output commit escapes its directory.")
    return resolved


def _read_output_commit(directory: Path, filename: str) -> dict[str, Any]:
    path = directory / filename
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise M3SourceDevelopmentError(f"Cannot read completed output: {path}") from error
    if not isinstance(payload, dict) or not _is_committed(payload):
        raise M3SourceDevelopmentError(f"Output commit is invalid: {path}")
    outputs = payload.get("output_files")
    if not isinstance(outputs, dict):
        raise M3SourceDevelopmentError("Output commit has no file records.")
    for relative, record in outputs.items():
        output = _relative_output(directory, relative)
        if (
            not isinstance(record, Mapping)
            or not output.is_file()
            or output.stat().st_size != record.get("bytes")
            or sha256_file(output) != record.get("sha256")
        ):
            raise M3SourceDevelopmentError(f"Committed output changed: {output}")
    return payload


def _write_frames(
    directory: Path,
    frames: Mapping[str, pd.DataFrame],
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for relative, frame in frames.items():
        path = _relative_output(directory, relative)
        atomic_parquet(frame, path)
        records[relative] = parquet_file_record(path, frame)
    return records


def _sort_if_present(frame: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    selected = [column for column in columns if column in frame.columns]
    if not selected or frame.empty:
        return frame.reset_index(drop=True)
    return frame.sort_values(selected, kind="stable").reset_index(drop=True)


def _reject_remote_strings(value: object) -> None:
    if isinstance(value, str) and value.lower().startswith(("http://", "https://")):
        raise M3SourceDevelopmentError("Offline state contains a remote reference.")
    if isinstance(value, Mapping):
        for nested in value.values():
            _reject_remote_strings(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_remote_strings(nested)


def _validate_authorized_components(
    protocol: Mapping[str, Any],
    amendment: Mapping[str, Any],
    inventory: Mapping[str, Any],
    authorization: Mapping[str, Any],
) -> dict[str, str]:
    protocol_commit = _require_sha(protocol.get("commit_sha256"), label="Protocol commit")
    amendment_commit = _require_sha(amendment.get("commit_sha256"), label="Amendment commit")
    inventory_commit = _require_sha(inventory.get("commit_sha256"), label="Inventory commit")
    authorization_commit = _require_sha(
        authorization.get("commit_sha256"), label="Authorization commit"
    )
    if authorization.get("state") != "source_qa_two_phase_execution_authorized":
        raise M3SourceDevelopmentError("Source QA execution is not authorized.")
    expected = {
        "m3_protocol_lock_commit_sha256": protocol_commit,
        "source_acquisition_amendment_commit_sha256": amendment_commit,
        "expanded_source_inventory_commit_sha256": inventory_commit,
    }
    if any(authorization.get(key) != value for key, value in expected.items()):
        raise M3SourceDevelopmentError("Source QA authorization bindings changed.")
    if authorization.get("blind_test_target_access_authorized") is not False:
        raise M3SourceDevelopmentError("Authorization opens a blind-test target.")
    online = authorization.get("online_predownload_permissions")
    offline = authorization.get("offline_qa_permissions")
    runtime = authorization.get("runtime_contract")
    if (
        tuple(authorization.get("source_city_ids", ())) != SOURCE_CITY_IDS
        or tuple(authorization.get("blind_test_city_ids", ())) != BLIND_CITY_IDS
        or tuple(authorization.get("required_landsat_assets", ())) != REQUIRED_ASSETS
        or tuple(authorization.get("qa_candidate_ids", ())) != QA_CANDIDATES
        or not isinstance(online, Mapping)
        or not isinstance(offline, Mapping)
        or not isinstance(runtime, Mapping)
        or tuple(runtime.get("download_workers_allowed", ())) != (1, 2)
        or runtime.get("compute_workers") != 1
        or runtime.get("raster_window_size") != 512
        or runtime.get("raster_window_size_is_hard_streaming_limit") is not False
        or runtime.get("offline_execution_granularity")
        != "one_complete_physical_overpass"
        or runtime.get("signed_urls_credentials_or_cookies_may_be_persisted")
        is not False
        or runtime.get("retry_and_resume_from_content_commits") is not True
        or online.get("hydrate_frozen_source_scene_asset_hrefs") is not True
        or online.get("read_exact_five_source_landsat_assets") is not True
        or online.get("write_verified_local_aligned_cache") is not True
        or online.get("aggregate_targets_or_apply_qa_candidates") is not False
        or online.get("read_blind_test_city_assets_or_values") is not False
        or offline.get("requires_authenticated_global_cache") is not True
        or offline.get("network_or_href_hydration_allowed") is not False
        or offline.get("read_verified_local_source_cache") is not True
        or offline.get("rebuild_none_3k_4k_6k_candidates") is not True
        or offline.get("fit_select_predict_or_score") is not False
        or authorization.get("model_fit_or_selection_authorized") is not False
        or authorization.get("predictor_build_or_read_authorized") is not False
    ):
        raise M3SourceDevelopmentError("Source QA authorization permissions changed.")
    rows = _overpasses(inventory)
    unique_city_scenes = {
        (str(row["city_id"]), str(scene_id))
        for row in rows
        for scene_id in row["scene_ids"]
    }
    if (
        authorization.get("expected_overpass_count") != len(rows)
        or authorization.get("expected_unique_city_scene_count")
        != len(unique_city_scenes)
    ):
        raise M3SourceDevelopmentError("Authorized inventory counts changed.")
    _require_sha(authorization.get("claim_id"), label="Authorization claim ID")
    return {
        "m3_protocol_lock": protocol_commit,
        "source_acquisition_amendment": amendment_commit,
        "expanded_source_inventory": inventory_commit,
        "source_qa_execution_authorization": authorization_commit,
    }


@dataclass(slots=True)
class M3SourceDevelopmentEngine:
    """TaskExecutor used by the resumable source-development worker."""

    settings: RunnerSettings
    phase: str
    protocol: dict[str, Any]
    amendment: dict[str, Any]
    inventory: dict[str, Any]
    authorization: dict[str, Any]
    plan: dict[str, Any]
    config: ResearchConfig
    contexts: dict[str, TargetCityContext]
    hydrator: SceneHydrator | None
    signer: Signer = pc.sign
    before_value_access: ValueAccessGate = lambda: None
    reconstructor: Reconstructor = reconstruct_overpass_candidates
    global_cache_commit: dict[str, Any] | None = None
    _hrefs_by_scene: dict[str, dict[str, str]] = field(default_factory=dict)
    _href_lock: threading.Lock = field(default_factory=threading.Lock)
    _access_marker_ready: bool = False

    @classmethod
    def create(
        cls,
        project_root: str | Path,
        *,
        phase: str,
        config_path: str | Path = "configs/research.toml",
        hydrator: SceneHydrator | None = None,
        signer: Signer = pc.sign,
        context_loader: ContextLoader = load_target_city_context,
        before_value_access: ValueAccessGate | None = None,
    ) -> M3SourceDevelopmentEngine:
        """Authenticate all existing gates; never create an authorization."""

        settings = load_runner_settings(project_root)
        protocol = authenticate_m3_development_protocol_lock(settings.root)
        amendment = authenticate_m3_source_acquisition_amendment(
            settings.root, settings.amendment
        )
        inventory = authenticate_expanded_inventory(settings, amendment)
        authorization = authenticate_m3_source_qa_authorization(
            settings.root,
            settings.authorization,
        )
        raw_config = Path(config_path)
        resolved_config = (
            raw_config.resolve()
            if raw_config.is_absolute()
            else (settings.root / raw_config).resolve()
        )
        if not resolved_config.is_relative_to(settings.root):
            raise M3SourceDevelopmentError("Research config must stay inside the project.")
        contexts = {
            city_id: context_loader(settings.root, city_id) for city_id in SOURCE_CITY_IDS
        }
        return cls.from_authenticated_components(
            settings=settings,
            phase=phase,
            protocol=protocol,
            amendment=amendment,
            inventory=inventory,
            authorization=authorization,
            config=load_config(resolved_config),
            contexts=contexts,
            hydrator=hydrator,
            signer=signer,
            before_value_access=before_value_access,
        )

    @classmethod
    def from_authenticated_components(
        cls,
        *,
        settings: RunnerSettings,
        phase: str,
        protocol: Mapping[str, Any],
        amendment: Mapping[str, Any],
        inventory: Mapping[str, Any],
        authorization: Mapping[str, Any],
        config: ResearchConfig,
        contexts: Mapping[str, TargetCityContext],
        hydrator: SceneHydrator | None = None,
        signer: Signer = pc.sign,
        before_value_access: ValueAccessGate | None = None,
        reconstructor: Reconstructor = reconstruct_overpass_candidates,
    ) -> M3SourceDevelopmentEngine:
        """Injection seam for deterministic tests and future metadata schema adapters."""

        if phase not in (ONLINE_PHASE, OFFLINE_PHASE):
            raise M3SourceDevelopmentError("Unknown source-development phase.")
        if (
            settings.download_workers not in (1, 2)
            or settings.compute_workers != 1
            or settings.window_size != 512
        ):
            raise M3SourceDevelopmentError("Runner resource limits changed.")
        if phase == OFFLINE_PHASE and hydrator is not None:
            raise M3SourceDevelopmentError("Offline QA may not receive a network hydrator.")
        bindings = _validate_authorized_components(
            protocol, amendment, inventory, authorization
        )
        normalized_contexts = dict(contexts)
        plan = build_cache_plan_from_inventory(
            inventory,
            contexts=normalized_contexts,
            bindings=bindings,
        )

        def default_gate() -> None:
            _validate_authorized_components(protocol, amendment, inventory, authorization)

        engine = cls(
            settings=settings,
            phase=phase,
            protocol=dict(protocol),
            amendment=dict(amendment),
            inventory=dict(inventory),
            authorization=dict(authorization),
            plan=plan,
            config=config,
            contexts=normalized_contexts,
            hydrator=(
                PlanetaryComputerSceneHydrator(
                    stac_api=str(config.raw["landsat"]["stac_api"]),
                    timeout_seconds=settings.network_timeout_seconds,
                )
                if phase == ONLINE_PHASE and hydrator is None
                else hydrator
            ),
            signer=signer,
            before_value_access=(
                default_gate if before_value_access is None else before_value_access
            ),
            reconstructor=reconstructor,
        )
        if phase == ONLINE_PHASE:
            write_scene_plan(settings.cache_root, plan)
        else:
            stored = load_scene_plan(settings.cache_root)
            if stored != plan:
                raise M3SourceDevelopmentError("Offline cache plan differs from inventory.")
            engine._authenticate_cache_completion()
            engine.global_cache_commit = authenticate_global_cache(
                settings.cache_root,
                plan,
                before_value_access=engine.before_value_access,
            )
            _reject_remote_strings(engine.global_cache_commit)
        return engine

    def _authorized_path(self, key: str, *, expected_name: str) -> Path:
        raw = self.authorization.get(key)
        if not isinstance(raw, str) or not raw:
            raise M3SourceDevelopmentError(f"Authorization lacks {key}.")
        path = (self.settings.root / raw).resolve()
        if not path.is_relative_to(self.settings.root) or path.name != expected_name:
            raise M3SourceDevelopmentError(f"Authorized path changed: {key}")
        return path

    def _access_marker_payload(self) -> dict[str, Any]:
        return _with_commit(
            {
                "schema_version": 1,
                "algorithm_version": ALGORITHM_VERSION,
                "state": "source_cache_access_started",
                "claim_id": self.authorization["claim_id"],
                "authorization_commit_sha256": self.authorization["commit_sha256"],
                "expanded_source_inventory_commit_sha256": self.inventory["commit_sha256"],
                "cache_plan_commit_sha256": self.plan["commit_sha256"],
                "source_city_ids": list(SOURCE_CITY_IDS),
                "required_landsat_assets": list(REQUIRED_ASSETS),
                "blind_test_asset_or_value_accessed": False,
                "signed_url_credential_or_cookie_persisted": False,
            }
        )

    def _ensure_access_marker(self) -> dict[str, Any]:
        """Create the first-access marker with O_EXCL, or authenticate its resume."""

        expected = self._access_marker_payload()
        path = self._authorized_path(
            "source_cache_access_started_marker",
            expected_name="SOURCE_CACHE_ACCESS_STARTED.json",
        )
        with _MARKER_LOCK:
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                encoded = (json.dumps(expected, indent=2, ensure_ascii=False) + "\n").encode()
                try:
                    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                except FileExistsError:
                    descriptor = None
                if descriptor is not None:
                    try:
                        with os.fdopen(descriptor, "wb") as handle:
                            handle.write(encoded)
                            handle.flush()
                            os.fsync(handle.fileno())
                    except Exception:
                        path.unlink(missing_ok=True)
                        raise
            observed = _load_committed_json(
                path,
                state="source_cache_access_started",
                label="Source cache access marker",
            )
            if observed != expected:
                raise M3SourceDevelopmentError("Source cache access marker differs.")
            self._access_marker_ready = True
            return observed

    def _cache_completion_payload(self, global_commit: Mapping[str, Any]) -> dict[str, Any]:
        return _with_commit(
            {
                "schema_version": 1,
                "algorithm_version": ALGORITHM_VERSION,
                "state": "source_landsat_cache_complete",
                "claim_id": self.authorization["claim_id"],
                "authorization_commit_sha256": self.authorization["commit_sha256"],
                "expanded_source_inventory_commit_sha256": self.inventory["commit_sha256"],
                "cache_plan_commit_sha256": self.plan["commit_sha256"],
                "global_cache_commit_sha256": global_commit["commit_sha256"],
                "scene_count": global_commit["scene_count"],
                "content_count": global_commit["content_count"],
                "local_only": True,
                "remote_hrefs_signed_urls_tokens_or_cookies_persisted": False,
                "model_fit_or_selection_performed": False,
                "blind_test_asset_or_value_accessed": False,
            }
        )

    def _authenticate_cache_completion(self) -> dict[str, Any]:
        path = self._authorized_path(
            "source_landsat_cache_completion",
            expected_name="SOURCE_LANDSAT_CACHE_COMPLETE.json",
        )
        observed = _load_committed_json(
            path,
            state="source_landsat_cache_complete",
            label="Source Landsat cache completion",
        )
        global_commit = authenticate_global_cache(
            self.settings.cache_root,
            self.plan,
            before_value_access=self.before_value_access,
        )
        if observed != self._cache_completion_payload(global_commit):
            raise M3SourceDevelopmentError("Source cache completion differs.")
        return observed

    def _scene(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        scene_id = _identifier(payload.get("scene_id"), label="scene_id")
        matches = [row for row in self.plan["scenes"] if row["scene_id"] == scene_id]
        if len(matches) != 1:
            raise M3SourceDevelopmentError("Task references an unknown cache scene.")
        scene = dict(matches[0])
        if payload.get("city_id") != scene["city_id"]:
            raise M3SourceDevelopmentError("Task city does not own its scene.")
        context = self.contexts[str(scene["city_id"])]
        if payload.get("grid_sha256") not in (None, context.grid.sha256):
            raise M3SourceDevelopmentError("Task grid binding changed.")
        context_commit = canonical_sha256(context.locks)
        if payload.get("target_context_commit_sha256") not in (None, context_commit):
            raise M3SourceDevelopmentError("Task target-context binding changed.")
        return scene

    def _hydrate_scene(self, scene_id: str) -> dict[str, str]:
        if self.phase != ONLINE_PHASE or self.hydrator is None:
            raise M3SourceDevelopmentError("Network hydration is forbidden in this phase.")
        with self._href_lock:
            existing = self._hrefs_by_scene.get(scene_id)
            if existing is not None:
                return existing
            self._ensure_access_marker()
            self.before_value_access()
            hrefs = dict(self.hydrator(scene_id))
            if tuple(hrefs) != tuple(REQUIRED_ASSETS) and set(hrefs) != set(REQUIRED_ASSETS):
                raise M3SourceDevelopmentError(
                    "Hydrated Landsat item must expose the exact five assets."
                )
            if any(
                not isinstance(href, str) or not href.startswith("https://")
                for href in hrefs.values()
            ):
                raise M3SourceDevelopmentError("Hydrator returned a non-HTTPS asset href.")
            self._hrefs_by_scene[scene_id] = hrefs
            return hrefs

    def _ensure_offline_cache(self) -> dict[str, Any]:
        if self.phase != OFFLINE_PHASE:
            raise M3SourceDevelopmentError("Local QA is forbidden in the online phase.")
        if self.global_cache_commit is None:
            self.global_cache_commit = authenticate_global_cache(
                self.settings.cache_root,
                self.plan,
                before_value_access=self.before_value_access,
            )
        _reject_remote_strings(self.global_cache_commit)
        return self.global_cache_commit

    def _overpass(self, city_id: str, overpass_id: str) -> dict[str, Any]:
        matches = [
            row
            for row in _overpasses(self.inventory)
            if row["city_id"] == city_id and row["overpass_id"] == overpass_id
        ]
        if len(matches) != 1:
            raise M3SourceDevelopmentError("Task references an unknown overpass.")
        return matches[0]

    def _base_lock(self, city_id: str) -> dict[str, Any]:
        global_commit = self._ensure_offline_cache()
        return {
            "protocol_commit_sha256": self.protocol["commit_sha256"],
            "amendment_commit_sha256": self.amendment["commit_sha256"],
            "inventory_commit_sha256": self.inventory["commit_sha256"],
            "authorization_commit_sha256": self.authorization["commit_sha256"],
            "cache_plan_commit_sha256": self.plan["commit_sha256"],
            "global_cache_commit_sha256": global_commit["commit_sha256"],
            "city_id": city_id,
            "target_context_locks_sha256": canonical_sha256(self.contexts[city_id].locks),
        }

    def _overpass_lock(self, row: Mapping[str, Any]) -> dict[str, Any]:
        city_id = str(row["city_id"])
        return {
            **self._base_lock(city_id),
            "overpass_id": str(row["overpass_id"]),
            "overpass_metadata_sha256": canonical_sha256(dict(row)),
            "candidate_config_sha256s": {
                candidate_id: candidate_target_config_sha256(self.config, candidate_id)
                for candidate_id in QA_CANDIDATES
            },
        }

    def execute_download_asset(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if self.phase != ONLINE_PHASE:
            raise M3SourceDevelopmentError("Asset download is forbidden offline.")
        scene = self._scene(payload)
        asset = str(payload.get("asset"))
        if asset not in REQUIRED_ASSETS:
            raise M3SourceDevelopmentError("Task requests an unknown Landsat asset.")

        def in_memory_signer(_: str) -> str:
            canonical_href = self._hydrate_scene(str(scene["scene_id"]))[asset]
            signed = self.signer(canonical_href)
            if not isinstance(signed, str) or not signed:
                raise M3SourceDevelopmentError("In-memory signer returned no href.")
            return signed

        commit = cache_asset_from_href(
            self.settings.cache_root,
            self.plan,
            str(scene["scene_id"]),
            asset,
            "IN_MEMORY_AUTHORIZED_HREF",
            before_value_access=self.before_value_access,
            signer=in_memory_signer,
        )
        return {
            "state": "asset_cached",
            "scene_id": scene["scene_id"],
            "asset": asset,
            "commit_sha256": commit["commit_sha256"],
            "signed_url_persisted": False,
        }

    def execute_finalize_scene(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if self.phase != ONLINE_PHASE:
            raise M3SourceDevelopmentError("Scene finalization is forbidden offline.")
        scene = self._scene(payload)
        commit = finalize_scene_cache(
            self.settings.cache_root,
            self.plan,
            str(scene["scene_id"]),
            before_value_access=self.before_value_access,
        )
        with self._href_lock:
            self._hrefs_by_scene.pop(str(scene["scene_id"]), None)
        return {
            "state": "scene_cached",
            "scene_id": scene["scene_id"],
            "commit_sha256": commit["commit_sha256"],
        }

    def execute_finalize_download(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if self.phase != ONLINE_PHASE:
            raise M3SourceDevelopmentError("Cache finalization is forbidden offline.")
        if payload.get("expected_scene_count") != self.plan["scene_count"]:
            raise M3SourceDevelopmentError("Expected source scene count changed.")
        commit = finalize_global_cache(
            self.settings.cache_root,
            self.plan,
            before_value_access=self.before_value_access,
        )
        completion = self._cache_completion_payload(commit)
        completion_path = self._authorized_path(
            "source_landsat_cache_completion",
            expected_name="SOURCE_LANDSAT_CACHE_COMPLETE.json",
        )
        if completion_path.is_file():
            observed = _load_committed_json(
                completion_path,
                state="source_landsat_cache_complete",
                label="Source Landsat cache completion",
            )
            if observed != completion:
                raise M3SourceDevelopmentError("Append-only cache completion differs.")
        else:
            atomic_json(completion, completion_path)
        return {
            "state": "global_source_cache_complete",
            "scene_count": commit["scene_count"],
            "commit_sha256": completion["commit_sha256"],
            "global_cache_commit_sha256": commit["commit_sha256"],
            "signed_url_persisted": False,
        }

    def execute_qa_overpass(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._ensure_offline_cache()
        city_id = _identifier(payload.get("city_id"), label="city_id")
        overpass_id = _identifier(payload.get("overpass_id"), label="overpass_id")
        if tuple(payload.get("qa_candidate_ids", ())) != QA_CANDIDATES:
            raise M3SourceDevelopmentError("QA candidate order or membership changed.")
        row = self._overpass(city_id, overpass_id)
        for key, expected in (
            ("scene_ids", row["scene_ids"]),
            ("target_date", _target_date(row)),
            ("platform", _platform(row)),
        ):
            if key in payload and payload[key] != expected:
                raise M3SourceDevelopmentError(f"Overpass task changed {key}.")
        context = self.contexts[city_id]
        expected_lock = self._overpass_lock(row)
        directory = self.settings.qa_output_root / "by_overpass" / city_id / overpass_id
        if (directory / OVERPASS_COMMIT).is_file():
            observed = _read_output_commit(directory, OVERPASS_COMMIT)
            if observed.get("cache_lock") != expected_lock:
                raise M3SourceDevelopmentError("Overpass QA output belongs to another lock.")
            return {
                "state": "qa_overpass_complete",
                "cache": "hit",
                "commit_sha256": observed["commit_sha256"],
            }

        def local_loader(requested_city: str, scene_id: str) -> Mapping[str, Any]:
            if requested_city != city_id:
                raise M3SourceDevelopmentError("Local loader crossed a source city.")
            return load_local_scene_arrays(
                self.settings.cache_root,
                self.plan,
                scene_id,
                before_value_access=self.before_value_access,
            )

        results = self.reconstructor(
            city_id=city_id,
            scene_ids=tuple(str(value) for value in row["scene_ids"]),
            loader=local_loader,
            context=context,
            base_config=self.config,
            target_date=_target_date(row),
            overpass_id=overpass_id,
            platform=_platform(row),
            union_city_coverage_fraction=_coverage(row),
            tract_manifest_sha256=str(
                row.get("tract_manifest_sha256", canonical_sha256(context.locks))
            ),
        )
        if tuple(results) != QA_CANDIDATES:
            raise M3SourceDevelopmentError("Offline reconstructor changed candidate order.")
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
        output_files = _write_frames(directory, frames)
        commit = _with_commit(
            {
                "schema_version": 1,
                "algorithm_version": ALGORITHM_VERSION,
                "state": "qa_overpass_complete",
                "cache_lock": expected_lock,
                "candidate_ids": list(QA_CANDIDATES),
                "raw_scene_arrays_loaded_once_per_overpass": True,
                "network_requests_performed": 0,
                "output_files": output_files,
                "model_fit_or_selection_performed": False,
            }
        )
        atomic_json(commit, directory / OVERPASS_COMMIT)
        return {
            "state": "qa_overpass_complete",
            "cache": "built",
            "commit_sha256": commit["commit_sha256"],
        }

    def execute_compile_city(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._ensure_offline_cache()
        city_id = _identifier(payload.get("city_id"), label="city_id")
        if (
            city_id not in SOURCE_CITY_IDS
            or tuple(payload.get("qa_candidate_ids", ())) != QA_CANDIDATES
        ):
            raise M3SourceDevelopmentError("City compile task changed its locked cohort.")
        rows = [row for row in _overpasses(self.inventory) if row["city_id"] == city_id]
        if not rows:
            raise M3SourceDevelopmentError("City compile has no source overpasses.")
        overpass_commits: list[str] = []
        grouped: dict[str, dict[str, list[pd.DataFrame]]] = {
            candidate_id: {"targets": [], "summary": [], "contributions": []}
            for candidate_id in QA_CANDIDATES
        }
        for row in rows:
            directory = (
                self.settings.qa_output_root
                / "by_overpass"
                / city_id
                / str(row["overpass_id"])
            )
            observed = _read_output_commit(directory, OVERPASS_COMMIT)
            if observed.get("cache_lock") != self._overpass_lock(row):
                raise M3SourceDevelopmentError("City compile found a detached overpass.")
            overpass_commits.append(str(observed["commit_sha256"]))
            for candidate_id in QA_CANDIDATES:
                grouped[candidate_id]["targets"].append(
                    pd.read_parquet(directory / candidate_id / "tract_date_qa.parquet")
                )
                grouped[candidate_id]["summary"].append(
                    pd.read_parquet(directory / candidate_id / "date_summary.parquet")
                )
                grouped[candidate_id]["contributions"].append(
                    pd.read_parquet(directory / candidate_id / "scene_contributions.parquet")
                )
        city_lock = {
            **self._base_lock(city_id),
            "overpass_commits_sha256": canonical_sha256(overpass_commits),
            "overpass_count": len(rows),
        }
        destination = self.settings.qa_output_root / "cities" / city_id
        if (destination / CITY_COMMIT).is_file():
            observed = _read_output_commit(destination, CITY_COMMIT)
            if observed.get("cache_lock") != city_lock:
                raise M3SourceDevelopmentError("City QA output belongs to another lock.")
            return {
                "state": "city_qa_candidates_complete",
                "cache": "hit",
                "commit_sha256": observed["commit_sha256"],
            }

        frames: dict[str, pd.DataFrame] = {}
        usable_dates: dict[str, int] = {}
        for candidate_id in QA_CANDIDATES:
            targets = _sort_if_present(
                pd.concat(grouped[candidate_id]["targets"], ignore_index=True),
                ("city_id", "target_date", "tract_geoid"),
            )
            summaries = _sort_if_present(
                pd.concat(grouped[candidate_id]["summary"], ignore_index=True),
                ("city_id", "target_date", "overpass_id"),
            )
            contributions = _sort_if_present(
                pd.concat(grouped[candidate_id]["contributions"], ignore_index=True),
                ("city_id", "target_date", "overpass_id", "tract_geoid", "scene_id"),
            )
            if targets.duplicated(["city_id", "tract_geoid", "target_date"]).any():
                raise M3SourceDevelopmentError("Compiled city target keys are duplicated.")
            if summaries.duplicated(["city_id", "target_date"]).any():
                raise M3SourceDevelopmentError("Compiled city date summaries are duplicated.")
            usable_dates[candidate_id] = int(summaries["date_usable"].astype(bool).sum())
            frames[f"{candidate_id}/targets.parquet"] = targets
            frames[f"{candidate_id}/date_summary.parquet"] = summaries
            frames[f"{candidate_id}/scene_contributions.parquet"] = contributions
        destination.mkdir(parents=True, exist_ok=True)
        output_files = _write_frames(destination, frames)
        commit = _with_commit(
            {
                "schema_version": 1,
                "algorithm_version": ALGORITHM_VERSION,
                "state": "city_qa_candidates_complete",
                "cache_lock": city_lock,
                "candidate_ids": list(QA_CANDIDATES),
                "usable_date_counts": usable_dates,
                "none_support_gate_passed": usable_dates["none"] >= MINIMUM_USABLE_DATES_PER_CITY,
                "output_files": output_files,
                "model_fit_or_selection_performed": False,
            }
        )
        atomic_json(commit, destination / CITY_COMMIT)
        return {
            "state": "city_qa_candidates_complete",
            "cache": "built",
            "commit_sha256": commit["commit_sha256"],
        }

    def execute_finalize_qa_candidates(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._ensure_offline_cache()
        if (
            tuple(payload.get("source_city_ids", ())) != SOURCE_CITY_IDS
            or tuple(payload.get("qa_candidate_ids", ())) != QA_CANDIDATES
            or payload.get("expected_overpass_count") != len(_overpasses(self.inventory))
        ):
            raise M3SourceDevelopmentError("Final QA task changed the locked source cohort.")
        city_commits: list[dict[str, Any]] = []
        counts: dict[str, dict[str, int]] = {}
        for city_id in SOURCE_CITY_IDS:
            directory = self.settings.qa_output_root / "cities" / city_id
            observed = _read_output_commit(directory, CITY_COMMIT)
            city_commits.append(
                {"city_id": city_id, "commit_sha256": observed["commit_sha256"]}
            )
            raw_counts = observed.get("usable_date_counts")
            if not isinstance(raw_counts, Mapping) or tuple(raw_counts) != QA_CANDIDATES:
                raise M3SourceDevelopmentError("City usable-date counts changed.")
            counts[city_id] = {
                candidate_id: int(raw_counts[candidate_id])
                for candidate_id in QA_CANDIDATES
            }
        none_counts = {city_id: values["none"] for city_id, values in counts.items()}
        per_city_pass = all(
            value >= MINIMUM_USABLE_DATES_PER_CITY for value in none_counts.values()
        )
        total_pass = sum(none_counts.values()) >= MINIMUM_TOTAL_USABLE_CITY_DATES
        support_pass = per_city_pass and total_pass
        payload_out = _with_commit(
            {
                "schema_version": 1,
                "algorithm_version": ALGORITHM_VERSION,
                "state": "source_qa_candidates_complete",
                "protocol_commit_sha256": self.protocol["commit_sha256"],
                "amendment_commit_sha256": self.amendment["commit_sha256"],
                "inventory_commit_sha256": self.inventory["commit_sha256"],
                "authorization_commit_sha256": self.authorization["commit_sha256"],
                "cache_plan_commit_sha256": self.plan["commit_sha256"],
                "global_cache_commit_sha256": self._ensure_offline_cache()["commit_sha256"],
                "source_city_ids": list(SOURCE_CITY_IDS),
                "candidate_ids": list(QA_CANDIDATES),
                "city_commits": city_commits,
                "usable_date_counts": counts,
                "support_gate": {
                    "minimum_usable_dates_per_city": MINIMUM_USABLE_DATES_PER_CITY,
                    "minimum_total_usable_city_dates": MINIMUM_TOTAL_USABLE_CITY_DATES,
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
                "network_requests_performed_offline": 0,
                "blind_test_asset_or_target_accessed": False,
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
        self.settings.completion_root.mkdir(parents=True, exist_ok=True)
        path = self._authorized_path(
            "source_qa_candidates_completion",
            expected_name=FINAL_COMMIT,
        )
        if path.is_file():
            observed = _load_committed_json(
                path,
                state="source_qa_candidates_complete",
                label="Source QA candidate completion",
            )
            if observed != payload_out:
                raise M3SourceDevelopmentError("Append-only QA completion differs.")
            return observed
        atomic_json(payload_out, path)
        return payload_out

    def execute(self, kind: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Dispatch one queue task while preserving strict phase isolation."""

        online = {
            "download_asset": self.execute_download_asset,
            "finalize_scene": self.execute_finalize_scene,
            "finalize_download": self.execute_finalize_download,
        }
        offline = {
            "qa_overpass": self.execute_qa_overpass,
            "compile_qa_city": self.execute_compile_city,
            "finalize_qa_candidates": self.execute_finalize_qa_candidates,
        }
        allowed = online if self.phase == ONLINE_PHASE else offline
        if kind not in allowed:
            raise M3SourceDevelopmentError(
                f"Task {kind!r} is forbidden during phase {self.phase!r}."
            )
        return allowed[kind](payload)
