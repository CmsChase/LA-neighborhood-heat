"""Target-blind evidence runner for the three deferred portable-contract gaps.

The runner may read only public Census geometry, ESA WorldCover classes, and
small real Sentinel-2 L2A calibration probes.  It cannot read a Landsat
thermal/QA value, construct a predictor, fit a model, or inspect a continuation
result.  Fifteen append-only JSON records are written in a fixed order; the
overall terminal is always last.
"""

from __future__ import annotations

import json
import os
import subprocess
import tomllib
import uuid
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from la_heat.provenance import canonical_sha256, sha256_file

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "multicity-missing-support-calibration-evidence-v1"
STAGE_ID: Final = "target_blind_missing_support_and_calibration_evidence_v1"
COMPLETE_STATE: Final = "complete_target_blind_missing_support_and_calibration_evidence"
CONFIG_PATH: Final = "configs/multicity/missing_support_calibration_evidence_v1.toml"
CONFIG_SHA256: Final = "f0b066da60768f6931e26e0498fba8bfd230af20009708d8991e3bfacb39d586"
PLAN_PATH: Final = "manifests/multicity/ACTIVE_STAGE.json"
PREDECESSOR_TERMINAL_PATH: Final = (
    "manifests/multicity/reviews/portable_predictor_contract/"
    "PORTABLE_PREDICTOR_CONTRACT_FREEZE_V2.json"
)
OVERALL_TERMINAL_PATH: Final = (
    "manifests/multicity/reviews/portable_predictor_contract/"
    "MISSING_SUPPORT_CALIBRATION_EVIDENCE_V1.json"
)

CITY_IDS: Final = (
    "los_angeles_ca",
    "phoenix_az",
    "houston_tx",
    "chicago_il",
)
EXTERNAL_CITY_IDS: Final = ("phoenix_az", "houston_tx", "chicago_il")

GEOGRAPHY_GLOBAL_PATH: Final = (
    "manifests/multicity/reviews/portable_predictor_contract/FOUR_CITY_GEOGRAPHY_CONTRACT_V1.json"
)
WORLDCOVER_GLOBAL_PATH: Final = (
    "manifests/multicity/reviews/portable_predictor_contract/"
    "FOUR_CITY_WORLDCOVER_ELIGIBLE_SUPPORT_V1.json"
)
SENTINEL_GLOBAL_PATH: Final = (
    "manifests/multicity/reviews/portable_predictor_contract/"
    "SENTINEL_CALIBRATION_SMOKE_EVIDENCE_V1.json"
)


def _city_geography_path(city_id: str) -> str:
    return f"manifests/multicity/cities/{city_id}/geography/GEOGRAPHY_CONTRACT_V1.json"


def _city_worldcover_path(city_id: str) -> str:
    return (
        f"manifests/multicity/cities/{city_id}/eligible_support/WORLDCOVER_ELIGIBLE_SUPPORT_V1.json"
    )


def _city_sentinel_path(city_id: str) -> str:
    return (
        f"manifests/multicity/cities/{city_id}/sentinel_calibration_smoke/"
        "SENTINEL_CALIBRATION_SMOKE_V1.json"
    )


TRACKED_OUTPUT_PATHS: Final = (
    *(_city_geography_path(city_id) for city_id in CITY_IDS),
    GEOGRAPHY_GLOBAL_PATH,
    *(_city_worldcover_path(city_id) for city_id in CITY_IDS),
    WORLDCOVER_GLOBAL_PATH,
    *(_city_sentinel_path(city_id) for city_id in EXTERNAL_CITY_IDS),
    SENTINEL_GLOBAL_PATH,
    OVERALL_TERMINAL_PATH,
)

CODE_PATHS: Final = (
    CONFIG_PATH,
    "configs/multicity/experiment.toml",
    "configs/multicity/portable_predictor_source_evidence_v1.toml",
    "configs/multicity/portable_predictor_contract_freeze_v2.toml",
    *(f"configs/multicity/cities/{city_id}.toml" for city_id in CITY_IDS),
    "scripts/stage_multicity_missing_support_calibration_evidence_v1.py",
    "src/la_heat/grid.py",
    "src/la_heat/multicity/config.py",
    "src/la_heat/multicity/four_city_geography_contract_v1.py",
    "src/la_heat/multicity/geography.py",
    "src/la_heat/multicity/missing_support_calibration_evidence_v1.py",
    "src/la_heat/multicity/portable_predictor_source_evidence_v1.py",
    "src/la_heat/multicity/sentinel_calibration_smoke_v1.py",
    "src/la_heat/multicity/source_footprints.py",
    "src/la_heat/multicity/worldcover_eligible_support_evidence_v1.py",
    "src/la_heat/provenance.py",
    "src/la_heat/sentinel_features.py",
    "src/la_heat/sentinel_inventory.py",
)

