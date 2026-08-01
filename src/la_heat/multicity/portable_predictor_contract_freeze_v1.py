"""Audit whether the portable predictor contract can be frozen.

The V1 decision is intentionally target-blind and tracked-only.  It reads the
canonical planning record, the water-distance terminal, the Phoenix metadata
pilot, exact configuration/code blobs, and local Git lineage.  It performs no
network request and opens no source payload, geometry, eligible support,
predictor, model, target, or result.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tomllib
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, Final

from la_heat.multicity.portable_water_distance_freeze_v2 import (
    CODE_PATHS as V2_RUNTIME_PATHS,
)
from la_heat.provenance import canonical_sha256, code_runtime_fingerprint

ALGORITHM_VERSION: Final = "portable-predictor-contract-freeze-v1"
CONFIG_PATH: Final = (
    "configs/multicity/portable_predictor_contract_freeze_v1.toml"
)
CONFIG_SHA256: Final = (
    "536364a9a44b0fbf04a7880f2053cb2b5ae6d9badbe12861236773d239362c62"
)
PLAN_PATH: Final = "manifests/multicity/PLAN_READINESS.json"
V2_TERMINAL_PATH: Final = (
    "manifests/multicity/reviews/portable_water_distance/"
    "WATER_DISTANCE_FREEZE_DECISION_V2.json"
)
PHOENIX_SOURCE_PATH: Final = (
    "manifests/multicity/cities/phoenix_az/source_footprints/"
    "SOURCE_FOOTPRINTS.json"
)
ABSENT_SOURCE_PATHS: Final = (
    "manifests/multicity/cities/houston_tx/source_footprints/"
    "SOURCE_FOOTPRINTS.json",
    "manifests/multicity/cities/chicago_il/source_footprints/"
    "SOURCE_FOOTPRINTS.json",
)
OUTPUT_PATH: Final = (
    "manifests/multicity/reviews/portable_predictor_contract/"
    "PORTABLE_PREDICTOR_CONTRACT_FREEZE_V1.json"
)

V8_MODULE_PATH: Final = (
    "src/la_heat/multicity/plan_predictor_contract_transition_v8.py"
)
V8_SCRIPT_PATH: Final = (
    "scripts/authorize_multicity_predictor_contract_freeze.py"
)
MODULE_PATH: Final = (
    "src/la_heat/multicity/portable_predictor_contract_freeze_v1.py"
)
SCRIPT_PATH: Final = (
    "scripts/audit_multicity_portable_predictor_contract_freeze_v1.py"
)
PROVENANCE_PATH: Final = "src/la_heat/provenance.py"
CODE_PATHS: Final = tuple(
    dict.fromkeys(
        (
            *V2_RUNTIME_PATHS,
            CONFIG_PATH,
            V8_MODULE_PATH,
            V8_SCRIPT_PATH,
            MODULE_PATH,
            SCRIPT_PATH,
            PROVENANCE_PATH,
        )
    )
)

V2_PUBLICATION_COMMIT: Final = "91a31fd9e1793bbfa9c9f751459fc73d0e0bbb4c"
V2_FILE_SHA256: Final = (
    "a25a8712d28bc3b6ccee3e5711f31d92d6e5996047f88635c49ba26bb74afb4b"
)
V2_INTERNAL_COMMIT_SHA256: Final = (
    "2416e9b4cdc0c823fb6bcfdc501f2c298f3afa09b8fbd70ed6371f3aac868a51"
)
V2_BYTES: Final = 18_541
PHOENIX_PUBLICATION_COMMIT: Final = "44ecdcd2e48c68fa1c67a7ff0c2d1a54d5d3a785"
PHOENIX_FILE_SHA256: Final = (
    "76a667f559a98bf6281d7f8af71fab18c2fac8d3701ea91813eef8ff8ef479df"
)
PHOENIX_INTERNAL_COMMIT_SHA256: Final = (
    "a6f287daa22c41d893519dc751848c426b819dcf4ae00d33012fbf11c6073ed7"
)
PHOENIX_BYTES: Final = 18_861

STATE: Final = "decision_complete_contract_freeze_deferred_predictor_closed"
OUTCOME: Final = (
    "defer_until_cross_city_source_footprints_and_static_source_contracts_complete"
)
EXPECTED_BLOCKERS: Final = (
    "houston_source_footprint_manifest_absent",
    "chicago_source_footprint_manifest_absent",
    "phoenix_nlcd_source_family_absent",
    "phoenix_terrain_content_and_schema_unfrozen",
)
NEXT_SAFE_STAGE: Final = (
    "stage_missing_portable_predictor_source_evidence_before_v2_freeze"
)


class PortablePredictorContractFreezeV1Error(ValueError):
    """Raised when the tracked-only V1 decision cannot be authenticated."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_equal(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(actual) == set(expected) and all(  # type: ignore[arg-type]
            _strict_equal(actual[key], expected[key])  # type: ignore[index]
            for key in expected
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(  # type: ignore[arg-type]
            _strict_equal(left, right)
            for left, right in zip(actual, expected, strict=True)  # type: ignore[arg-type]
        )
    return bool(actual == expected)


