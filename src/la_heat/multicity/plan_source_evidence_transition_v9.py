"""Authorize the exact missing portable-predictor source-evidence stage.

This transition consumes the canonical planning-v8 record and the append-only
V1 deferred predictor-contract decision.  It reads tracked JSON/config/code
blobs and local Git metadata only.  It closes both prior staging grants and
opens exactly one target-blind source-evidence grant; all scientific locks and
all predictor/model/target/result permissions remain unchanged.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import subprocess
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, Final

from la_heat.multicity.plan_predictor_contract_transition_v8 import (
    AUTHORIZED_NOW as V8_AUTHORIZED_NOW,
)
from la_heat.multicity.plan_predictor_contract_transition_v8 import (
    LOCKS,
    authenticate_historical_v8_payload,
)
from la_heat.multicity.portable_predictor_contract_freeze_v1 import (
    EXPECTED_BLOCKERS as V1_EXPECTED_BLOCKERS,
)

SCHEMA_VERSION: Final = 9
ALGORITHM_VERSION: Final = "multicity-planning-readiness-v9"
PLANNING_STAGE: Final = (
    "portable_predictor_contract_v1_deferred_missing_source_evidence_stage_authorized"
)
NEXT_SAFE_STAGE: Final = "stage_missing_portable_predictor_source_evidence_v1"
EXPERIMENT_ID: Final = "la_to_three_city_zero_shot_v1"
EXPERIMENT_SEMANTIC_SHA256: Final = (
    "6a52eb39486dc9e992927e5506b4071aaba6ec31dd910eeb0f9bcc51dea1736c"
)

PLAN_PATH: Final = "manifests/multicity/PLAN_READINESS.json"
V8_PUBLICATION_COMMIT: Final = "35b6015a3a9a410b42752d2e50a7599e18bf2563"
V8_FILE_SHA256: Final = "8ad87ecdfd7d6e232d574662187dc91977bef0c177fd673cb96305469f44d948"
V8_INTERNAL_COMMIT_SHA256: Final = (
    "d2a3d95bc4935b3aa1c46861abd2420d67959db0de18c3689b6d5994e64800dd"
)
V8_BYTES: Final = 27_837

V1_TERMINAL_PATH: Final = (
    "manifests/multicity/reviews/portable_predictor_contract/"
    "PORTABLE_PREDICTOR_CONTRACT_FREEZE_V1.json"
)
V1_PUBLICATION_COMMIT: Final = "47a626f6fc0a6577148cc731bb00d21f5387f20a"
V1_IMPLEMENTATION_COMMIT: Final = "622b03cadbc94af0ecf667ce4602913b36fb0d74"
V1_FILE_SHA256: Final = "794e85c2ea5ad76b84c5e6e7be0999bc5939ab85d9dc7df773406f9802fe6127"
V1_INTERNAL_COMMIT_SHA256: Final = (
    "75b368d7f71c7af5af10317f996595f629e4dacdbbf62b3dc79c3ac0c5eb3e3d"
)
V1_BYTES: Final = 12_934
V1_STATE: Final = "decision_complete_contract_freeze_deferred_predictor_closed"
V1_OUTCOME: Final = "defer_until_cross_city_source_footprints_and_static_source_contracts_complete"

V9_MODULE_PATH: Final = "src/la_heat/multicity/plan_source_evidence_transition_v9.py"
V9_SCRIPT_PATH: Final = "scripts/authorize_multicity_source_evidence_stage.py"
SOURCE_EVIDENCE_CODE_PATHS: Final = (
    "configs/multicity/portable_predictor_source_evidence_v1.toml",
    "configs/multicity/experiment.toml",
    "configs/multicity/cities/phoenix_az.toml",
    "configs/multicity/cities/houston_tx.toml",
    "configs/multicity/cities/chicago_il.toml",
    "scripts/stage_multicity_portable_predictor_source_evidence_v1.py",
    "src/la_heat/daymet_grid.py",
    "src/la_heat/multicity/config.py",
    "src/la_heat/multicity/geography.py",
    "src/la_heat/multicity/portable_predictor_source_evidence_v1.py",
    "src/la_heat/multicity/source_footprints.py",
    "src/la_heat/multicity/workspace.py",
    "src/la_heat/provenance.py",
    "src/la_heat/static_sources.py",
)

AUTHORIZED_NOW: Final = {
    **V8_AUTHORIZED_NOW,
    "boundary_and_public_metadata_staging": False,
    "portable_predictor_source_and_calibration_contract_freeze": False,
    "portable_predictor_missing_source_evidence_staging": True,
}
BLOCKERS_BEFORE_PREDICTOR_BUILD: Final = (
    "complete_missing_portable_predictor_source_evidence_stage_v1",
    "freeze_exact_portable_predictor_source_and_calibration_contract_v2",
    "promote_protocol_from_draft_with_separate_lock",
)
EXPECTED_TRANSITION_DIFF_PATHS: Final = (
    "authorized_now.boundary_and_public_metadata_staging",
    "authorized_now.portable_predictor_missing_source_evidence_staging",
    "authorized_now.portable_predictor_source_and_calibration_contract_freeze",
)

TRANSITION_ACCESS_CONTRACT: Final = {
    "network_requests": 0,
    "tracked_configuration_files_read": True,
    "tracked_code_files_hashed": True,
    "tracked_json_manifests_read": True,
    "local_git_metadata_and_historical_blobs_read": True,
    "untracked_path_names_checked_by_git_status": True,
    "untracked_file_contents_opened": False,
    "ignored_path_names_requested_from_git": False,
    "source_payload_or_geometry_opened": False,
    "eligible_land_grid_opened": False,
    "predictor_values_opened_or_computed": False,
    "predictor_construction_performed": False,
    "model_fit_performed": False,
    "model_predictions_computed": False,
    "landsat_thermal_values_read": False,
    "landsat_target_qa_values_read": False,
    "external_target_files_opened": False,
    "final_evaluation_outputs_opened": False,
}


class MulticityPlanSourceEvidenceTransitionV9Error(ValueError):
    """Raised when the tracked-only v9 transition cannot authenticate."""


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
        raise MulticityPlanSourceEvidenceTransitionV9Error(f"{label} must be an object.")
    return value


def _json_from_bytes(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            f"Cannot parse authenticated JSON: {label}"
        ) from exc
    payload = _require_mapping(payload, label=label)
    recorded = payload.get("commit_sha256")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or _canonical_sha256(body) != recorded:
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            f"Authenticated JSON internal commit is invalid: {label}"
        )
    return payload


def _run_git(
    project_root: Path,
    *arguments: str,
    binary: bool = False,
) -> str | bytes:
    completed = subprocess.run(
        ["git", "-C", str(project_root), *arguments],
        check=False,
        capture_output=True,
        text=not binary,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace") if binary else completed.stderr
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            f"Git authentication failed for {' '.join(arguments)}: {stderr.strip()}"
        )
    return completed.stdout


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
    )
    return completed.returncode == 0


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
        raise MulticityPlanSourceEvidenceTransitionV9Error(
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


def _historical_json(
    project_root: Path,
    *,
    commit: str,
    relative_path: str,
) -> tuple[dict[str, Any], bytes]:
    raw, _, _ = _git_regular_blob(
        project_root,
        commit=commit,
        relative_path=relative_path,
    )
    return _json_from_bytes(raw, label=f"{commit}:{relative_path}"), raw


def _read_current_json(path: Path) -> tuple[dict[str, Any], bytes]:
    if not path.is_file() or path.is_symlink():
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            f"Required JSON is not one regular file: {path}"
        )
    before = path.read_bytes()
    after = path.read_bytes()
    if before != after:
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            f"Authenticated input changed while read: {path}"
        )
    return _json_from_bytes(before, label=str(path)), before


def _recursive_diff_paths(
    left: object,
    right: object,
    *,
    prefix: str = "",
) -> tuple[str, ...]:
    if type(left) is not type(right):
        return (prefix,)
    if isinstance(left, dict):
        paths: list[str] = []
        for key in sorted(set(left) | set(right)):  # type: ignore[arg-type]
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in left or key not in right:
                paths.append(path)
            else:
                paths.extend(_recursive_diff_paths(left[key], right[key], prefix=path))
        return tuple(paths)
    if isinstance(left, list):
        paths = []
        for index in range(max(len(left), len(right))):
            path = f"{prefix}[{index}]"
            if index >= len(left) or index >= len(right):
                paths.append(path)
            else:
                paths.extend(_recursive_diff_paths(left[index], right[index], prefix=path))
        return tuple(paths)
    return () if left == right else (prefix,)


def _source_contract() -> tuple[tuple[str, ...], dict[str, Any], str, str]:
    module = importlib.import_module("la_heat.multicity.portable_predictor_source_evidence_v1")
    try:
        config_path = module.CONFIG_PATH
        output_path = getattr(module, "OUTPUT_PATH", module.TERMINAL_PATH)
        code_paths = tuple(module.CODE_PATHS)
        scope = module.expected_plan_authorization_scope()
    except (AttributeError, TypeError) as exc:
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The source-evidence runtime does not expose its exact contract."
        ) from exc
    expected_config = "configs/multicity/portable_predictor_source_evidence_v1.toml"
    expected_output = (
        "manifests/multicity/reviews/portable_predictor_contract/"
        "PORTABLE_PREDICTOR_SOURCE_EVIDENCE_V1.json"
    )
    if config_path != expected_config or output_path != expected_output:
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The source-evidence config or terminal path changed."
        )
    if code_paths != SOURCE_EVIDENCE_CODE_PATHS:
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The source-evidence runtime path set changed."
        )
    paths = (*SOURCE_EVIDENCE_CODE_PATHS, V9_MODULE_PATH, V9_SCRIPT_PATH)
    if any(not isinstance(path, str) or not path for path in paths):
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The source-evidence runtime path set is invalid."
        )
    return paths, deepcopy(scope), config_path, output_path


def transition_code_paths() -> tuple[str, ...]:
    """Return the exact code/config paths bound by the future source stage."""

    paths, _, _, _ = _source_contract()
    return paths


def _validate_v8(payload: Mapping[str, Any], raw: bytes) -> None:
    if len(raw) != V8_BYTES or _sha256_bytes(raw) != V8_FILE_SHA256:
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The canonical v8 predecessor bytes changed."
        )
    expected = {
        "schema_version": 8,
        "algorithm_version": "multicity-planning-readiness-v8",
        "state": "planning_ready",
        "planning_stage": (
            "portable_water_distance_source_and_algorithm_frozen_"
            "predictor_contract_freeze_authorized"
        ),
        "experiment_id": EXPERIMENT_ID,
        "config_semantic_sha256": EXPERIMENT_SEMANTIC_SHA256,
        "next_safe_stage": ("freeze_exact_portable_predictor_source_and_calibration_contract"),
        "commit_sha256": V8_INTERNAL_COMMIT_SHA256,
    }
    for key, value in expected.items():
        if not _strict_equal(payload.get(key), value):
            raise MulticityPlanSourceEvidenceTransitionV9Error(
                f"The canonical v8 predecessor field changed: {key}"
            )
    if not _strict_equal(payload.get("authorized_now"), V8_AUTHORIZED_NOW):
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The canonical v8 authorization boundary changed."
        )
    if not _strict_equal(payload.get("locks"), LOCKS):
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The canonical v8 lock boundary changed."
        )


def _validate_v1_terminal(payload: Mapping[str, Any], raw: bytes) -> None:
    if len(raw) != V1_BYTES or _sha256_bytes(raw) != V1_FILE_SHA256:
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The canonical V1 terminal bytes changed."
        )
    expected = {
        "schema_version": 1,
        "algorithm_version": "portable-predictor-contract-freeze-v1",
        "state": V1_STATE,
        "outcome": V1_OUTCOME,
        "experiment_id": EXPERIMENT_ID,
        "experiment_semantic_sha256": EXPERIMENT_SEMANTIC_SHA256,
        "commit_sha256": V1_INTERNAL_COMMIT_SHA256,
    }
    for key, value in expected.items():
        if not _strict_equal(payload.get(key), value):
            raise MulticityPlanSourceEvidenceTransitionV9Error(
                f"The canonical V1 terminal field changed: {key}"
            )
    gaps = _require_mapping(payload.get("evidence_gaps"), label="V1 evidence gaps")
    if not _strict_equal(gaps.get("observed_blockers"), list(V1_EXPECTED_BLOCKERS)):
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The canonical V1 terminal blockers changed."
        )
    decision = _require_mapping(payload.get("decision"), label="V1 decision")
    if (
        decision.get("contract_freeze_passed") is not False
        or decision.get("predictor_build_authorized") is not False
        or decision.get("next_safe_stage")
        != "stage_missing_portable_predictor_source_evidence_before_v2_freeze"
    ):
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The V1 deferred decision boundary changed."
        )
    planning = _require_mapping(
        payload.get("planning_authorization"), label="V1 planning authorization"
    )
    required_planning = {
        "path": PLAN_PATH,
        "bytes": V8_BYTES,
        "file_sha256": V8_FILE_SHA256,
        "commit_sha256": V8_INTERNAL_COMMIT_SHA256,
        "publication_git_commit": V8_PUBLICATION_COMMIT,
    }
    for key, value in required_planning.items():
        if not _strict_equal(planning.get(key), value):
            raise MulticityPlanSourceEvidenceTransitionV9Error(
                f"The V1-to-v8 binding changed: {key}"
            )


def _require_exact_delta(
    project_root: Path,
    *,
    parent: str,
    commit: str,
    status: str,
    path: str,
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
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The publication delta is not valid NUL-delimited Git output."
        )
    try:
        decoded = tuple(field.decode("utf-8") for field in fields[:-1])
    except UnicodeDecodeError as exc:
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The publication delta contains a non-UTF-8 path."
        ) from exc
    if decoded != (status, path):
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The publication commit changed a path outside its exact allowlist."
        )


def _require_v1_history(
    project_root: Path,
    *,
    terminal_raw: bytes,
    current_head: str,
) -> None:
    ancestry = _run_git(project_root, "rev-list", "--parents", "-n", "1", V1_PUBLICATION_COMMIT)
    assert isinstance(ancestry, str)
    if ancestry.split() != [V1_PUBLICATION_COMMIT, V8_PUBLICATION_COMMIT]:
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The V1 terminal is not the direct child of canonical planning v8."
        )
    _require_exact_delta(
        project_root,
        parent=V8_PUBLICATION_COMMIT,
        commit=V1_PUBLICATION_COMMIT,
        status="A",
        path=V1_TERMINAL_PATH,
    )
    additions = _run_git(
        project_root,
        "log",
        "--all",
        "--diff-filter=A",
        "--format=%H",
        "--",
        V1_TERMINAL_PATH,
    )
    assert isinstance(additions, str)
    if [line for line in additions.splitlines() if line] != [V1_PUBLICATION_COMMIT]:
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The V1 terminal lacks one unique append-only publication."
        )
    if not _is_ancestor(project_root, V1_PUBLICATION_COMMIT, current_head):
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The V1 terminal is not an ancestor of current HEAD."
        )
    later = _run_git(
        project_root,
        "log",
        "--format=%H",
        f"{V1_PUBLICATION_COMMIT}..{current_head}",
        "--",
        V1_TERMINAL_PATH,
    )
    assert isinstance(later, str)
    if later.strip():
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The append-only V1 terminal changed after publication."
        )
    published, _, _ = _git_regular_blob(
        project_root,
        commit=V1_PUBLICATION_COMMIT,
        relative_path=V1_TERMINAL_PATH,
    )
    if published != terminal_raw:
        raise MulticityPlanSourceEvidenceTransitionV9Error("The V1 publication bytes changed.")


def _require_v1_runtime_history(
    project_root: Path,
    *,
    terminal: Mapping[str, Any],
    current_head: str,
) -> None:
    """Reject modification-and-restoration of any V1-bound runtime blob."""

    runtime = _require_mapping(terminal.get("code_runtime"), label="V1 runtime")
    git_files = _require_mapping(runtime.get("git_files"), label="V1 runtime Git files")
    relative_paths = runtime.get("relative_paths")
    if not isinstance(relative_paths, list) or not _strict_equal(relative_paths, list(git_files)):
        raise MulticityPlanSourceEvidenceTransitionV9Error("The V1 runtime path set changed.")
    for path, raw_record in git_files.items():
        if not isinstance(path, str):
            raise MulticityPlanSourceEvidenceTransitionV9Error("A V1 runtime path is not a string.")
        record = _require_mapping(raw_record, label=f"V1 runtime {path}")
        raw, oid, mode = _git_regular_blob(
            project_root,
            commit=V1_IMPLEMENTATION_COMMIT,
            relative_path=path,
        )
        expected = {
            "sha256": _sha256_bytes(raw),
            "bytes": len(raw),
            "git_blob_oid": oid,
            "git_mode": mode,
        }
        if not _strict_equal(record, expected):
            raise MulticityPlanSourceEvidenceTransitionV9Error(
                f"The V1 implementation identity changed: {path}"
            )
        history = _run_git(
            project_root,
            "log",
            "--format=%H",
            f"{V1_IMPLEMENTATION_COMMIT}..{current_head}",
            "--",
            path,
        )
        assert isinstance(history, str)
        if history.strip():
            raise MulticityPlanSourceEvidenceTransitionV9Error(
                f"A V1 runtime was modified after implementation: {path}"
            )


def _code_records_at_commit(
    project_root: Path,
    *,
    commit: str,
) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for path in transition_code_paths():
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


def _validate_config_files_at_commit(
    project_root: Path,
    *,
    commit: str,
    config_files: object,
) -> None:
    records = _require_mapping(config_files, label="v8 config files")
    for path, raw_record in records.items():
        if not isinstance(path, str):
            raise MulticityPlanSourceEvidenceTransitionV9Error("A v8 config path is not a string.")
        record = _require_mapping(raw_record, label=f"config {path}")
        raw, _, _ = _git_regular_blob(
            project_root,
            commit=commit,
            relative_path=path,
        )
        if not _strict_equal(record, {"sha256": _sha256_bytes(raw), "bytes": len(raw)}):
            raise MulticityPlanSourceEvidenceTransitionV9Error(
                f"Experiment config changed before v9: {path}"
            )


def _validate_scope(
    scope: Mapping[str, Any],
    *,
    code_files: Mapping[str, Any],
) -> None:
    paths, expected, config_path, output_path = _source_contract()
    if not _strict_equal(scope, expected):
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The source-evidence authorization scope changed."
        )
    for path in paths:
        _require_mapping(code_files.get(path), label=f"v9 code record {path}")
    scope_code_paths = scope.get("code_paths")
    if not _strict_equal(
        scope_code_paths,
        [path for path in paths if path not in {V9_MODULE_PATH, V9_SCRIPT_PATH}],
    ):
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The source-evidence code path scope is not exact."
        )
    tracked_outputs = scope.get("tracked_output_paths")
    if (
        not isinstance(tracked_outputs, list)
        or any(not isinstance(path, str) for path in tracked_outputs)
        or output_path not in tracked_outputs
    ):
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The source-evidence tracked output scope is not exact."
        )
    filesystem_paths = (*paths, *tracked_outputs)
    if any(any(character in path for character in "*?[]") for path in filesystem_paths):
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The source-evidence filesystem scope may not use wildcard paths."
        )
    config_record = _require_mapping(
        code_files.get(config_path), label="source-evidence config record"
    )
    scope_config = _require_mapping(
        scope.get("configuration"), label="source-evidence configuration"
    )
    if scope_config.get("path") != config_path or scope_config.get("sha256") != config_record.get(
        "sha256"
    ):
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The source-evidence config identity is not exact."
        )


def _validate_transition_boundary(
    predecessor: Mapping[str, Any], successor: Mapping[str, Any]
) -> None:
    if not _strict_equal(predecessor.get("authorized_now"), V8_AUTHORIZED_NOW):
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The v8 authorization boundary is not canonical."
        )
    if not _strict_equal(predecessor.get("locks"), LOCKS):
        raise MulticityPlanSourceEvidenceTransitionV9Error("The v8 lock boundary is not canonical.")
    if not _strict_equal(successor.get("authorized_now"), AUTHORIZED_NOW):
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The v9 transition opened a wrong permission."
        )
    if not _strict_equal(successor.get("locks"), LOCKS):
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The v9 transition changed a scientific lock."
        )
    changes = _recursive_diff_paths(
        {
            "authorized_now": predecessor["authorized_now"],
            "locks": predecessor["locks"],
        },
        {
            "authorized_now": successor["authorized_now"],
            "locks": successor["locks"],
        },
    )
    if changes != EXPECTED_TRANSITION_DIFF_PATHS:
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The v9 transition changed fields outside the exact permission boundary."
        )


def _build_v9_payload(
    predecessor: Mapping[str, Any],
    *,
    predecessor_bytes: int,
    terminal: Mapping[str, Any],
    terminal_bytes: int,
    precondition_commit: str,
    code_files: Mapping[str, Any],
) -> dict[str, Any]:
    """Build v9 from exact authenticated v8 and V1 evidence."""

    if re.fullmatch(r"[0-9a-f]{40}", precondition_commit) is None:
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The v9 precondition Git commit is invalid."
        )
    old_scope = deepcopy(
        _require_mapping(
            predecessor.get("predictor_contract_freeze_authorization_scope"),
            label="v8 predictor-contract authorization scope",
        )
    )
    _, authorization_scope, _, _ = _source_contract()
    _validate_scope(authorization_scope, code_files=code_files)
    payload = deepcopy(dict(predecessor))
    payload.pop("commit_sha256", None)
    payload.pop("predictor_contract_freeze_authorization_scope", None)
    payload.update(
        {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "planning_ready",
            "planning_stage": PLANNING_STAGE,
            "code_files": deepcopy(dict(code_files)),
            "transition": {
                "id": ("consume_predictor_contract_v1_and_authorize_missing_source_evidence_stage"),
                "mode": "tracked_manifests_and_local_git_only",
                "predecessor_plan_readiness": {
                    "path": PLAN_PATH,
                    "source_git_commit": V8_PUBLICATION_COMMIT,
                    "file_sha256": V8_FILE_SHA256,
                    "bytes": predecessor_bytes,
                    "commit_sha256": predecessor["commit_sha256"],
                    "state": predecessor["state"],
                    "planning_stage": predecessor["planning_stage"],
                    "next_safe_stage": predecessor["next_safe_stage"],
                },
                "predictor_contract_v1_terminal": {
                    "path": V1_TERMINAL_PATH,
                    "source_git_commit": V1_PUBLICATION_COMMIT,
                    "file_sha256": V1_FILE_SHA256,
                    "bytes": terminal_bytes,
                    "commit_sha256": terminal["commit_sha256"],
                    "state": terminal["state"],
                    "outcome": terminal["outcome"],
                    "observed_blockers": list(V1_EXPECTED_BLOCKERS),
                },
                "writer_precondition": {
                    "branch": "main",
                    "git_head": precondition_commit,
                    "origin_main": precondition_commit,
                    "worktree_clean": True,
                    "head_equals_local_origin_main": True,
                    "all_transition_inputs_regular_git_tracked_blobs": True,
                },
                "authorization_effective_only_when": {
                    "this_exact_plan_readiness_is_git_tracked": True,
                    "branch_is_main": True,
                    "worktree_is_clean": True,
                    "head_equals_local_origin_main": True,
                    "v9_check_only_passes_before_source_evidence_staging": True,
                },
            },
            "transition_access_contract": deepcopy(TRANSITION_ACCESS_CONTRACT),
            "locks": deepcopy(LOCKS),
            "authorized_now": deepcopy(AUTHORIZED_NOW),
            "consumed_predictor_contract_freeze_v1_authorization": {
                "status": "consumed_and_closed_after_deferred_v1",
                "predecessor_schema_version": 8,
                "predecessor_scope": old_scope,
                "completion_manifest_path": V1_TERMINAL_PATH,
                "completion_manifest_file_sha256": V1_FILE_SHA256,
                "completion_manifest_commit_sha256": V1_INTERNAL_COMMIT_SHA256,
                "completion_manifest_publication_git_commit": V1_PUBLICATION_COMMIT,
                "observed_blockers": list(V1_EXPECTED_BLOCKERS),
                "contract_freeze_permission_now": False,
                "boundary_and_public_metadata_staging_permission_now": False,
                "predictor_model_target_or_result_access": False,
            },
            "portable_predictor_contract_freeze_v1": {
                "path": V1_TERMINAL_PATH,
                "file_sha256": V1_FILE_SHA256,
                "bytes": terminal_bytes,
                "commit_sha256": V1_INTERNAL_COMMIT_SHA256,
                "publication_git_commit": V1_PUBLICATION_COMMIT,
                "state": V1_STATE,
                "outcome": V1_OUTCOME,
                "observed_blockers": list(V1_EXPECTED_BLOCKERS),
                "contract_locked": False,
                "predictor_build_authorized": False,
                "authentication_mode": ("exact_append_only_terminal_and_historical_git_lineage"),
            },
            "portable_predictor_source_evidence_stage_authorization_scope": (
                deepcopy(authorization_scope)
            ),
            "blockers_before_predictor_build": list(BLOCKERS_BEFORE_PREDICTOR_BUILD),
            "next_safe_stage": NEXT_SAFE_STAGE,
        }
    )
    _validate_transition_boundary(predecessor, payload)
    payload["commit_sha256"] = _canonical_sha256(payload)
    return payload


def _validate_exact_v9_payload(observed: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    recorded = observed.get("commit_sha256")
    body = {key: value for key, value in observed.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or _canonical_sha256(body) != recorded:
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The v9 planning internal commit is invalid."
        )
    if not _strict_equal(observed, expected):
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The v9 planning record differs from the complete reconstructed payload."
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
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The v9 transition requires clean synchronized main."
        )
    if expected_head is not None and head != expected_head:
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "HEAD changed between v9 transition gates."
        )
    for ancestor in (V8_PUBLICATION_COMMIT, V1_PUBLICATION_COMMIT):
        if not _is_ancestor(project_root, ancestor, head):
            raise MulticityPlanSourceEvidenceTransitionV9Error(
                "Canonical v8 or V1 evidence is not an ancestor of HEAD."
            )
    for path in required_paths:
        worktree_path = project_root / path
        if worktree_path.is_symlink() or not worktree_path.is_file():
            raise MulticityPlanSourceEvidenceTransitionV9Error(
                f"A required input is not one regular worktree file: {path}"
            )
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
            raise MulticityPlanSourceEvidenceTransitionV9Error(
                f"A required input differs from HEAD: {path}"
            )
    return head


def _require_v9_history(
    project_root: Path,
    *,
    publication_commit: str,
    precondition_commit: str,
    published_raw: bytes,
    current_head: str,
) -> None:
    ancestry = _run_git(project_root, "rev-list", "--parents", "-n", "1", publication_commit)
    assert isinstance(ancestry, str)
    if ancestry.split() != [publication_commit, precondition_commit]:
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The v9 publication is not the direct child of its precondition."
        )
    _require_exact_delta(
        project_root,
        parent=precondition_commit,
        commit=publication_commit,
        status="M",
        path=PLAN_PATH,
    )
    history = _run_git(
        project_root,
        "log",
        "--format=%H",
        f"{V8_PUBLICATION_COMMIT}..{publication_commit}",
        "--",
        PLAN_PATH,
    )
    assert isinstance(history, str)
    if [line for line in history.splitlines() if line] != [publication_commit]:
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "PLAN_READINESS changed outside the one v8-to-v9 publication."
        )
    if not _is_ancestor(project_root, publication_commit, current_head):
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The v9 publication is not an ancestor of current HEAD."
        )
    later = _run_git(
        project_root,
        "log",
        "--format=%H",
        f"{publication_commit}..{current_head}",
        "--",
        PLAN_PATH,
    )
    assert isinstance(later, str)
    if later.strip():
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "PLAN_READINESS changed after the v9 publication."
        )
    current_raw, _, _ = _git_regular_blob(
        project_root,
        commit=current_head,
        relative_path=PLAN_PATH,
    )
    if current_raw != published_raw:
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "Current PLAN_READINESS differs from the v9 publication."
        )


def _require_runtime_unchanged(
    project_root: Path,
    *,
    precondition_commit: str,
    current_head: str,
) -> None:
    for path in transition_code_paths():
        history = _run_git(
            project_root,
            "log",
            "--format=%H",
            f"{precondition_commit}..{current_head}",
            "--",
            path,
        )
        assert isinstance(history, str)
        if history.strip():
            raise MulticityPlanSourceEvidenceTransitionV9Error(
                f"A v9-authorized source-evidence runtime changed: {path}"
            )


def _expected_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(payload), indent=2).encode("utf-8")


def _locate_v9_publication_commit(
    project_root: Path,
    payload: Mapping[str, Any],
    *,
    current_head: str,
) -> str:
    transition = _require_mapping(payload.get("transition"), label="v9 transition")
    writer = _require_mapping(transition.get("writer_precondition"), label="v9 writer precondition")
    precondition = writer.get("git_head")
    if not isinstance(precondition, str):
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The v9 writer precondition commit is missing."
        )
    log = _run_git(project_root, "log", "--format=%H", current_head, "--", PLAN_PATH)
    assert isinstance(log, str)
    expected = _expected_json_bytes(payload)
    candidates: list[str] = []
    for commit in (line for line in log.splitlines() if line):
        ancestry = _run_git(project_root, "rev-list", "--parents", "-n", "1", commit)
        assert isinstance(ancestry, str)
        if ancestry.split() != [commit, precondition]:
            continue
        raw, _, _ = _git_regular_blob(
            project_root,
            commit=commit,
            relative_path=PLAN_PATH,
        )
        if raw == expected:
            candidates.append(commit)
    if len(candidates) != 1:
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The exact v9 transition must have one unique direct publication."
        )
    return candidates[0]


def authenticate_historical_v9_payload(
    project_root: str | Path,
    payload: Mapping[str, Any],
    *,
    publication_commit: str | None = None,
    current_head: str | None = None,
) -> dict[str, Any]:
    """Reconstruct v9 from historical v8 and V1 Git blobs."""

    root = Path(project_root).resolve()
    transition = _require_mapping(payload.get("transition"), label="v9 transition")
    writer = _require_mapping(transition.get("writer_precondition"), label="v9 writer precondition")
    precondition = writer.get("git_head")
    if not isinstance(precondition, str) or re.fullmatch(r"[0-9a-f]{40}", precondition) is None:
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The v9 precondition Git commit is invalid."
        )
    if not _is_ancestor(root, V1_PUBLICATION_COMMIT, precondition):
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The v9 precondition does not descend from the canonical V1 terminal."
        )
    predecessor, predecessor_raw = _historical_json(
        root,
        commit=V8_PUBLICATION_COMMIT,
        relative_path=PLAN_PATH,
    )
    _validate_v8(predecessor, predecessor_raw)
    plan_at_precondition, _, _ = _git_regular_blob(
        root,
        commit=precondition,
        relative_path=PLAN_PATH,
    )
    if plan_at_precondition != predecessor_raw:
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "PLAN_READINESS at the v9 precondition is not exact v8."
        )
    try:
        authenticate_historical_v8_payload(
            root,
            predecessor,
            publication_commit=V8_PUBLICATION_COMMIT,
            current_head=precondition,
        )
    except Exception as exc:
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The historical v8 predecessor failed authentication."
        ) from exc
    terminal, terminal_raw = _historical_json(
        root,
        commit=V1_PUBLICATION_COMMIT,
        relative_path=V1_TERMINAL_PATH,
    )
    _validate_v1_terminal(terminal, terminal_raw)
    _require_v1_history(root, terminal_raw=terminal_raw, current_head=precondition)
    _require_v1_runtime_history(
        root,
        terminal=terminal,
        current_head=precondition,
    )
    _validate_config_files_at_commit(
        root,
        commit=precondition,
        config_files=predecessor["config_files"],
    )
    code_files = _code_records_at_commit(root, commit=precondition)
    expected = _build_v9_payload(
        predecessor,
        predecessor_bytes=len(predecessor_raw),
        terminal=terminal,
        terminal_bytes=len(terminal_raw),
        precondition_commit=precondition,
        code_files=code_files,
    )
    _validate_exact_v9_payload(payload, expected)
    if publication_commit is not None:
        if re.fullmatch(r"[0-9a-f]{40}", publication_commit) is None:
            raise MulticityPlanSourceEvidenceTransitionV9Error(
                "The v9 publication Git commit is invalid."
            )
        published_raw, _, _ = _git_regular_blob(
            root,
            commit=publication_commit,
            relative_path=PLAN_PATH,
        )
        published = _json_from_bytes(published_raw, label=f"{publication_commit}:{PLAN_PATH}")
        if not _strict_equal(published, payload):
            raise MulticityPlanSourceEvidenceTransitionV9Error(
                "The supplied v9 payload differs from its publication blob."
            )
        end = publication_commit if current_head is None else current_head
        _require_v1_history(
            root,
            terminal_raw=terminal_raw,
            current_head=end,
        )
        _require_v1_runtime_history(
            root,
            terminal=terminal,
            current_head=end,
        )
        _require_v9_history(
            root,
            publication_commit=publication_commit,
            precondition_commit=precondition,
            published_raw=published_raw,
            current_head=end,
        )
        observed_code = _code_records_at_commit(root, commit=publication_commit)
        if not _strict_equal(observed_code, code_files):
            raise MulticityPlanSourceEvidenceTransitionV9Error(
                "The v9 publication changed an authorized runtime blob."
            )
        _require_runtime_unchanged(
            root,
            precondition_commit=precondition,
            current_head=end,
        )
    return deepcopy(dict(payload))


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def authorize_multicity_source_evidence_stage(
    *,
    project_root: str | Path | None = None,
    output_path: str | Path = PLAN_PATH,
    write: bool = True,
) -> dict[str, Any]:
    """Create or authenticate the exact tracked-only planning-v9 transition."""

    root = _default_project_root() if project_root is None else Path(project_root)
    root = root.resolve()
    destination = Path(output_path)
    if not destination.is_absolute():
        destination = root / destination
    if destination.resolve() != (root / PLAN_PATH).resolve():
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "The v9 transition may replace only canonical PLAN_READINESS.json."
        )
    predecessor, predecessor_raw = _historical_json(
        root,
        commit=V8_PUBLICATION_COMMIT,
        relative_path=PLAN_PATH,
    )
    _validate_v8(predecessor, predecessor_raw)
    terminal, terminal_raw = _historical_json(
        root,
        commit=V1_PUBLICATION_COMMIT,
        relative_path=V1_TERMINAL_PATH,
    )
    _validate_v1_terminal(terminal, terminal_raw)
    code_paths = transition_code_paths()
    config_paths = tuple(_require_mapping(predecessor.get("config_files"), label="v8 config files"))
    required = tuple(dict.fromkeys((*config_paths, *code_paths, PLAN_PATH, V1_TERMINAL_PATH)))
    head = _git_preflight(root, required_paths=required)
    current, current_raw = _read_current_json(destination)
    if _sha256_bytes(current_raw) == V8_FILE_SHA256:
        if not write:
            raise MulticityPlanSourceEvidenceTransitionV9Error(
                "PLAN_READINESS is still v8; planning v9 has not been written."
            )
        if current_raw != predecessor_raw:
            raise MulticityPlanSourceEvidenceTransitionV9Error(
                "Current v8 bytes differ from the canonical historical blob."
            )
        authenticate_historical_v8_payload(
            root,
            predecessor,
            publication_commit=V8_PUBLICATION_COMMIT,
            current_head=head,
        )
        _require_v1_history(root, terminal_raw=terminal_raw, current_head=head)
        _require_v1_runtime_history(
            root,
            terminal=terminal,
            current_head=head,
        )
        current_terminal, current_terminal_raw = _read_current_json(root / V1_TERMINAL_PATH)
        _validate_v1_terminal(current_terminal, current_terminal_raw)
        if current_terminal_raw != terminal_raw:
            raise MulticityPlanSourceEvidenceTransitionV9Error(
                "Current V1 terminal differs from its canonical publication."
            )
        _validate_config_files_at_commit(
            root,
            commit=head,
            config_files=predecessor["config_files"],
        )
        code_files = _code_records_at_commit(root, commit=head)
        payload = _build_v9_payload(
            predecessor,
            predecessor_bytes=len(predecessor_raw),
            terminal=terminal,
            terminal_bytes=len(terminal_raw),
            precondition_commit=head,
            code_files=code_files,
        )
        _git_preflight(root, required_paths=required, expected_head=head)
        expected = _expected_json_bytes(payload)
        if destination.read_bytes() != predecessor_raw:
            raise MulticityPlanSourceEvidenceTransitionV9Error(
                "PLAN_READINESS changed before the v9 write boundary."
            )
        temporary = destination.with_suffix(destination.suffix + ".partial")
        temporary.write_bytes(expected)
        temporary.replace(destination)
        return payload
    publication = _locate_v9_publication_commit(root, current, current_head=head)
    authenticated = authenticate_historical_v9_payload(
        root,
        current,
        publication_commit=publication,
        current_head=head,
    )
    _git_preflight(root, required_paths=required, expected_head=head)
    if destination.read_bytes() != _expected_json_bytes(authenticated):
        raise MulticityPlanSourceEvidenceTransitionV9Error(
            "Current v9 bytes changed after authentication."
        )
    return authenticated