EXPECTED_AUTHORIZED_NOW: Final = {
    "boundary_and_public_metadata_staging": False,
    "target_blind_source_geometry_review": False,
    "target_blind_gshhg_l3_hierarchy_preregistration": False,
    "target_blind_gshhg_l3_hierarchy_geometry_read": False,
    "portable_predictor_source_freeze": False,
    "portable_predictor_source_and_calibration_contract_freeze": False,
    "predictor_construction": False,
    "model_fitting": False,
    "external_target_or_qa_value_access": False,
    "one_time_external_evaluation": False,
    "operational_forecast_claim": False,
    "portable_predictor_missing_source_evidence_staging": False,
    "portable_predictor_source_and_calibration_contract_freeze_v2": False,
    "portable_predictor_missing_support_and_calibration_evidence_staging": True,
}

EXPECTED_LOCKS: Final = {
    "protocol_locked": False,
    "external_targets_unlocked": False,
    "external_target_values_read": False,
    "external_prediction_commit_exists": False,
    "portable_water_distance_source_locked": True,
    "portable_water_distance_algorithm_locked": True,
    "portable_water_distance_feature_names_frozen": False,
    "predictor_build_authorized": False,
    "protocol_lock_created": False,
}

_FORBIDDEN_PARTS: Final = frozenset(
    {
        "final_test_2025",
        "final_evaluation",
        "feature_ablation",
        "model_lock",
        "models",
        "predictions",
        "targets",
    }
)
_SECRET_QUERY_KEYS: Final = frozenset(
    {"sig", "st", "se", "sp", "sv", "srt", "spr", "token", "credential"}
)


class MissingSupportCalibrationEvidenceV1Error(ValueError):
    """Raised when the active evidence contract cannot be authenticated."""


@dataclass(frozen=True, slots=True)
class EvidenceConfig:
    """Validated immutable evidence configuration."""

    path: Path
    project_root: Path
    raw: dict[str, Any]

    @property
    def semantic_sha256(self) -> str:
        return canonical_sha256(self.raw)

    def project_path(self, value: str, *, allow_pilot: bool = False) -> Path:
        path = (self.project_root / value).resolve()
        if not path.is_relative_to(self.project_root):
            raise MissingSupportCalibrationEvidenceV1Error(
                f"Configured path escapes the project root: {value}"
            )
        forbidden = {part.lower() for part in path.parts} & _FORBIDDEN_PARTS
        if forbidden:
            raise MissingSupportCalibrationEvidenceV1Error(
                f"Configured path enters a prohibited result area: {value}"
            )
        if not allow_pilot and "pilot" in {part.lower() for part in path.parts}:
            raise MissingSupportCalibrationEvidenceV1Error(
                f"Pilot input needs an explicit compatibility-read allowance: {value}"
            )
        return path


def expected_authorized_now() -> dict[str, bool]:
    """Return the sole permission map accepted by the evidence runner."""

    return deepcopy(EXPECTED_AUTHORIZED_NOW)