def _require_mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PortablePredictorContractFreezeV1Error(
            f"{label} must be an object."
        )
    return value


def _run_git(
    project_root: Path,
    *arguments: str,
    binary: bool = False,
    accepted_returncodes: tuple[int, ...] = (0,),
) -> str | bytes:
    completed = subprocess.run(
        ["git", "-C", str(project_root), *arguments],
        check=False,
        capture_output=True,
        text=not binary,
    )
    if completed.returncode not in accepted_returncodes:
        stderr = (
            completed.stderr.decode("utf-8", errors="replace")
            if binary
            else completed.stderr
        )
        raise PortablePredictorContractFreezeV1Error(
            f"Git authentication failed for {' '.join(arguments)}: "
            f"{stderr.strip()}"
        )
    return completed.stdout


def _git_regular_blob(
    project_root: Path,
    *,
    commit: str,
    relative_path: str,
) -> tuple[bytes, str, str]:
    tree = _run_git(project_root, "ls-tree", commit, "--", relative_path)
    assert isinstance(tree, str)
    parts = tree.strip().split(maxsplit=3)
    if (
        len(parts) != 4
        or parts[0] not in {"100644", "100755"}
        or parts[1] != "blob"
        or parts[3] != relative_path
    ):
        raise PortablePredictorContractFreezeV1Error(
            f"Required input is not one exact regular Git blob: {relative_path}"
        )
    raw = _run_git(
        project_root,
        "show",
        f"{commit}:{relative_path}",
        binary=True,
    )
    assert isinstance(raw, bytes)
    return raw, parts[2], parts[0]


