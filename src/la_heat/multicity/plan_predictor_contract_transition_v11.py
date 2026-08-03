"""Consume source evidence V1 and authorize one predictor-contract V2 decision.

This tracked-only planning transition authenticates immutable Git blobs.  It
does not import a scientific reader or open geometry, raster, predictor,
model, target, prediction, evaluation, or result bytes.  The only permission
opened by v11 is the exact target-blind portable predictor source-and-
calibration contract-freeze V2 decision; predictor construction remains
closed and requires a later planning transition.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import subprocess
import uuid
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, Final

SCHEMA_VERSION: Final = 11
ALGORITHM_VERSION: Final = "multicity-planning-readiness-v11"
PLANNING_STAGE: Final = (
    "portable_predictor_source_evidence_complete_contract_freeze_v2_authorized"
)
NEXT_SAFE_STAGE: Final = "freeze_exact_portable_predictor_contract_v2"

PLAN_PATH: Final = "manifests/multicity/PLAN_READINESS.json"
V10_PUBLICATION_COMMIT: Final = "975c155d625de4c9912b9cbf1b5ec710e945bc07"
V10_IMPLEMENTATION_COMMIT: Final = "6c03b7d7fb2d4ec1b9cc99d52302ee129a700b2d"
V10_FILE_SHA256: Final = (
    "bdd2a1c75d397116f2c9e9f00e7e673dc83581e5b3b707b9bf5318e815cb3c28"
)
V10_INTERNAL_COMMIT_SHA256: Final = (
    "61359cbec70f2f4f549ed177abdca8b06b3459e3bb21d3f3d46e75d99001ec26"
)
V10_BYTES: Final = 45_470

SOURCE_PUBLICATION_COMMIT: Final = "d41cc58078b23b3b4e7295c38c16d86ff02f5974"
SOURCE_TERMINAL_PATH: Final = (
    "manifests/multicity/reviews/portable_predictor_contract/"
    "PORTABLE_PREDICTOR_SOURCE_EVIDENCE_V1.json"
)
SOURCE_TRACKED_OUTPUT_PATHS: Final = (
    "manifests/multicity/cities/houston_tx/geography/GEOGRAPHY.json",
    "manifests/multicity/cities/chicago_il/geography/GEOGRAPHY.json",
    "manifests/multicity/cities/houston_tx/source_footprints/SOURCE_FOOTPRINTS.json",
    "manifests/multicity/cities/chicago_il/source_footprints/SOURCE_FOOTPRINTS.json",
    "manifests/multicity/cities/phoenix_az/source_evidence/"
    "PORTABLE_PREDICTOR_SOURCE_EVIDENCE_V1.json",
    "manifests/multicity/cities/houston_tx/source_evidence/"
    "PORTABLE_PREDICTOR_SOURCE_EVIDENCE_V1.json",
    "manifests/multicity/cities/chicago_il/source_evidence/"
    "PORTABLE_PREDICTOR_SOURCE_EVIDENCE_V1.json",
    SOURCE_TERMINAL_PATH,
)

IMPLEMENTATION_BASE_COMMIT: Final = "6b12c6035c913ee6c410b4d890052b539f294b05"
CONFIG_V2_PATH: Final = "configs/multicity/portable_predictor_contract_freeze_v2.toml"
CONTRACT_V2_MODULE_PATH: Final = (
    "src/la_heat/multicity/portable_predictor_contract_freeze_v2.py"
)
V11_MODULE_PATH: Final = (
    "src/la_heat/multicity/plan_predictor_contract_transition_v11.py"
)
V11_SCRIPT_PATH: Final = (
    "scripts/authorize_multicity_predictor_contract_freeze_v2.py"
)
CONTRACT_V2_SCRIPT_PATH: Final = (
    "scripts/audit_multicity_portable_predictor_contract_freeze_v2.py"
)
V11_TEST_PATH: Final = (
    "tests/test_multicity_plan_predictor_contract_transition_v11.py"
)
CONTRACT_V2_TEST_PATH: Final = (
    "tests/test_multicity_portable_predictor_contract_freeze_v2.py"
)

EXPECTED_IMPLEMENTATION_DELTA: Final = frozenset(
    {
        ("A", CONFIG_V2_PATH),
        ("A", CONTRACT_V2_MODULE_PATH),
        ("A", V11_MODULE_PATH),
        ("A", V11_SCRIPT_PATH),
        ("A", CONTRACT_V2_SCRIPT_PATH),
        ("A", V11_TEST_PATH),
        ("A", CONTRACT_V2_TEST_PATH),
    }
)

V10_AUTHORIZED_NOW: Final = {
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
    "portable_predictor_missing_source_evidence_staging": True,
}
AUTHORIZED_NOW: Final = {
    **{key: False for key in V10_AUTHORIZED_NOW},
    "portable_predictor_source_and_calibration_contract_freeze_v2": True,
}
LOCKS: Final = {
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
BLOCKERS_BEFORE_PREDICTOR_BUILD: Final = (
    "complete_deferred_portable_predictor_contract_v2_decision",
    "complete_four_city_geography_contract_and_los_angeles_parity_evidence",
    "complete_four_city_worldcover_item_mosaic_and_eligible_support_evidence",
    "complete_external_city_sentinel_asset_calibration_smoke_evidence",
    "complete_separate_portable_predictor_contract_v3_decision",
    "authorize_predictor_construction_with_separate_tracked_only_transition",
    "promote_protocol_from_draft_with_separate_lock",
)
EXPECTED_PERMISSION_DIFF_PATHS: Final = (
    "authorized_now.portable_predictor_missing_source_evidence_staging",
    "authorized_now.portable_predictor_source_and_calibration_contract_freeze_v2",
)

TRANSITION_ACCESS_CONTRACT: Final = {
    "network_requests": 0,
    "tracked_json_toml_and_code_bytes_read": True,
    "historical_git_blob_and_metadata_read": True,
    "untracked_path_names_checked_by_git_status": True,
    "untracked_file_contents_opened": False,
    "ignored_paths_requested": False,
    "source_raster_or_archive_payload_opened": False,
    "geometry_opened": False,
    "eligible_land_grid_opened": False,
    "predictor_values_opened_or_computed": False,
    "predictor_construction_performed": False,
    "model_fit_performed": False,
    "model_predictions_computed": False,
    "external_target_or_qa_values_read": False,
    "landsat_thermal_values_read": False,
    "final_evaluation_outputs_opened": False,
}


class MulticityPlanPredictorContractTransitionV11Error(ValueError):
    """Raised when the exact tracked-only planning-v11 transition fails."""


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _expected_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(payload), indent=2).encode("utf-8")


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
        raise MulticityPlanPredictorContractTransitionV11Error(
            f"{label} must be an object."
        )
    return value


def _json_from_bytes(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MulticityPlanPredictorContractTransitionV11Error(
            f"Cannot parse authenticated JSON: {label}"
        ) from exc
    payload = _require_mapping(payload, label=label)
    recorded = payload.get("commit_sha256")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or _canonical_sha256(body) != recorded:
        raise MulticityPlanPredictorContractTransitionV11Error(
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
        stderr = (
            completed.stderr.decode("utf-8", errors="replace")
            if binary
            else completed.stderr
        )
        raise MulticityPlanPredictorContractTransitionV11Error(
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
        raise MulticityPlanPredictorContractTransitionV11Error(
            f"Required path is not one exact regular Git blob: {relative_path}"
        )
    raw = _run_git(project_root, "show", f"{commit}:{relative_path}", binary=True)
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
    if path.is_symlink() or not path.is_file():
        raise MulticityPlanPredictorContractTransitionV11Error(
            "Canonical planning is not one regular file."
        )
    before = path.read_bytes()
    after = path.read_bytes()
    if before != after:
        raise MulticityPlanPredictorContractTransitionV11Error(
            "Canonical planning changed while read."
        )
    return _json_from_bytes(before, label="canonical planning"), before


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


def _parse_delta(raw: bytes, *, label: str) -> frozenset[tuple[str, str]]:
    fields = raw.split(b"\0")
    if fields[-1:] != [b""] or len(fields[:-1]) % 2:
        raise MulticityPlanPredictorContractTransitionV11Error(
            f"{label} is not valid NUL-delimited status/path output."
        )
    try:
        pairs = [
            (fields[index].decode("ascii"), fields[index + 1].decode("utf-8"))
            for index in range(0, len(fields) - 1, 2)
        ]
    except UnicodeDecodeError as exc:
        raise MulticityPlanPredictorContractTransitionV11Error(
            f"{label} contains noncanonical text."
        ) from exc
    if len(pairs) != len(set(pairs)):
        raise MulticityPlanPredictorContractTransitionV11Error(
            f"{label} contains a duplicate status/path pair."
        )
    return frozenset(pairs)


def _require_exact_delta_set(
    project_root: Path,
    *,
    parent: str,
    commit: str,
    expected: frozenset[tuple[str, str]],
    label: str,
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
    observed = _parse_delta(raw, label=label)
    if len(observed) != len(expected) or observed != expected:
        raise MulticityPlanPredictorContractTransitionV11Error(
            f"{label} changed a path outside its exact allowlist."
        )


def _preflight_decision_contract_import(project_root: Path) -> str:
    branch = _run_git(project_root, "branch", "--show-current")
    head = _run_git(project_root, "rev-parse", "HEAD")
    origin = _run_git(project_root, "rev-parse", "origin/main")
    status = _run_git(
        project_root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        binary=True,
    )
    assert isinstance(branch, str)
    assert isinstance(head, str)
    assert isinstance(origin, str)
    assert isinstance(status, bytes)
    normalized_head = head.strip()
    if branch.strip() != "main" or normalized_head != origin.strip() or status:
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The V2 contract module may be imported only from clean synchronized main."
        )
    additions = _run_git(
        project_root,
        "log",
        "--format=%H",
        "--diff-filter=A",
        normalized_head,
        "--",
        CONTRACT_V2_MODULE_PATH,
    )
    assert isinstance(additions, str)
    candidates = [line for line in additions.splitlines() if line]
    if len(candidates) != 1:
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The V2 contract module must have one exact Git addition before import."
        )
    implementation = candidates[0]
    if not _is_ancestor(project_root, implementation, normalized_head):
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The V2 contract implementation is not on current HEAD."
        )
    _implementation_delta(project_root, implementation)
    later = _run_git(
        project_root,
        "log",
        "--format=%H",
        f"{implementation}..{normalized_head}",
        "--",
        CONTRACT_V2_MODULE_PATH,
    )
    assert isinstance(later, str)
    if later.strip():
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The V2 contract module changed after its exact implementation commit."
        )
    path = project_root / CONTRACT_V2_MODULE_PATH
    if path.is_symlink() or not path.is_file():
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The V2 contract module is not one regular tracked file."
        )
    _, oid, _ = _git_regular_blob(
        project_root,
        commit=normalized_head,
        relative_path=CONTRACT_V2_MODULE_PATH,
    )
    worktree_oid = _run_git(
        project_root,
        "hash-object",
        f"--path={CONTRACT_V2_MODULE_PATH}",
        "--",
        CONTRACT_V2_MODULE_PATH,
    )
    assert isinstance(worktree_oid, str)
    if worktree_oid.strip() != oid:
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The V2 contract module differs from its authenticated HEAD blob."
        )
    return normalized_head


def _decision_contract(project_root: Path) -> tuple[
    tuple[str, ...],
    dict[str, dict[str, object]],
    dict[str, Any],
    str,
    str,
]:
    import_head = _preflight_decision_contract_import(project_root)
    module = importlib.import_module(
        "la_heat.multicity.portable_predictor_contract_freeze_v2"
    )
    try:
        code_paths = tuple(module.CODE_PATHS)
        source_records = deepcopy(dict(module.SOURCE_OUTPUT_RECORDS))
        scope = deepcopy(module.expected_plan_authorization_scope())
        output_path = module.OUTPUT_PATH
    except (AttributeError, TypeError) as exc:
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The contract-freeze V2 runtime does not expose its exact contract."
        ) from exc
    if not all(isinstance(path, str) for path in code_paths):
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The V2 runtime path list is invalid."
        )
    if tuple(source_records) != SOURCE_TRACKED_OUTPUT_PATHS:
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The V2 source-evidence record set is not the exact eight-file set."
        )
    if not isinstance(scope, dict) or not isinstance(output_path, str):
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The V2 decision authorization contract is invalid."
        )
    return code_paths, source_records, scope, output_path, import_head


def _code_records_at_commit(
    project_root: Path,
    *,
    commit: str,
    code_paths: tuple[str, ...],
) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for relative in code_paths:
        raw, oid, mode = _git_regular_blob(
            project_root,
            commit=commit,
            relative_path=relative,
        )
        records[relative] = {
            "sha256": _sha256_bytes(raw),
            "bytes": len(raw),
            "git_blob_oid": oid,
            "git_mode": mode,
        }
    return records


def _validate_v10(payload: Mapping[str, Any], raw: bytes) -> None:
    expected = {
        "schema_version": 10,
        "algorithm_version": "multicity-planning-readiness-v10",
        "state": "planning_ready",
        "planning_stage": (
            "portable_predictor_source_evidence_v1_runtime_hotfix_resume_authorized"
        ),
        "next_safe_stage": "resume_missing_portable_predictor_source_evidence_v1",
        "commit_sha256": V10_INTERNAL_COMMIT_SHA256,
    }
    if len(raw) != V10_BYTES or _sha256_bytes(raw) != V10_FILE_SHA256:
        raise MulticityPlanPredictorContractTransitionV11Error(
            "Canonical planning-v10 bytes changed."
        )
    for key, value in expected.items():
        if not _strict_equal(payload.get(key), value):
            raise MulticityPlanPredictorContractTransitionV11Error(
                f"Canonical planning-v10 field changed: {key}"
            )
    if not _strict_equal(payload.get("authorized_now"), V10_AUTHORIZED_NOW):
        raise MulticityPlanPredictorContractTransitionV11Error(
            "Canonical planning-v10 permissions changed."
        )
    if not _strict_equal(payload.get("locks"), LOCKS):
        raise MulticityPlanPredictorContractTransitionV11Error(
            "Canonical planning-v10 locks changed."
        )


def _v10_predecessor(project_root: Path) -> tuple[dict[str, Any], bytes]:
    payload, raw = _historical_json(
        project_root,
        commit=V10_PUBLICATION_COMMIT,
        relative_path=PLAN_PATH,
    )
    _validate_v10(payload, raw)
    return payload, raw


def _validate_source_records(
    project_root: Path,
    source_records: Mapping[str, Mapping[str, object]],
) -> tuple[dict[str, Any], dict[str, dict[str, object]]]:
    observed_records: dict[str, dict[str, object]] = {}
    terminal: dict[str, Any] | None = None
    for relative in SOURCE_TRACKED_OUTPUT_PATHS:
        expected = _require_mapping(
            source_records.get(relative),
            label=f"source-evidence record for {relative}",
        )
        payload, raw = _historical_json(
            project_root,
            commit=SOURCE_PUBLICATION_COMMIT,
            relative_path=relative,
        )
        observed = {
            "bytes": len(raw),
            "file_sha256": _sha256_bytes(raw),
            "commit_sha256": payload.get("commit_sha256"),
            "state": payload.get("state"),
        }
        if not _strict_equal(observed, expected):
            raise MulticityPlanPredictorContractTransitionV11Error(
                f"Published source-evidence record changed: {relative}"
            )
        observed_records[relative] = deepcopy(observed)
        if relative == SOURCE_TERMINAL_PATH:
            terminal = payload
    if terminal is None:
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The source-evidence terminal is absent from its exact publication."
        )
    if not _strict_equal(
        terminal.get("tracked_output_paths"),
        list(SOURCE_TRACKED_OUTPUT_PATHS),
    ):
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The source-evidence terminal changed its exact output declaration."
        )
    checkpoints = _require_mapping(
        terminal.get("tracked_checkpoints"),
        label="source-evidence tracked checkpoints",
    )
    if set(checkpoints) != set(SOURCE_TRACKED_OUTPUT_PATHS[:-1]):
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The source-evidence terminal changed its checkpoint set."
        )
    if (
        terminal.get("schema_version") != 1
        or terminal.get("algorithm_version")
        != "portable-predictor-source-evidence-v1"
        or terminal.get("state")
        != "complete_target_blind_portable_predictor_source_evidence"
        or terminal.get("tracked_output_set_exact") is not True
        or terminal.get("terminal_written_last") is not True
    ):
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The source-evidence V1 completion boundary changed."
        )
    return terminal, observed_records


def _require_fixed_history(project_root: Path, *, current_head: str) -> None:
    for ancestor in (
        V10_PUBLICATION_COMMIT,
        SOURCE_PUBLICATION_COMMIT,
        IMPLEMENTATION_BASE_COMMIT,
    ):
        if not _is_ancestor(project_root, ancestor, current_head):
            raise MulticityPlanPredictorContractTransitionV11Error(
                f"Required historical commit is not an ancestor: {ancestor}"
            )
    v10_ancestry = _run_git(
        project_root,
        "rev-list",
        "--parents",
        "-n",
        "1",
        V10_PUBLICATION_COMMIT,
    )
    assert isinstance(v10_ancestry, str)
    if v10_ancestry.split() != [V10_PUBLICATION_COMMIT, V10_IMPLEMENTATION_COMMIT]:
        raise MulticityPlanPredictorContractTransitionV11Error(
            "Planning-v10 is not the exact direct publication commit."
        )
    _require_exact_delta_set(
        project_root,
        parent=V10_IMPLEMENTATION_COMMIT,
        commit=V10_PUBLICATION_COMMIT,
        expected=frozenset({("M", PLAN_PATH)}),
        label="planning-v10 publication delta",
    )
    source_ancestry = _run_git(
        project_root,
        "rev-list",
        "--parents",
        "-n",
        "1",
        SOURCE_PUBLICATION_COMMIT,
    )
    assert isinstance(source_ancestry, str)
    if source_ancestry.split() != [SOURCE_PUBLICATION_COMMIT, V10_PUBLICATION_COMMIT]:
        raise MulticityPlanPredictorContractTransitionV11Error(
            "Source evidence is not the direct child of canonical planning-v10."
        )
    _require_exact_delta_set(
        project_root,
        parent=V10_PUBLICATION_COMMIT,
        commit=SOURCE_PUBLICATION_COMMIT,
        expected=frozenset({("A", path) for path in SOURCE_TRACKED_OUTPUT_PATHS}),
        label="source-evidence publication delta",
    )
    for relative in SOURCE_TRACKED_OUTPUT_PATHS:
        additions = _run_git(
            project_root,
            "log",
            "--format=%H",
            "--diff-filter=A",
            current_head,
            "--",
            relative,
        )
        assert isinstance(additions, str)
        if [line for line in additions.splitlines() if line] != [SOURCE_PUBLICATION_COMMIT]:
            raise MulticityPlanPredictorContractTransitionV11Error(
                f"Source-evidence path does not have one exact addition: {relative}"
            )
        later = _run_git(
            project_root,
            "log",
            "--format=%H",
            f"{SOURCE_PUBLICATION_COMMIT}..{current_head}",
            "--",
            relative,
        )
        assert isinstance(later, str)
        if later.strip():
            raise MulticityPlanPredictorContractTransitionV11Error(
                f"Append-only source evidence changed after publication: {relative}"
            )


def _implementation_delta(project_root: Path, implementation_commit: str) -> None:
    ancestry = _run_git(
        project_root,
        "rev-list",
        "--parents",
        "-n",
        "1",
        implementation_commit,
    )
    assert isinstance(ancestry, str)
    if ancestry.split() != [implementation_commit, IMPLEMENTATION_BASE_COMMIT]:
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The v11/V2 implementation must directly descend from its exact base."
        )
    _require_exact_delta_set(
        project_root,
        parent=IMPLEMENTATION_BASE_COMMIT,
        commit=implementation_commit,
        expected=EXPECTED_IMPLEMENTATION_DELTA,
        label="v11/V2 implementation delta",
    )


def _validate_authorization_scope(
    project_root: Path,
    *,
    implementation_commit: str,
    code_paths: tuple[str, ...],
    code_files: Mapping[str, Mapping[str, object]],
    source_records: Mapping[str, Mapping[str, object]],
    scope: Mapping[str, Any],
    decision_output_path: str,
) -> None:
    if scope.get("decision_id") != "portable_predictor_contract_freeze_v2":
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The V2 decision identifier changed."
        )
    if not _strict_equal(scope.get("decision_runtime_paths"), list(code_paths)):
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The V2 decision runtime path scope changed."
        )
    if not _strict_equal(scope.get("source_output_records"), source_records):
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The V2 authorization changed its source-evidence records."
        )
    configuration = _require_mapping(
        scope.get("configuration"),
        label="V2 authorization configuration",
    )
    if configuration.get("path") != CONFIG_V2_PATH:
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The V2 authorization configuration path changed."
        )
    config_record = _require_mapping(
        code_files.get(CONFIG_V2_PATH),
        label="V2 configuration code record",
    )
    if (
        configuration.get("bytes") != config_record.get("bytes")
        or configuration.get("sha256") != config_record.get("sha256")
    ):
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The V2 authorization configuration identity changed."
        )
    if scope.get("decision_output_path") != decision_output_path:
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The V2 decision output path changed."
        )
    if scope.get("network_requests") != 0:
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The tracked-only V2 authorization may not permit networking."
        )
    for key in (
        "untracked_file_contents_allowed",
        "source_payload_or_geometry_allowed",
        "predictor_or_model_value_allowed",
        "external_target_or_qa_value_allowed",
        "final_evaluation_output_allowed",
        "predictor_construction_allowed",
        "model_fitting_allowed",
        "protocol_promotion_allowed",
    ):
        if scope.get(key) is not False:
            raise MulticityPlanPredictorContractTransitionV11Error(
                f"The V2 authorization opened a forbidden boundary: {key}"
            )
    for relative in code_paths:
        _git_regular_blob(
            project_root,
            commit=implementation_commit,
            relative_path=relative,
        )


def _validate_transition_boundary(
    predecessor: Mapping[str, Any],
    successor: Mapping[str, Any],
) -> None:
    if not _strict_equal(predecessor.get("authorized_now"), V10_AUTHORIZED_NOW):
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The v10 authorization boundary is not canonical."
        )
    if not _strict_equal(predecessor.get("locks"), LOCKS):
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The v10 lock boundary is not canonical."
        )
    if not _strict_equal(successor.get("authorized_now"), AUTHORIZED_NOW):
        raise MulticityPlanPredictorContractTransitionV11Error(
            "Planning-v11 opened a permission outside the exact V2 decision."
        )
    if not _strict_equal(successor.get("locks"), LOCKS):
        raise MulticityPlanPredictorContractTransitionV11Error(
            "Planning-v11 changed a scientific lock."
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
    if changes != EXPECTED_PERMISSION_DIFF_PATHS:
        raise MulticityPlanPredictorContractTransitionV11Error(
            "Planning-v11 changed fields outside the exact permission boundary."
        )


def _build_v11_payload(
    predecessor: Mapping[str, Any],
    *,
    predecessor_bytes: int,
    terminal: Mapping[str, Any],
    source_records: Mapping[str, Mapping[str, object]],
    implementation_commit: str,
    code_files: Mapping[str, Mapping[str, object]],
    authorization_scope: Mapping[str, Any],
) -> dict[str, Any]:
    old_scope = deepcopy(
        _require_mapping(
            predecessor.get("portable_predictor_source_evidence_stage_authorization_scope"),
            label="v10 source-evidence authorization scope",
        )
    )
    old_transition = deepcopy(
        _require_mapping(predecessor.get("transition"), label="v10 transition")
    )
    terminal_record = source_records[SOURCE_TERMINAL_PATH]
    payload = deepcopy(dict(predecessor))
    payload.pop("commit_sha256", None)
    payload.pop("portable_predictor_source_evidence_stage_authorization_scope", None)
    payload.update(
        {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "planning_ready",
            "planning_stage": PLANNING_STAGE,
            "code_files": deepcopy(dict(code_files)),
            "transition": {
                "id": (
                    "consume_portable_predictor_source_evidence_v1_and_"
                    "authorize_contract_freeze_v2"
                ),
                "mode": "tracked_manifests_toml_code_and_local_git_only",
                "predecessor_plan_readiness": {
                    "path": PLAN_PATH,
                    "source_git_commit": V10_PUBLICATION_COMMIT,
                    "file_sha256": V10_FILE_SHA256,
                    "bytes": predecessor_bytes,
                    "commit_sha256": V10_INTERNAL_COMMIT_SHA256,
                    "state": predecessor["state"],
                    "planning_stage": predecessor["planning_stage"],
                    "next_safe_stage": predecessor["next_safe_stage"],
                },
                "source_evidence_v1_publication": {
                    "path": SOURCE_TERMINAL_PATH,
                    "source_git_commit": SOURCE_PUBLICATION_COMMIT,
                    "bytes": terminal_record["bytes"],
                    "file_sha256": terminal_record["file_sha256"],
                    "commit_sha256": terminal_record["commit_sha256"],
                    "state": terminal["state"],
                    "exact_added_paths": list(SOURCE_TRACKED_OUTPUT_PATHS),
                    "all_eight_outputs_append_only": True,
                },
                "implementation": {
                    "base_git_commit": IMPLEMENTATION_BASE_COMMIT,
                    "implementation_git_commit": implementation_commit,
                    "delta": [
                        {"status": status, "path": path}
                        for status, path in sorted(EXPECTED_IMPLEMENTATION_DELTA)
                    ],
                },
                "consumed_v10_transition": old_transition,
                "writer_precondition": {
                    "branch": "main",
                    "git_head": implementation_commit,
                    "origin_main": implementation_commit,
                    "worktree_clean": True,
                    "head_equals_local_origin_main": True,
                    "all_transition_inputs_regular_git_tracked_blobs": True,
                },
                "authorization_effective_only_when": {
                    "this_exact_plan_readiness_is_git_tracked": True,
                    "branch_is_main": True,
                    "worktree_is_clean": True,
                    "head_equals_local_origin_main": True,
                    "v11_check_only_passes_before_contract_freeze_v2": True,
                },
            },
            "transition_access_contract": deepcopy(TRANSITION_ACCESS_CONTRACT),
            "locks": deepcopy(LOCKS),
            "authorized_now": deepcopy(AUTHORIZED_NOW),
            "consumed_portable_predictor_source_evidence_stage_authorization": {
                "status": "consumed_and_closed_after_complete_source_evidence_v1",
                "predecessor_schema_version": 10,
                "predecessor_scope": old_scope,
                "completion_manifest_path": SOURCE_TERMINAL_PATH,
                "completion_manifest_file_sha256": terminal_record["file_sha256"],
                "completion_manifest_commit_sha256": terminal_record["commit_sha256"],
                "completion_manifest_publication_git_commit": (
                    SOURCE_PUBLICATION_COMMIT
                ),
                "missing_source_evidence_staging_permission_now": False,
                "predictor_model_target_or_result_access": False,
            },
            "portable_predictor_source_evidence_v1": {
                "path": SOURCE_TERMINAL_PATH,
                "bytes": terminal_record["bytes"],
                "file_sha256": terminal_record["file_sha256"],
                "commit_sha256": terminal_record["commit_sha256"],
                "publication_git_commit": SOURCE_PUBLICATION_COMMIT,
                "state": terminal["state"],
                "tracked_output_paths": list(SOURCE_TRACKED_OUTPUT_PATHS),
                "tracked_output_records": deepcopy(dict(source_records)),
                "source_evidence_complete": True,
                "predictor_values_computed": False,
                "authentication_mode": (
                    "exact_eight_file_direct_child_publication_and_historical_git_lineage"
                ),
            },
            "portable_predictor_contract_freeze_v2_authorization_scope": deepcopy(
                dict(authorization_scope)
            ),
            "blockers_before_predictor_build": list(BLOCKERS_BEFORE_PREDICTOR_BUILD),
            "next_safe_stage": NEXT_SAFE_STAGE,
        }
    )
    _validate_transition_boundary(predecessor, payload)
    payload["commit_sha256"] = _canonical_sha256(payload)
    return payload


def _validate_exact_v11_payload(
    observed: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> None:
    recorded = observed.get("commit_sha256")
    body = {key: value for key, value in observed.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or _canonical_sha256(body) != recorded:
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The v11 planning internal commit is invalid."
        )
    if not _strict_equal(observed, expected):
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The v11 planning record differs from its full reconstruction."
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
        "-z",
        "--untracked-files=all",
        binary=True,
    )
    assert isinstance(branch, str)
    assert isinstance(head, str)
    assert isinstance(origin, str)
    assert isinstance(status, bytes)
    normalized_head = head.strip()
    if branch.strip() != "main" or normalized_head != origin.strip() or status:
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The v11 transition requires clean synchronized main."
        )
    if expected_head is not None and normalized_head != expected_head:
        raise MulticityPlanPredictorContractTransitionV11Error(
            "HEAD changed between v11 transition gates."
        )
    for ancestor in (
        V10_PUBLICATION_COMMIT,
        SOURCE_PUBLICATION_COMMIT,
        IMPLEMENTATION_BASE_COMMIT,
    ):
        if not _is_ancestor(project_root, ancestor, normalized_head):
            raise MulticityPlanPredictorContractTransitionV11Error(
                f"Required historical commit is not an ancestor: {ancestor}"
            )
    for relative in dict.fromkeys(required_paths):
        path = project_root / relative
        if path.is_symlink() or not path.is_file():
            raise MulticityPlanPredictorContractTransitionV11Error(
                f"A required v11 input is not one regular file: {relative}"
            )
        _, oid, _ = _git_regular_blob(
            project_root,
            commit=normalized_head,
            relative_path=relative,
        )
        worktree_oid = _run_git(
            project_root,
            "hash-object",
            f"--path={relative}",
            "--",
            relative,
        )
        assert isinstance(worktree_oid, str)
        if worktree_oid.strip() != oid:
            raise MulticityPlanPredictorContractTransitionV11Error(
                f"A required v11 input differs from HEAD: {relative}"
            )
    return normalized_head


def _require_v11_history(
    project_root: Path,
    *,
    publication_commit: str,
    implementation_commit: str,
    published_raw: bytes,
    current_head: str,
) -> None:
    ancestry = _run_git(
        project_root,
        "rev-list",
        "--parents",
        "-n",
        "1",
        publication_commit,
    )
    assert isinstance(ancestry, str)
    if ancestry.split() != [publication_commit, implementation_commit]:
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The v11 publication is not the direct child of its implementation."
        )
    _require_exact_delta_set(
        project_root,
        parent=implementation_commit,
        commit=publication_commit,
        expected=frozenset({("M", PLAN_PATH)}),
        label="planning-v11 publication delta",
    )
    history = _run_git(
        project_root,
        "log",
        "--format=%H",
        f"{V10_PUBLICATION_COMMIT}..{publication_commit}",
        "--",
        PLAN_PATH,
    )
    assert isinstance(history, str)
    if [line for line in history.splitlines() if line] != [publication_commit]:
        raise MulticityPlanPredictorContractTransitionV11Error(
            "PLAN_READINESS changed outside the one v10-to-v11 publication."
        )
    if not _is_ancestor(project_root, publication_commit, current_head):
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The v11 publication is not an ancestor of current HEAD."
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
        raise MulticityPlanPredictorContractTransitionV11Error(
            "PLAN_READINESS changed after v11 publication."
        )
    current_raw, _, _ = _git_regular_blob(
        project_root,
        commit=current_head,
        relative_path=PLAN_PATH,
    )
    if current_raw != published_raw:
        raise MulticityPlanPredictorContractTransitionV11Error(
            "Current PLAN_READINESS differs from the v11 publication."
        )


def _require_runtime_unchanged(
    project_root: Path,
    *,
    implementation_commit: str,
    current_head: str,
    code_paths: tuple[str, ...],
) -> None:
    for relative in code_paths:
        history = _run_git(
            project_root,
            "log",
            "--format=%H",
            f"{implementation_commit}..{current_head}",
            "--",
            relative,
        )
        assert isinstance(history, str)
        if history.strip():
            raise MulticityPlanPredictorContractTransitionV11Error(
                f"A v11-authorized V2 runtime changed: {relative}"
            )


def _locate_v11_publication_commit(
    project_root: str | Path,
    payload: Mapping[str, Any],
    *,
    current_head: str,
) -> str:
    """Locate the unique direct Git publication of these exact v11 bytes."""

    root = Path(project_root).resolve()
    transition = _require_mapping(payload.get("transition"), label="v11 transition")
    writer = _require_mapping(
        transition.get("writer_precondition"),
        label="v11 writer precondition",
    )
    implementation = writer.get("git_head")
    if not isinstance(implementation, str):
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The v11 implementation commit is missing."
        )
    log = _run_git(root, "log", "--format=%H", current_head, "--", PLAN_PATH)
    assert isinstance(log, str)
    expected = _expected_json_bytes(payload)
    candidates: list[str] = []
    for commit in (line for line in log.splitlines() if line):
        ancestry = _run_git(root, "rev-list", "--parents", "-n", "1", commit)
        assert isinstance(ancestry, str)
        if ancestry.split() != [commit, implementation]:
            continue
        raw, _, _ = _git_regular_blob(root, commit=commit, relative_path=PLAN_PATH)
        if raw == expected:
            candidates.append(commit)
    if len(candidates) != 1:
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The exact v11 transition must have one unique direct publication."
        )
    return candidates[0]


def authenticate_historical_v11_payload(
    project_root: str | Path,
    payload: Mapping[str, Any],
    *,
    publication_commit: str | None = None,
    current_head: str | None = None,
) -> dict[str, Any]:
    """Reconstruct v11 from fixed v10, source publication, and V2 code blobs."""

    root = Path(project_root).resolve()
    transition = _require_mapping(payload.get("transition"), label="v11 transition")
    writer = _require_mapping(
        transition.get("writer_precondition"),
        label="v11 writer precondition",
    )
    implementation = writer.get("git_head")
    if (
        not isinstance(implementation, str)
        or re.fullmatch(r"[0-9a-f]{40}", implementation) is None
    ):
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The v11 implementation Git commit is invalid."
        )
    _implementation_delta(root, implementation)
    _require_fixed_history(root, current_head=implementation)
    predecessor, predecessor_raw = _v10_predecessor(root)
    plan_at_implementation, _, _ = _git_regular_blob(
        root,
        commit=implementation,
        relative_path=PLAN_PATH,
    )
    if plan_at_implementation != predecessor_raw:
        raise MulticityPlanPredictorContractTransitionV11Error(
            "PLAN_READINESS changed in the v11/V2 implementation commit."
        )
    code_paths, source_records, scope, decision_output, _ = _decision_contract(root)
    terminal, authenticated_source_records = _validate_source_records(
        root,
        source_records,
    )
    if not _strict_equal(authenticated_source_records, source_records):
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The V2 runtime source records differ from historical source evidence."
        )
    code_files = _code_records_at_commit(
        root,
        commit=implementation,
        code_paths=code_paths,
    )
    _validate_authorization_scope(
        root,
        implementation_commit=implementation,
        code_paths=code_paths,
        code_files=code_files,
        source_records=source_records,
        scope=scope,
        decision_output_path=decision_output,
    )
    expected = _build_v11_payload(
        predecessor,
        predecessor_bytes=len(predecessor_raw),
        terminal=terminal,
        source_records=source_records,
        implementation_commit=implementation,
        code_files=code_files,
        authorization_scope=scope,
    )
    _validate_exact_v11_payload(payload, expected)
    if publication_commit is not None:
        if re.fullmatch(r"[0-9a-f]{40}", publication_commit) is None:
            raise MulticityPlanPredictorContractTransitionV11Error(
                "The v11 publication Git commit is invalid."
            )
        published_raw, _, _ = _git_regular_blob(
            root,
            commit=publication_commit,
            relative_path=PLAN_PATH,
        )
        published = _json_from_bytes(
            published_raw,
            label=f"{publication_commit}:{PLAN_PATH}",
        )
        if not _strict_equal(published, payload):
            raise MulticityPlanPredictorContractTransitionV11Error(
                "The supplied v11 payload differs from its publication blob."
            )
        end = publication_commit if current_head is None else current_head
        _require_fixed_history(root, current_head=end)
        _require_v11_history(
            root,
            publication_commit=publication_commit,
            implementation_commit=implementation,
            published_raw=published_raw,
            current_head=end,
        )
        observed_code = _code_records_at_commit(
            root,
            commit=publication_commit,
            code_paths=code_paths,
        )
        if not _strict_equal(observed_code, code_files):
            raise MulticityPlanPredictorContractTransitionV11Error(
                "The v11 publication changed an authorized V2 runtime blob."
            )
        _require_runtime_unchanged(
            root,
            implementation_commit=implementation,
            current_head=end,
            code_paths=code_paths,
        )
    return deepcopy(dict(payload))


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def authorize_multicity_predictor_contract_freeze_v2(
    *,
    project_root: str | Path | None = None,
    output_path: str | Path = PLAN_PATH,
    write: bool = True,
) -> dict[str, Any]:
    """Create or authenticate the exact tracked-only planning-v11 transition."""

    root = _default_project_root() if project_root is None else Path(project_root)
    root = root.resolve()
    destination = Path(output_path)
    if not destination.is_absolute():
        destination = root / destination
    if destination.resolve() != (root / PLAN_PATH).resolve():
        raise MulticityPlanPredictorContractTransitionV11Error(
            "The v11 transition may replace only canonical PLAN_READINESS.json."
        )
    predecessor, predecessor_raw = _v10_predecessor(root)
    (
        code_paths,
        source_records,
        scope,
        decision_output,
        import_head,
    ) = _decision_contract(root)
    required = tuple(
        dict.fromkeys((*code_paths, PLAN_PATH, *SOURCE_TRACKED_OUTPUT_PATHS))
    )
    head = _git_preflight(
        root,
        required_paths=required,
        expected_head=import_head,
    )
    current, current_raw = _read_current_json(destination)
    if _sha256_bytes(current_raw) == V10_FILE_SHA256:
        if not write:
            raise MulticityPlanPredictorContractTransitionV11Error(
                "PLAN_READINESS is still v10; planning v11 has not been written."
            )
        if current_raw != predecessor_raw:
            raise MulticityPlanPredictorContractTransitionV11Error(
                "Current v10 bytes differ from the canonical historical blob."
            )
        _implementation_delta(root, head)
        _require_fixed_history(root, current_head=head)
        terminal, authenticated_source_records = _validate_source_records(
            root,
            source_records,
        )
        if not _strict_equal(authenticated_source_records, source_records):
            raise MulticityPlanPredictorContractTransitionV11Error(
                "The V2 runtime source records differ from historical source evidence."
            )
        code_files = _code_records_at_commit(
            root,
            commit=head,
            code_paths=code_paths,
        )
        _validate_authorization_scope(
            root,
            implementation_commit=head,
            code_paths=code_paths,
            code_files=code_files,
            source_records=source_records,
            scope=scope,
            decision_output_path=decision_output,
        )
        payload = _build_v11_payload(
            predecessor,
            predecessor_bytes=len(predecessor_raw),
            terminal=terminal,
            source_records=source_records,
            implementation_commit=head,
            code_files=code_files,
            authorization_scope=scope,
        )
        _git_preflight(root, required_paths=required, expected_head=head)
        if destination.read_bytes() != predecessor_raw:
            raise MulticityPlanPredictorContractTransitionV11Error(
                "PLAN_READINESS changed before the v11 write boundary."
            )
        temporary = destination.with_name(
            f".{destination.name}.{uuid.uuid4().hex}.partial"
        )
        try:
            with temporary.open("xb") as handle:
                handle.write(_expected_json_bytes(payload))
                handle.flush()
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return payload
    publication = _locate_v11_publication_commit(root, current, current_head=head)
    authenticated = authenticate_historical_v11_payload(
        root,
        current,
        publication_commit=publication,
        current_head=head,
    )
    _git_preflight(root, required_paths=required, expected_head=head)
    if destination.read_bytes() != _expected_json_bytes(authenticated):
        raise MulticityPlanPredictorContractTransitionV11Error(
            "Current v11 bytes changed after authentication."
        )
    return authenticated