def expected_plan_authorization_scope() -> dict[str, Any]:
    """Return the scientific scope recorded by the active evidence stage."""

    return {
        "stage_id": STAGE_ID,
        "experiment_id": "la_to_three_city_zero_shot_v1",
        "configuration": {"path": CONFIG_PATH, "sha256": CONFIG_SHA256},
        "predecessor_terminal_path": PREDECESSOR_TERMINAL_PATH,
        "cities": list(CITY_IDS),
        "external_sentinel_cities": list(EXTERNAL_CITY_IDS),
        "code_paths": list(CODE_PATHS),
        "evidence_tasks": {
            "geography": (
                "four-city Census-2020 same-adapter rebuild/replay plus "
                "Los Angeles and Phoenix compatibility evidence"
            ),
            "worldcover": (
                "exact 2020-v100 item/assets, local mosaic, and invariant "
                "30 m eligible-cell identities"
            ),
            "sentinel": (
                "one real product-metadata and seven-band/SCL native-DN "
                "probe per contributing external native UTM zone"
            ),
        },
        "allowed_reads": {
            "authenticated_predecessor_and_source_manifests": True,
            "public_census_geometry": True,
            "los_angeles_pilot_geography_for_compatibility_only": True,
            "saved_source_metadata_for_geometry_and_probe_selection": True,
            "worldcover_static_land_class_assets": True,
            "sentinel_product_metadata_and_small_native_dn_windows": True,
        },
        "prohibited_reads_and_actions": {
            "landsat_thermal_or_target_qa_values": True,
            "external_target_or_lst_values": True,
            "final_evaluation_or_feature_ablation_outputs": True,
            "predictor_values_or_predictor_construction": True,
            "sentinel_indices_or_tract_features": True,
            "model_fit_prediction_or_scoring": True,
            "protocol_promotion_or_external_target_unlock": True,
        },
        "network": {
            "only_configured_https_hosts_and_paths": True,
            "redirects_allowed": False,
            "maximum_geography_requests": 64,
            "maximum_worldcover_requests": 64,
            "maximum_sentinel_requests": 1024,
            "maximum_worldcover_asset_bytes": 12_884_901_888,
            "maximum_sentinel_probe_bytes": 1_073_741_824,
            "signed_urls_persisted": False,
        },
        "tracked_output_paths": list(TRACKED_OUTPUT_PATHS),
        "write_contract": {
            "append_only": True,
            "expected_output_count": 15,
            "overall_terminal_written_last": True,
            "check_only_network_requests": 0,
            "tracked_outputs_must_match_head": True,
        },
        "next_gate": "portable_predictor_contract_decision",
    }


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], *, label: str) -> None:
    if set(value) != expected:
        raise MissingSupportCalibrationEvidenceV1Error(
            f"{label} keys changed; missing={sorted(expected - set(value))}, "
            f"unexpected={sorted(set(value) - expected)}."
        )


def read_evidence_config(path: str | Path = CONFIG_PATH) -> EvidenceConfig:
    """Load and authenticate the active scientific configuration."""

    config_path = Path(path).resolve()
    if not config_path.is_file() or config_path.is_symlink():
        raise FileNotFoundError(config_path)
    observed_sha = sha256_file(config_path)
    if observed_sha != CONFIG_SHA256:
        raise MissingSupportCalibrationEvidenceV1Error(
            "The active evidence configuration SHA-256 changed."
        )
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    _require_exact_keys(
        raw,
        {
            "stage",
            "geography",
            "worldcover",
            "sentinel",
            "outputs",
            "publication",
            "access_contract",
            "next_gate",
        },
        label="evidence config",
    )
    project_root = config_path.parents[2]
    stage = raw["stage"]
    if stage != {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "stage_id": STAGE_ID,
        "experiment_id": "la_to_three_city_zero_shot_v1",
        "experiment_config": "configs/multicity/experiment.toml",
        "plan_path": PLAN_PATH,
        "predecessor_terminal": PREDECESSOR_TERMINAL_PATH,
        "contract_config": "configs/multicity/portable_predictor_contract_freeze_v2.toml",
        "city_ids": list(CITY_IDS),
        "external_city_ids": list(EXTERNAL_CITY_IDS),
        "source_city_id": "los_angeles_ca",
        "analysis_crs": "EPSG:5070",
        "confirmation_year": 2025,
        "target_blind": True,
        "predictor_construction_authorized": False,
        "model_fit_authorized": False,
        "external_target_access_authorized": False,
    }:
        raise MissingSupportCalibrationEvidenceV1Error(
            "The evidence stage identity or access boundary changed."
        )
    outputs = raw["outputs"]
    expected_outputs = {
        "raw_stage_directory": "data/raw/multicity/missing_support_calibration_evidence_v1",
        "processed_stage_directory": (
            "data/processed/multicity/missing_support_calibration_evidence_v1"
        ),
        "progress_path": (
            "data/interim/multicity/missing_support_calibration_evidence_v1/status.json"
        ),
        "overall_terminal": OVERALL_TERMINAL_PATH,
        "geography_terminal": GEOGRAPHY_GLOBAL_PATH,
        "worldcover_terminal": WORLDCOVER_GLOBAL_PATH,
        "sentinel_terminal": SENTINEL_GLOBAL_PATH,
        "geography_city_manifest_name": "GEOGRAPHY_CONTRACT_V1.json",
        "worldcover_city_manifest_name": "WORLDCOVER_ELIGIBLE_SUPPORT_V1.json",
        "sentinel_city_manifest_name": "SENTINEL_CALIBRATION_SMOKE_V1.json",
        "append_only": True,
        "overwrite_allowed": False,
        "terminal_written_last": True,
    }
    if outputs != expected_outputs:
        raise MissingSupportCalibrationEvidenceV1Error("Evidence output paths changed.")
    if raw["publication"] != {
        "expected_city_manifest_count": 11,
        "expected_global_terminal_count": 4,
        "expected_total_tracked_output_count": 15,
        "tracked_outputs_must_match_head": True,
    }:
        raise MissingSupportCalibrationEvidenceV1Error("Evidence publication contract changed.")
    if len(TRACKED_OUTPUT_PATHS) != 15 or len(set(TRACKED_OUTPUT_PATHS)) != 15:
        raise MissingSupportCalibrationEvidenceV1Error(
            "The tracked output set is not the exact fifteen-file set."
        )
    return EvidenceConfig(path=config_path, project_root=project_root, raw=raw)