def _json_from_bytes(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PortablePredictorContractFreezeV1Error(
            f"Cannot parse authenticated JSON: {label}"
        ) from exc
    payload = _require_mapping(payload, label=label)
    recorded = payload.get("commit_sha256")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or canonical_sha256(body) != recorded:
        raise PortablePredictorContractFreezeV1Error(
            f"Authenticated JSON internal commit is invalid: {label}"
        )
    return payload


def _read_current_json(path: Path) -> tuple[dict[str, Any], bytes]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = path.read_bytes()
    after = path.read_bytes()
    if before != after:
        raise PortablePredictorContractFreezeV1Error(
            f"Authenticated input changed while read: {path}"
        )
    return _json_from_bytes(before, label=str(path)), before


def _read_config(config_path: Path) -> tuple[dict[str, Any], bytes]:
    raw = config_path.read_bytes()
    if _sha256_bytes(raw) != CONFIG_SHA256:
        raise PortablePredictorContractFreezeV1Error(
            "The predictor-contract decision config changed."
        )
    try:
        payload = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PortablePredictorContractFreezeV1Error(
            "Cannot parse the predictor-contract decision config."
        ) from exc
    return payload, raw


def expected_plan_authorization_scope() -> dict[str, Any]:
    """Return the exact contract-audit scope that v8 must bind."""

    return {
        "decision_id": "portable_predictor_contract_freeze_v1",
        "experiment_id": "la_to_three_city_zero_shot_v1",
        "experiment_semantic_sha256": (
            "6a52eb39486dc9e992927e5506b4071aaba6ec31dd910eeb0f9bcc51dea1736c"
        ),
        "decision_config_path": CONFIG_PATH,
        "decision_config_sha256": CONFIG_SHA256,
        "decision_runtime_paths": list(CODE_PATHS),
        "tracked_read_set": {
            "water_distance_v2": {
                "path": V2_TERMINAL_PATH,
                "bytes": V2_BYTES,
                "file_sha256": V2_FILE_SHA256,
                "commit_sha256": V2_INTERNAL_COMMIT_SHA256,
                "publication_git_commit": V2_PUBLICATION_COMMIT,
            },
            "phoenix_source_footprint": {
                "path": PHOENIX_SOURCE_PATH,
                "bytes": PHOENIX_BYTES,
                "file_sha256": PHOENIX_FILE_SHA256,
                "commit_sha256": PHOENIX_INTERNAL_COMMIT_SHA256,
                "publication_git_commit": PHOENIX_PUBLICATION_COMMIT,
            },
        },
        "required_absent_paths": list(ABSENT_SOURCE_PATHS),
        "decision_output_path": OUTPUT_PATH,
        "append_only_output": True,
        "network_or_download_allowed": False,
        "source_archive_payload_or_geometry_read_allowed": False,
        "eligible_land_or_predictor_value_read_allowed": False,
        "predictor_construction_allowed": False,
        "model_target_or_result_read_allowed": False,
        "protocol_promotion_allowed": False,
    }


def _validate_config(config: Mapping[str, Any]) -> None:
    decision = _require_mapping(config.get("decision"), label="decision config")
    expected_decision = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "decision_id": "portable_predictor_contract_freeze_v1",
        "decision_date": "2026-08-01",
        "scope": (
            "target-blind portable predictor source and calibration contract "
            "freeze decision"
        ),
        "state": STATE,
        "outcome": OUTCOME,
        "expected_experiment_id": "la_to_three_city_zero_shot_v1",
        "expected_experiment_semantic_sha256": (
            "6a52eb39486dc9e992927e5506b4071aaba6ec31dd910eeb0f9bcc51dea1736c"
        ),
    }
    if not _strict_equal(decision, expected_decision):
        raise PortablePredictorContractFreezeV1Error(
            "The predictor-contract decision identity changed."
        )
    planning = _require_mapping(
        config.get("planning_authorization"),
        label="planning authorization",
    )
    if not _strict_equal(
        planning,
        {
            "path": PLAN_PATH,
            "expected_schema_version": 8,
            "expected_algorithm_version": "multicity-planning-readiness-v8",
            "expected_state": "planning_ready",
            "expected_planning_stage": (
                "portable_water_distance_source_and_algorithm_frozen_"
                "predictor_contract_freeze_authorized"
            ),
            "expected_next_safe_stage": (
                "freeze_exact_portable_predictor_source_and_calibration_contract"
            ),
            "required_authorization": (
                "portable_predictor_source_and_calibration_contract_freeze"
            ),
        },
    ):
        raise PortablePredictorContractFreezeV1Error(
            "The planning-authorization contract changed."
        )
    prerequisites = _require_mapping(
        config.get("prerequisites"),
        label="prerequisites",
    )
    expected_prerequisites = {
        "water_distance_v2": {
            "path": V2_TERMINAL_PATH,
            "expected_bytes": V2_BYTES,
            "expected_file_sha256": V2_FILE_SHA256,
            "expected_commit_sha256": V2_INTERNAL_COMMIT_SHA256,
            "expected_state": (
                "decision_complete_source_and_algorithm_frozen_predictor_closed"
            ),
            "expected_outcome": (
                "freeze_gshhg_2_3_7_l1_l2_l3_source_and_point_distance_algorithm"
            ),
            "publication_git_commit": V2_PUBLICATION_COMMIT,
        },
        "phoenix_source_footprint": {
            "path": PHOENIX_SOURCE_PATH,
            "expected_bytes": PHOENIX_BYTES,
            "expected_file_sha256": PHOENIX_FILE_SHA256,
            "expected_commit_sha256": PHOENIX_INTERNAL_COMMIT_SHA256,
            "expected_state": "complete_metadata_only_source_not_protocol_locked",
            "expected_source_lock_status": "pilot_snapshot_not_protocol_lock",
            "publication_git_commit": PHOENIX_PUBLICATION_COMMIT,
        },
    }
    if not _strict_equal(prerequisites, expected_prerequisites):
        raise PortablePredictorContractFreezeV1Error(
            "The exact tracked prerequisite set changed."
        )
    expected_absent = _require_mapping(
        config.get("expected_absent_source_footprints"),
        label="expected-absent source footprints",
    )
    if not _strict_equal(
        expected_absent,
        {
            "city_ids": ["houston_tx", "chicago_il"],
            "paths": list(ABSENT_SOURCE_PATHS),
            "required_status": "never_tracked_at_v8_publication",
        },
    ):
        raise PortablePredictorContractFreezeV1Error(
            "The expected-absent source-footprint contract changed."
        )
    required_contract = _require_mapping(
        config.get("required_contract"),
        label="required predictor contract",
    )
    if not _strict_equal(
        required_contract,
        {
            "families": [
                "nationwide_land_cover_and_imperviousness",
                "elevation_and_slope",
                "portable_water_distance_aggregation_and_feature_names",
                "calendar_harmonics",
                "lagged_daymet",
                "lagged_nonthermal_sentinel_2",
            ],
            "must_freeze": [
                "source_identity_and_version",
                "calibration_and_units",
                "timing_and_lag_windows",
                "spatial_support_and_aggregation",
                "feature_names_and_missingness_rules",
            ],
            "water_distance_source_and_point_algorithm_must_remain_exact": True,
        },
    ):
        raise PortablePredictorContractFreezeV1Error(
            "The required predictor-contract families changed."
        )
    defer = _require_mapping(config.get("defer_rules"), label="defer rules")
    if not _strict_equal(
        defer,
        {
            "required_blockers": list(EXPECTED_BLOCKERS),
            "phoenix_required_nlcd_family_key": (
                "nlcd_land_cover_and_imperviousness"
            ),
            "phoenix_required_terrain_family_key": "terrain_windows",
            "phoenix_terrain_content_sha256_must_be_frozen": True,
            "phoenix_terrain_raster_schema_must_be_verified": True,
            "all_required_blockers_must_be_observed": True,
            "freeze_when_a_required_blocker_is_observed": False,
        },
    ):
        raise PortablePredictorContractFreezeV1Error(
            "The exact deferred blocker set changed."
        )
    locks = _require_mapping(config.get("locks"), label="locks")
    if not _strict_equal(
        locks,
        {
            "portable_water_distance_source_locked": True,
            "portable_water_distance_algorithm_locked": True,
            "portable_predictor_source_and_calibration_contract_locked": False,
            "portable_water_distance_feature_names_frozen": False,
            "predictor_build_authorized": False,
            "protocol_lock_created": False,
            "external_targets_unlocked": False,
            "external_target_values_read": False,
            "external_prediction_commit_exists": False,
        },
    ):
        raise PortablePredictorContractFreezeV1Error(
            "The deferred-decision lock boundary changed."
        )
    output = _require_mapping(config.get("outputs"), label="outputs")
    if not _strict_equal(
        output,
        {"manifest": OUTPUT_PATH, "append_only": True, "overwrite_allowed": False},
    ):
        raise PortablePredictorContractFreezeV1Error(
            "The append-only output contract changed."
        )
    access = _require_mapping(config.get("access_contract"), label="access")
    expected_access = {
        "decision_program_network_requests": 0,
        "planning_manifest_read": True,
        "tracked_decision_config_bytes_read": True,
        "tracked_code_file_bytes_hashed": True,
        "tracked_json_manifest_bytes_read": True,
        "historical_git_blob_bytes_read": True,
        "local_git_metadata_read": True,
        "untracked_path_names_checked_by_git_status": True,
        "untracked_file_contents_opened": False,
        "ignored_path_names_requested_from_git": False,
        "source_archive_or_payload_opened": False,
        "geometry_opened": False,
        "eligible_land_grid_opened": False,
        "raster_or_vector_values_opened": False,
        "distance_feature_surface_opened_or_computed": False,
        "predictor_values_opened_or_computed": False,
        "predictor_construction_performed": False,
        "model_fit_performed": False,
        "model_predictions_computed": False,
        "landsat_thermal_values_read": False,
        "landsat_target_qa_values_read": False,
        "external_lst_values_read": False,
        "external_target_files_opened": False,
        "final_evaluation_outputs_opened": False,
    }
    if not _strict_equal(access, expected_access):
        raise PortablePredictorContractFreezeV1Error(
            "The tracked-only access contract changed."
        )
    next_gate = _require_mapping(config.get("next_gate"), label="next gate")
    if not _strict_equal(
        next_gate,
        {
            "stage_id": NEXT_SAFE_STAGE,
            "v1_terminal_is_append_only_and_may_not_be_overwritten": True,
            "houston_and_chicago_metadata_only_source_footprints_required": True,
            (
                "phoenix_nlcd_source_identity_version_calibration_units_and_"
                "support_required"
            ): True,
            (
                "phoenix_terrain_content_hash_schema_calibration_units_and_"
                "support_required"
            ): True,
            "new_network_or_download_requires_exact_preregistration": True,
            "separate_v2_contract_decision_required": True,
            (
                "separate_tracked_only_transition_required_before_predictor_"
                "construction"
            ): True,
            "portable_predictor_contract_remains_unfrozen": True,
            "predictor_construction_remains_closed": True,
            (
                "model_target_protocol_and_operational_claim_authorization_"
                "remain_closed"
            ): True,
        },
    ):
        raise PortablePredictorContractFreezeV1Error(
            "The post-decision next gate changed."
        )


def _validate_v2(payload: Mapping[str, Any], raw: bytes) -> None:
    if len(raw) != V2_BYTES or _sha256_bytes(raw) != V2_FILE_SHA256:
        raise PortablePredictorContractFreezeV1Error(
            "The exact water-distance V2 terminal changed."
        )
    expected = {
        "state": (
            "decision_complete_source_and_algorithm_frozen_predictor_closed"
        ),
        "outcome": (
            "freeze_gshhg_2_3_7_l1_l2_l3_source_and_point_distance_algorithm"
        ),
        "commit_sha256": V2_INTERNAL_COMMIT_SHA256,
    }
    for key, value in expected.items():
        if not _strict_equal(payload.get(key), value):
            raise PortablePredictorContractFreezeV1Error(
                f"The water-distance terminal field changed: {key}"
            )
    locks = _require_mapping(payload.get("locks"), label="water-distance locks")
    if (
        locks.get("source_lock_created") is not True
        or locks.get("algorithm_lock_created") is not True
        or locks.get("feature_names_frozen") is not False
        or locks.get("predictor_build_authorized") is not False
    ):
        raise PortablePredictorContractFreezeV1Error(
            "The water-distance terminal lock boundary changed."
        )


def _validate_phoenix(payload: Mapping[str, Any], raw: bytes) -> tuple[str, ...]:
    if len(raw) != PHOENIX_BYTES or _sha256_bytes(raw) != PHOENIX_FILE_SHA256:
        raise PortablePredictorContractFreezeV1Error(
            "The exact Phoenix source-footprint manifest changed."
        )
    expected = {
        "state": "complete_metadata_only_source_not_protocol_locked",
        "source_lock_status": "pilot_snapshot_not_protocol_lock",
        "commit_sha256": PHOENIX_INTERNAL_COMMIT_SHA256,
    }
    for key, value in expected.items():
        if not _strict_equal(payload.get(key), value):
            raise PortablePredictorContractFreezeV1Error(
                f"The Phoenix source-footprint field changed: {key}"
            )
    families = _require_mapping(payload.get("source_families"), label="families")
    blockers: list[str] = []
    if "nlcd_land_cover_and_imperviousness" not in families:
        blockers.append("phoenix_nlcd_source_family_absent")
    terrain = _require_mapping(families.get("terrain_windows"), label="terrain")
    if (
        terrain.get("content_sha256_frozen") is not True
        or terrain.get("raster_schema_verified") is not True
    ):
        blockers.append("phoenix_terrain_content_and_schema_unfrozen")
    return tuple(blockers)