def canonical_unsigned_url(value: str) -> str:
    """Canonicalize one HTTPS asset identity without persisting any SAS secret."""

    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https" or not parsed.hostname or not parsed.path:
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Asset URL is not canonical HTTPS: {value!r}"
        )
    return urlunsplit(("https", parsed.netloc.lower(), parsed.path, "", ""))


def assert_no_secrets(value: object, *, label: str = "manifest") -> None:
    """Reject signed URLs, bearer values, and credential-like fields recursively."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).casefold()
            if lowered in _SECRET_QUERY_KEYS or any(
                marker in lowered for marker in ("bearer", "password", "secret", "credential")
            ):
                raise MissingSupportCalibrationEvidenceV1Error(
                    f"{label} contains a secret-like field: {key}"
                )
            assert_no_secrets(child, label=label)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            assert_no_secrets(child, label=label)
        return
    if isinstance(value, str):
        parsed = urlsplit(value)
        if parsed.query:
            keys = {key.casefold() for key, _ in parse_qsl(parsed.query)}
            if keys & _SECRET_QUERY_KEYS:
                raise MissingSupportCalibrationEvidenceV1Error(
                    f"{label} contains a signed or credential-bearing URL."
                )
        if value.casefold().startswith("bearer "):
            raise MissingSupportCalibrationEvidenceV1Error(f"{label} contains a bearer credential.")


def _expected_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(payload), indent=2).encode("utf-8")


def json_with_commit(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy with the repository-standard internal semantic commit."""

    result = deepcopy(dict(payload))
    if "commit_sha256" in result:
        raise MissingSupportCalibrationEvidenceV1Error(
            "Caller may not prepopulate an internal commit."
        )
    assert_no_secrets(result)
    result["commit_sha256"] = canonical_sha256(result)
    return result