def _path_never_tracked(project_root: Path, *, path: str, head: str) -> bool:
    history = _run_git(
        project_root,
        "log",
        head,
        "--format=%H",
        "--",
        path,
    )
    tree = _run_git(project_root, "ls-tree", head, "--", path)
    assert isinstance(history, str)
    assert isinstance(tree, str)
    return not history.strip() and not tree.strip()


def _is_ancestor(project_root: Path, ancestor: str, descendant: str) -> bool:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


def _require_exact_commit_delta(
    project_root: Path,
    *,
    parent: str,
    commit: str,
) -> None:
    raw = _run_git(
        project_root,
        "diff-tree",
        "--no-commit-id",
        "--name-status",
        "-r",
        "-z",
        "--no-renames",
        parent,
        commit,
        binary=True,
    )
    assert isinstance(raw, bytes)
    fields = raw.split(b"\0")
    if fields[-1:] != [b""]:
        raise PortablePredictorContractFreezeV1Error(
            "The V1 terminal delta is not valid NUL-delimited Git output."
        )
    fields = fields[:-1]
    try:
        decoded = tuple(field.decode("utf-8") for field in fields)
    except UnicodeDecodeError as exc:
        raise PortablePredictorContractFreezeV1Error(
            "The V1 terminal delta contains a non-UTF-8 path."
        ) from exc
    if decoded != ("A", OUTPUT_PATH):
        raise PortablePredictorContractFreezeV1Error(
            "The V1 publication commit must add only its canonical terminal."
        )


def _authenticate_phoenix_history(
    project_root: Path,
    *,
    expected_raw: bytes,
    valid_through_commit: str,
) -> None:
    if not _is_ancestor(
        project_root,
        PHOENIX_PUBLICATION_COMMIT,
        valid_through_commit,
    ):
        raise PortablePredictorContractFreezeV1Error(
            "The Phoenix source-footprint publication is not in v8 history."
        )
    additions = _run_git(
        project_root,
        "log",
        "--all",
        "--diff-filter=A",
        "--format=%H",
        "--",
        PHOENIX_SOURCE_PATH,
    )
    assert isinstance(additions, str)
    if [line for line in additions.splitlines() if line] != [
        PHOENIX_PUBLICATION_COMMIT
    ]:
        raise PortablePredictorContractFreezeV1Error(
            "The Phoenix source-footprint manifest lacks one unique publication."
        )
    later = _run_git(
        project_root,
        "log",
        "--format=%H",
        f"{PHOENIX_PUBLICATION_COMMIT}..{valid_through_commit}",
        "--",
        PHOENIX_SOURCE_PATH,
    )
    assert isinstance(later, str)
    if later.strip():
        raise PortablePredictorContractFreezeV1Error(
            "The Phoenix source-footprint manifest changed before planning v8."
        )
    published, _, _ = _git_regular_blob(
        project_root,
        commit=PHOENIX_PUBLICATION_COMMIT,
        relative_path=PHOENIX_SOURCE_PATH,
    )
    if published != expected_raw:
        raise PortablePredictorContractFreezeV1Error(
            "The Phoenix source-footprint publication bytes changed."
        )


def _git_preflight(
    project_root: Path,
    *,
    required_paths: tuple[str, ...],
    expected_head: str | None = None,
) -> str:
    branch = _run_git(project_root, "branch", "--show-current")
    head = _run_git(project_root, "rev-parse", "HEAD")
    origin = _run_git(project_root, "rev-parse", "origin/main")
    status = _run_git(
        project_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    assert isinstance(branch, str)
    assert isinstance(head, str)
    assert isinstance(origin, str)
    assert isinstance(status, str)
    head = head.strip()
    if branch.strip() != "main" or head != origin.strip() or status:
        raise PortablePredictorContractFreezeV1Error(
            "The contract decision requires clean synchronized main."
        )
    if expected_head is not None and head != expected_head:
        raise PortablePredictorContractFreezeV1Error(
            "HEAD changed between contract decision gates."
        )
    for path in required_paths:
        _, oid, _ = _git_regular_blob(
            project_root,
            commit=head,
            relative_path=path,
        )
        worktree_oid = _run_git(
            project_root,
            "hash-object",
            f"--path={path}",
            "--",
            path,
        )
        assert isinstance(worktree_oid, str)
        if worktree_oid.strip() != oid:
            raise PortablePredictorContractFreezeV1Error(
                f"A required input differs from HEAD: {path}"
            )
    return head


def _code_records_at_commit(
    project_root: Path,
    *,
    commit: str,
) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for path in CODE_PATHS:
        raw, oid, mode = _git_regular_blob(
            project_root,
            commit=commit,
            relative_path=path,
        )
        records[path] = {
            "sha256": _sha256_bytes(raw),
            "bytes": len(raw),
            "git_blob_oid": oid,
            "git_mode": mode,
        }
    return records


def _authenticate_v8_plan(
    project_root: Path,
    *,
    head: str,
) -> tuple[dict[str, Any], bytes, str]:
    from la_heat.multicity import plan_predictor_contract_transition_v8 as plan_v8

    plan, raw = _read_current_json(project_root / PLAN_PATH)
    if plan.get("schema_version") != 8:
        raise PortablePredictorContractFreezeV1Error(
            "Canonical planning is not yet v8."
        )
    publication = plan_v8._locate_v8_publication_commit(
        project_root,
        plan,
        current_head=head,
    )
    try:
        plan_v8.authenticate_historical_v8_payload(
            project_root,
            plan,
            publication_commit=publication,
            current_head=head,
        )
    except Exception as exc:
        raise PortablePredictorContractFreezeV1Error(
            "Canonical planning v8 failed authentication."
        ) from exc
    authorization = _require_mapping(
        plan.get("authorized_now"),
        label="v8 authorization",
    )
    locks = _require_mapping(plan.get("locks"), label="v8 locks")
    if (
        authorization.get(
            "portable_predictor_source_and_calibration_contract_freeze"
        )
        is not True
        or authorization.get("predictor_construction") is not False
        or locks.get("portable_water_distance_source_locked") is not True
        or locks.get("portable_water_distance_algorithm_locked") is not True
        or locks.get("portable_water_distance_feature_names_frozen") is not False
        or locks.get("predictor_build_authorized") is not False
    ):
        raise PortablePredictorContractFreezeV1Error(
            "Planning v8 does not expose the exact narrow contract permission."
        )
    if not _strict_equal(
        plan.get("predictor_contract_freeze_authorization_scope"),
        expected_plan_authorization_scope(),
    ):
        raise PortablePredictorContractFreezeV1Error(
            "Planning v8 did not bind the exact contract-audit scope."
        )
    return plan, raw, publication


def _build_payload(
    *,
    project_root: Path,
    config: Mapping[str, Any],
    config_bytes: int,
    plan: Mapping[str, Any],
    plan_raw: bytes,
    plan_publication: str,
    v2: Mapping[str, Any],
    phoenix: Mapping[str, Any],
    blockers: tuple[str, ...],
    precondition_head: str,
    code_files: Mapping[str, Any],
) -> dict[str, Any]:
    if blockers != EXPECTED_BLOCKERS:
        raise PortablePredictorContractFreezeV1Error(
            "The observed blockers differ from the exact deferred decision."
        )
    decision = _require_mapping(config.get("decision"), label="decision")
    access = _require_mapping(config.get("access_contract"), label="access")
    locks = _require_mapping(config.get("locks"), label="locks")
    next_gate = _require_mapping(config.get("next_gate"), label="next gate")
    runtime_sha, runtime = code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=CODE_PATHS,
        algorithm_version=ALGORITHM_VERSION,
    )
    runtime["relative_paths"] = list(CODE_PATHS)
    runtime["sha256"] = runtime_sha
    runtime["git_files"] = deepcopy(dict(code_files))
    payload: dict[str, Any] = {
        "schema_version": decision["schema_version"],
        "algorithm_version": ALGORITHM_VERSION,
        "state": STATE,
        "decision_id": decision["decision_id"],
        "decision_date": decision["decision_date"],
        "decision_scope": decision["scope"],
        "outcome": OUTCOME,
        "experiment_id": decision["expected_experiment_id"],
        "experiment_semantic_sha256": decision[
            "expected_experiment_semantic_sha256"
        ],
        "repository": {
            "branch": "main",
            "head": precondition_head,
            "origin_main": precondition_head,
            "head_equals_origin_main": True,
            "working_tree_clean_before_decision": True,
        },
        "planning_authorization": {
            "path": PLAN_PATH,
            "bytes": len(plan_raw),
            "file_sha256": _sha256_bytes(plan_raw),
            "commit_sha256": plan["commit_sha256"],
            "publication_git_commit": plan_publication,
            "state": plan["state"],
            "planning_stage": plan["planning_stage"],
            "authorized_now": deepcopy(plan["authorized_now"]),
            "locks": deepcopy(plan["locks"]),
        },
        "prerequisites": {
            "water_distance_v2": {
                "path": V2_TERMINAL_PATH,
                "bytes": V2_BYTES,
                "file_sha256": V2_FILE_SHA256,
                "commit_sha256": v2["commit_sha256"],
                "publication_git_commit": V2_PUBLICATION_COMMIT,
                "state": v2["state"],
                "outcome": v2["outcome"],
            },
            "phoenix_source_footprint": {
                "path": PHOENIX_SOURCE_PATH,
                "bytes": PHOENIX_BYTES,
                "file_sha256": PHOENIX_FILE_SHA256,
                "commit_sha256": phoenix["commit_sha256"],
                "publication_git_commit": PHOENIX_PUBLICATION_COMMIT,
                "state": phoenix["state"],
                "source_lock_status": phoenix["source_lock_status"],
            },
        },
        "evidence_gaps": {
            "observed_blockers": list(blockers),
            "houston_source_footprint_manifest_present": False,
            "chicago_source_footprint_manifest_present": False,
            "phoenix_nlcd_source_family_present": False,
            "phoenix_terrain_content_sha256_frozen": False,
            "phoenix_terrain_raster_schema_verified": False,
        },
        "decision": {
            "contract_freeze_passed": False,
            "portable_predictor_contract_locked": False,
            "feature_names_frozen": False,
            "predictor_build_authorized": False,
            "protocol_lock_created": False,
            "external_targets_unlocked": False,
            "next_safe_stage": NEXT_SAFE_STAGE,
        },
        "locks": deepcopy(locks),
        "access_contract": deepcopy(access),
        "next_gate": deepcopy(next_gate),
        "decision_config": {
            "path": CONFIG_PATH,
            "bytes": config_bytes,
            "file_sha256": CONFIG_SHA256,
        },
        "code_runtime": runtime,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def _expected_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(payload), indent=2).encode("utf-8")