def read_json_with_commit(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise MissingSupportCalibrationEvidenceV1Error(f"{label} is not one regular file: {path}")
    before = path.read_bytes()
    after = path.read_bytes()
    if before != after:
        raise MissingSupportCalibrationEvidenceV1Error(f"{label} changed while it was read.")
    try:
        payload = json.loads(before.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MissingSupportCalibrationEvidenceV1Error(f"Cannot parse {label}.") from exc
    if not isinstance(payload, dict):
        raise MissingSupportCalibrationEvidenceV1Error(f"{label} is not an object.")
    recorded = payload.get("commit_sha256")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or canonical_sha256(body) != recorded:
        raise MissingSupportCalibrationEvidenceV1Error(f"{label} has an invalid internal commit.")
    assert_no_secrets(payload, label=label)
    return payload


def atomic_bytes_no_clobber(
    content: bytes, destination: Path, *, accept_identical: bool = True
) -> None:
    """Publish bytes without an overwrite race; identical cache replay is optional."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not accept_identical or not destination.is_file() or destination.read_bytes() != content:
            raise MissingSupportCalibrationEvidenceV1Error(
                f"Existing append-only artifact differs: {destination}"
            )
        return
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.partial")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError as exc:
            raise MissingSupportCalibrationEvidenceV1Error(
                f"Append-only publication raced at {destination}."
            ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def write_manifest_no_clobber(payload: Mapping[str, Any], destination: Path) -> None:
    committed = json_with_commit(payload)
    atomic_bytes_no_clobber(_expected_json_bytes(committed), destination, accept_identical=True)


def file_record(config: EvidenceConfig, path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    if not resolved.is_relative_to(config.project_root):
        raise MissingSupportCalibrationEvidenceV1Error(f"Artifact escapes project root: {path}")
    relative = resolved.relative_to(config.project_root).as_posix()
    return {
        "path": relative,
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def checkpoint_record(config: EvidenceConfig, path: Path) -> dict[str, Any]:
    payload = read_json_with_commit(path, label=path.as_posix())
    return {
        **file_record(config, path),
        "state": payload.get("state"),
        "commit_sha256": payload["commit_sha256"],
    }


def update_progress(
    config: EvidenceConfig,
    *,
    state: str,
    completed_tasks: int,
    total_tasks: int,
    current_task: str | None,
    error: str | None = None,
) -> None:
    """Write an ignored human-readable status snapshot; never include URLs."""

    payload = {
        "schema_version": 1,
        "state": state,
        "updated_at_utc": datetime.now(UTC).isoformat(),
        "completed_tasks": completed_tasks,
        "total_tasks": total_tasks,
        "current_task": current_task,
        "error": error,
    }
    assert_no_secrets(payload, label="progress")
    path = config.project_path(str(config.raw["outputs"]["progress_path"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
    temporary.write_bytes(_expected_json_bytes(payload))
    os.replace(temporary, path)


def _run_git(project_root: Path, *arguments: str, binary: bool = False) -> str | bytes:
    completed = subprocess.run(
        ["git", "-C", str(project_root), *arguments],
        check=False,
        capture_output=True,
        text=not binary,
    )
    if completed.returncode:
        stderr = completed.stderr.decode("utf-8", errors="replace") if binary else completed.stderr
        raise MissingSupportCalibrationEvidenceV1Error(
            f"Git authentication failed for {' '.join(arguments)}: {stderr.strip()}"
        )
    return completed.stdout


def _status_paths(raw: bytes) -> tuple[str, ...]:
    fields = raw.split(b"\0")
    if fields[-1:] != [b""]:
        raise MissingSupportCalibrationEvidenceV1Error("Git status is not NUL terminated.")
    paths: list[str] = []
    for field in fields[:-1]:
        if len(field) < 4:
            raise MissingSupportCalibrationEvidenceV1Error("Git status is malformed.")
        paths.append(field[3:].decode("utf-8"))
    return tuple(paths)


def authenticate_plan(
    config: EvidenceConfig, *, allowed_untracked_outputs: Sequence[str] = ()
) -> dict[str, Any]:
    """Confirm the active stage and resumable output set before networking."""

    status = _run_git(
        config.project_root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        binary=True,
    )
    assert isinstance(status, bytes)
    observed_paths = set(_status_paths(status))
    allowed = set(allowed_untracked_outputs)
    if observed_paths != allowed or not observed_paths.issubset(TRACKED_OUTPUT_PATHS):
        raise MissingSupportCalibrationEvidenceV1Error(
            "Evidence execution requires only the declared resumable outputs in the worktree."
        )

    plan_path = config.project_path(PLAN_PATH)
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MissingSupportCalibrationEvidenceV1Error(
            "Cannot read the active multicity stage."
        ) from exc
    if (
        not isinstance(plan, dict)
        or plan.get("schema_version") != 1
        or plan.get("stage_id") != STAGE_ID
        or plan.get("state") != "ready"
        or plan.get("next_safe_stage")
        != "stage_target_blind_missing_support_and_calibration_evidence_v1"
        or plan.get("tracked_output_paths") != list(TRACKED_OUTPUT_PATHS)
        or plan.get("permissions")
        != {
            "run_public_evidence": True,
            "build_predictor": False,
            "fit_or_score_model": False,
            "read_external_targets": False,
        }
        or plan.get("locks") != EXPECTED_LOCKS
    ):
        raise MissingSupportCalibrationEvidenceV1Error(
            "The active multicity stage does not authorize this evidence run."
        )

    for relative in sorted(allowed):
        payload = read_json_with_commit(config.project_path(relative), label=relative)
        if not str(payload.get("state", "")).startswith("complete_target_blind"):
            raise MissingSupportCalibrationEvidenceV1Error(
                f"Resume checkpoint is not complete: {relative}"
            )

    return {
        "path": PLAN_PATH,
        "bytes": plan_path.stat().st_size,
        "file_sha256": sha256_file(plan_path),
        "revision": plan["revision"],
    }


def authenticate_publication(config: EvidenceConfig) -> str:
    """Confirm that the fifteen evidence outputs are committed and unchanged."""

    head = str(_run_git(config.project_root, "rev-parse", "HEAD")).strip()
    status = _run_git(
        config.project_root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        binary=True,
    )
    assert isinstance(status, bytes)
    if status:
        raise MissingSupportCalibrationEvidenceV1Error(
            "Publication authentication requires a clean worktree."
        )

    for relative in TRACKED_OUTPUT_PATHS:
        worktree = config.project_path(relative)
        try:
            head_bytes = _run_git(
                config.project_root, "show", f"{head}:{relative}", binary=True
            )
        except MissingSupportCalibrationEvidenceV1Error as exc:
            raise MissingSupportCalibrationEvidenceV1Error(
                f"Evidence output is not committed: {relative}"
            ) from exc
        assert isinstance(head_bytes, bytes)
        if not worktree.is_file() or worktree.read_bytes() != head_bytes:
            raise MissingSupportCalibrationEvidenceV1Error(
                f"Tracked evidence differs from HEAD: {relative}"
            )
    return head


def _tracked_outputs_present(config: EvidenceConfig) -> tuple[str, ...]:
    return tuple(
        relative for relative in TRACKED_OUTPUT_PATHS if config.project_path(relative).exists()
    )


def verify_terminal(config: EvidenceConfig, *, require_publication: bool = True) -> dict[str, Any]:
    """Authenticate every checkpoint without networking or scientific recompute."""

    terminal_path = config.project_path(OVERALL_TERMINAL_PATH)
    terminal = read_json_with_commit(terminal_path, label="evidence terminal")
    if (
        terminal.get("schema_version") != SCHEMA_VERSION
        or terminal.get("algorithm_version") != ALGORITHM_VERSION
        or terminal.get("state") != COMPLETE_STATE
        or terminal.get("tracked_output_paths") != list(TRACKED_OUTPUT_PATHS)
        or terminal.get("terminal_written_last") is not True
        or terminal.get("access_contract") != config.raw["access_contract"]
    ):
        raise MissingSupportCalibrationEvidenceV1Error(
            "The evidence terminal contract changed."
        )
    checkpoints = terminal.get("tracked_checkpoints")
    expected_checkpoint_paths = set(TRACKED_OUTPUT_PATHS[:-1])
    if not isinstance(checkpoints, dict) or set(checkpoints) != expected_checkpoint_paths:
        raise MissingSupportCalibrationEvidenceV1Error("The terminal checkpoint set is not exact.")
    for relative, record in checkpoints.items():
        path = config.project_path(relative)
        payload = read_json_with_commit(path, label=relative)
        expected_record = checkpoint_record(config, path)
        if record != expected_record or payload.get("state") != record.get("state"):
            raise MissingSupportCalibrationEvidenceV1Error(
                f"Checkpoint identity changed: {relative}"
            )
    from la_heat.multicity.four_city_geography_contract_v1 import (
        _verify_global as verify_geography,
    )
    from la_heat.multicity.sentinel_calibration_smoke_v1 import (
        _verify_global as verify_sentinel,
    )
    from la_heat.multicity.worldcover_eligible_support_evidence_v1 import (
        _verify_global as verify_worldcover,
    )

    verified = {
        "geography_commit_sha256": verify_geography(config)["commit_sha256"],
        "worldcover_commit_sha256": verify_worldcover(config)["commit_sha256"],
        "sentinel_commit_sha256": verify_sentinel(config)["commit_sha256"],
    }
    if terminal.get("evidence") != verified:
        raise MissingSupportCalibrationEvidenceV1Error(
            "The overall terminal lost a verified task terminal."
        )
    result = deepcopy(terminal)
    if require_publication:
        result["publication_status"] = "authenticated_git_publication"
        result["publication_git_commit"] = authenticate_publication(config)
    else:
        result["publication_status"] = "awaiting_git_publication"
    return result


def stage_missing_support_calibration_evidence_v1(
    config_path: str | Path = CONFIG_PATH,
    *,
    check_only: bool = False,
    session: Any | None = None,
) -> dict[str, Any]:
    """Run or authenticate the three target-blind evidence tasks."""

    config = read_evidence_config(config_path)
    overall = config.project_path(OVERALL_TERMINAL_PATH)
    if check_only:
        return verify_terminal(config, require_publication=True)
    if overall.is_file():
        tracked_at_head = (
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(config.project_root),
                    "cat-file",
                    "-e",
                    f"HEAD:{OVERALL_TERMINAL_PATH}",
                ],
                check=False,
                capture_output=True,
            ).returncode
            == 0
        )
        return verify_terminal(config, require_publication=tracked_at_head)

    partial = _tracked_outputs_present(config)
    plan_record = authenticate_plan(config, allowed_untracked_outputs=partial)
    update_progress(
        config,
        state="running",
        completed_tasks=0,
        total_tasks=3,
        current_task="four_city_geography_contract",
    )
    try:
        from la_heat.multicity.four_city_geography_contract_v1 import (
            stage_four_city_geography_contract_v1,
        )
        from la_heat.multicity.sentinel_calibration_smoke_v1 import (
            stage_sentinel_calibration_smoke_v1,
        )
        from la_heat.multicity.worldcover_eligible_support_evidence_v1 import (
            stage_worldcover_eligible_support_evidence_v1,
        )

        geography = stage_four_city_geography_contract_v1(
            config, plan_record=plan_record, session=session
        )
        update_progress(
            config,
            state="running",
            completed_tasks=1,
            total_tasks=3,
            current_task="four_city_worldcover_eligible_support",
        )
        worldcover = stage_worldcover_eligible_support_evidence_v1(
            config, plan_record=plan_record, session=session
        )
        update_progress(
            config,
            state="running",
            completed_tasks=2,
            total_tasks=3,
            current_task="external_city_sentinel_calibration_smoke",
        )
        sentinel = stage_sentinel_calibration_smoke_v1(
            config,
            plan_record=plan_record,
            worldcover_terminal=worldcover,
            session=session,
        )
    except Exception as exc:
        update_progress(
            config,
            state="failed",
            completed_tasks=len(
                [
                    path
                    for path in (
                        GEOGRAPHY_GLOBAL_PATH,
                        WORLDCOVER_GLOBAL_PATH,
                        SENTINEL_GLOBAL_PATH,
                    )
                    if config.project_path(path).is_file()
                ]
            ),
            total_tasks=3,
            current_task=None,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise

    checkpoints = {
        relative: checkpoint_record(config, config.project_path(relative))
        for relative in TRACKED_OUTPUT_PATHS[:-1]
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": COMPLETE_STATE,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "experiment_id": "la_to_three_city_zero_shot_v1",
        "plan_authorization": plan_record,
        "authorization_scope": expected_plan_authorization_scope(),
        "config": {
            **file_record(config, config.path),
            "semantic_sha256": config.semantic_sha256,
        },
        "evidence": {
            "geography_commit_sha256": geography["commit_sha256"],
            "worldcover_commit_sha256": worldcover["commit_sha256"],
            "sentinel_commit_sha256": sentinel["commit_sha256"],
        },
        "tracked_output_paths": list(TRACKED_OUTPUT_PATHS),
        "tracked_checkpoints": checkpoints,
        "tracked_output_set_exact": True,
        "terminal_written_last": True,
        "access_contract": dict(config.raw["access_contract"]),
        "locks": EXPECTED_LOCKS,
        "predictor_contract_locked": False,
        "predictor_build_authorized": False,
        "external_targets_unlocked": False,
        "next_gate": dict(config.raw["next_gate"]),
    }
    write_manifest_no_clobber(payload, overall)
    update_progress(
        config,
        state="complete_awaiting_git_publication",
        completed_tasks=3,
        total_tasks=3,
        current_task=None,
    )
    return verify_terminal(config, require_publication=False)