def _authenticate_terminal_history(
    project_root: Path,
    *,
    payload_raw: bytes,
    plan_publication: str,
    current_head: str,
) -> None:
    additions = _run_git(
        project_root,
        "log",
        "--all",
        "--diff-filter=A",
        "--format=%H",
        "--",
        OUTPUT_PATH,
    )
    assert isinstance(additions, str)
    commits = [line for line in additions.splitlines() if line]
    if len(commits) != 1:
        raise PortablePredictorContractFreezeV1Error(
            "The V1 contract terminal must have one unique publication."
        )
    publication = commits[0]
    ancestry = _run_git(
        project_root,
        "rev-list",
        "--parents",
        "-n",
        "1",
        publication,
    )
    assert isinstance(ancestry, str)
    if ancestry.split() != [publication, plan_publication]:
        raise PortablePredictorContractFreezeV1Error(
            "The V1 contract terminal is not the direct child of planning v8."
        )
    _require_exact_commit_delta(
        project_root,
        parent=plan_publication,
        commit=publication,
    )
    if not _is_ancestor(project_root, publication, current_head):
        raise PortablePredictorContractFreezeV1Error(
            "The V1 contract terminal is not an ancestor of current HEAD."
        )
    published, _, _ = _git_regular_blob(
        project_root,
        commit=publication,
        relative_path=OUTPUT_PATH,
    )
    if published != payload_raw:
        raise PortablePredictorContractFreezeV1Error(
            "The V1 terminal publication bytes changed."
        )
    later = _run_git(
        project_root,
        "log",
        "--format=%H",
        f"{publication}..{current_head}",
        "--",
        OUTPUT_PATH,
    )
    assert isinstance(later, str)
    if later.strip():
        raise PortablePredictorContractFreezeV1Error(
            "The append-only V1 terminal changed after publication."
        )


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _require_terminal_generation_precondition(
    *,
    head: str,
    plan_publication: str,
    output_exists: bool,
    write: bool,
) -> None:
    if not output_exists and write and head != plan_publication:
        raise PortablePredictorContractFreezeV1Error(
            "A new V1 terminal may be generated only while HEAD is the exact "
            "planning-v8 publication commit."
        )


def audit_portable_predictor_contract_freeze_v1(
    *,
    project_root: str | Path | None = None,
    config_path: str | Path = CONFIG_PATH,
    output_path: str | Path = OUTPUT_PATH,
    write: bool = True,
) -> dict[str, Any]:
    """Create or authenticate the append-only deferred V1 decision."""

    root = (
        _default_project_root()
        if project_root is None
        else Path(project_root).resolve()
    )
    config_file = Path(config_path)
    if not config_file.is_absolute():
        config_file = root / config_file
    destination = Path(output_path)
    if not destination.is_absolute():
        destination = root / destination
    if config_file.resolve() != (root / CONFIG_PATH).resolve():
        raise PortablePredictorContractFreezeV1Error(
            "The contract decision requires the canonical config path."
        )
    if destination.resolve() != (root / OUTPUT_PATH).resolve():
        raise PortablePredictorContractFreezeV1Error(
            "The contract decision may write only its canonical terminal."
        )
    if destination.is_symlink() or (
        destination.exists() and not destination.is_file()
    ):
        raise PortablePredictorContractFreezeV1Error(
            "The canonical V1 terminal path must be absent or a regular file."
        )
    output_exists = destination.is_file()
    config, config_raw = _read_config(config_file)
    _validate_config(config)
    required = tuple(
        dict.fromkeys(
            (
                PLAN_PATH,
                V2_TERMINAL_PATH,
                PHOENIX_SOURCE_PATH,
                *CODE_PATHS,
                *((OUTPUT_PATH,) if output_exists else ()),
            )
        )
    )
    head = _git_preflight(root, required_paths=required)
    plan, plan_raw, plan_publication = _authenticate_v8_plan(root, head=head)
    _require_terminal_generation_precondition(
        head=head,
        plan_publication=plan_publication,
        output_exists=output_exists,
        write=write,
    )
    v2, v2_raw = _read_current_json(root / V2_TERMINAL_PATH)
    _validate_v2(v2, v2_raw)
    phoenix_raw, _, _ = _git_regular_blob(
        root,
        commit=PHOENIX_PUBLICATION_COMMIT,
        relative_path=PHOENIX_SOURCE_PATH,
    )
    phoenix = _json_from_bytes(
        phoenix_raw,
        label=f"{PHOENIX_PUBLICATION_COMMIT}:{PHOENIX_SOURCE_PATH}",
    )
    phoenix_blockers = _validate_phoenix(phoenix, phoenix_raw)
    _authenticate_phoenix_history(
        root,
        expected_raw=phoenix_raw,
        valid_through_commit=plan_publication,
    )
    absent_blockers: list[str] = []
    absent_names = ("houston", "chicago")
    for name, path in zip(absent_names, ABSENT_SOURCE_PATHS, strict=True):
        if not _path_never_tracked(root, path=path, head=plan_publication):
            raise PortablePredictorContractFreezeV1Error(
                f"The expected-absent source manifest now exists: {path}"
            )
        absent_blockers.append(f"{name}_source_footprint_manifest_absent")
    blockers = tuple((*absent_blockers, *phoenix_blockers))
    code_files = _code_records_at_commit(root, commit=head)
    plan_code = _require_mapping(plan.get("code_files"), label="v8 code files")
    if not _strict_equal(plan_code, code_files):
        raise PortablePredictorContractFreezeV1Error(
            "The contract runtime differs from the exact code bound by v8."
        )
    payload = _build_payload(
        project_root=root,
        config=config,
        config_bytes=len(config_raw),
        plan=plan,
        plan_raw=plan_raw,
        plan_publication=plan_publication,
        v2=v2,
        phoenix=phoenix,
        blockers=blockers,
        precondition_head=plan_publication,
        code_files=code_files,
    )
    expected = _expected_json_bytes(payload)
    _git_preflight(root, required_paths=required, expected_head=head)

    if output_exists:
        observed, observed_raw = _read_current_json(destination)
        if observed_raw != expected or not _strict_equal(observed, payload):
            raise PortablePredictorContractFreezeV1Error(
                "The existing V1 terminal differs from the reconstructed payload."
            )
        _authenticate_terminal_history(
            root,
            payload_raw=observed_raw,
            plan_publication=plan_publication,
            current_head=head,
        )
        return observed
    if not write:
        raise FileNotFoundError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    temporary.write_bytes(expected)
    temporary.replace(destination)
    return payload
