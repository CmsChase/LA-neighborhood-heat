"""Authorize one audited runtime hotfix and resume source-evidence V1.

The first v9-authorized live run stopped before any static raster download or
source-footprint checkpoint because the sole non-geometry Parquet recorder
called ``canonical_frame_sha256`` without its required explicit ``sort_by``
argument.  This tracked-only transition authenticates the immutable v9
publication, binds the exact five-path implementation delta, preserves the two
append-only geography checkpoints already created under v9, and changes no
scientific permission, lock, source, feature rule, model, target, or result.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, Final

from la_heat.multicity.plan_source_evidence_transition_v9 import (
    PLAN_PATH,
    _canonical_sha256,
    _expected_json_bytes,
    _git_regular_blob,
    _historical_json,
    _is_ancestor,
    _require_exact_delta,
    _require_mapping,
    _run_git,
    _strict_equal,
    authenticate_historical_v9_payload,
)
from la_heat.multicity.portable_predictor_source_evidence_v1 import (
    CODE_PATHS as SOURCE_EVIDENCE_CODE_PATHS,
)
from la_heat.multicity.portable_predictor_source_evidence_v1 import (
    TRACKED_OUTPUT_PATHS,
    expected_authorized_now,
    expected_plan_authorization_scope,
)

SCHEMA_VERSION: Final = 10
ALGORITHM_VERSION: Final = "multicity-planning-readiness-v10"
PLANNING_STAGE: Final = (
    "portable_predictor_source_evidence_v1_runtime_hotfix_resume_authorized"
)
NEXT_SAFE_STAGE: Final = "resume_missing_portable_predictor_source_evidence_v1"

V9_PUBLICATION_COMMIT: Final = "85bef193c0f1d5fdf8f7333b1f763d80dda56741"
V9_PRECONDITION_COMMIT: Final = "ad81b404e85c5b84308cb41eea980694756e9125"
V9_FILE_SHA256: Final = "ad1ac16f221883f403da35ca1011c247eac3a5d5f1253d36dddc0ad85b14ec20"
V9_INTERNAL_COMMIT_SHA256: Final = (
    "40984be17b59c1ace7481bf5e34f64bd567a42f9004970bdaa1111bb0a52cf68"
)
V9_BYTES: Final = 36_745

V9_MODULE_PATH: Final = "src/la_heat/multicity/plan_source_evidence_transition_v9.py"
V9_SCRIPT_PATH: Final = "scripts/authorize_multicity_source_evidence_stage.py"
V10_MODULE_PATH: Final = (
    "src/la_heat/multicity/plan_source_evidence_hotfix_transition_v10.py"
)
V10_SCRIPT_PATH: Final = "scripts/authorize_multicity_source_evidence_hotfix_resume.py"
SOURCE_MODULE_PATH: Final = (
    "src/la_heat/multicity/portable_predictor_source_evidence_v1.py"
)
SOURCE_TEST_PATH: Final = "tests/test_multicity_portable_predictor_source_evidence_v1.py"
V9_TEST_PATH: Final = "tests/test_multicity_plan_source_evidence_transition_v9.py"
V10_TEST_PATH: Final = (
    "tests/test_multicity_plan_source_evidence_hotfix_transition_v10.py"
)

EXPECTED_IMPLEMENTATION_DELTA: Final = frozenset(
    {
        ("M", SOURCE_MODULE_PATH),
        ("M", SOURCE_TEST_PATH),
        ("M", V9_TEST_PATH),
        ("A", V10_MODULE_PATH),
        ("A", V10_SCRIPT_PATH),
        ("A", V10_TEST_PATH),
    }
)

RESUME_CHECKPOINTS: Final = (
    {
        "path": "manifests/multicity/cities/houston_tx/geography/GEOGRAPHY.json",
        "bytes": 5_523,
        "file_sha256": "f89fd246854baa670ecc7a32c20672922be5327e1b89abce8caa07a9ccb14906",
        "commit_sha256": "1c00bb64c1eaa6ce83fe44b377fd40235dfe4108ce8535839cb94a7caf43711f",
        "state": "complete_target_blind_public_geography",
    },
    {
        "path": "manifests/multicity/cities/chicago_il/geography/GEOGRAPHY.json",
        "bytes": 5_520,
        "file_sha256": "6fadf3355dda0ca70a5ef0f6201f84d38f8df29a588bdddc6fac076238e50b25",
        "commit_sha256": "10a2f41134becd5e45697fa0e95c43e58665155c39a8ec02391c7003e0b01c15",
        "state": "complete_target_blind_public_geography",
    },
)
RESUME_CHECKPOINT_PATHS: Final = tuple(
    str(record["path"]) for record in RESUME_CHECKPOINTS
)

TRANSITION_ACCESS_CONTRACT: Final = {
    "network_requests": 0,
    "local_git_metadata_and_historical_blobs_read": True,
    "tracked_configuration_and_code_files_hashed": True,
    "exact_untracked_geography_checkpoint_manifests_read": True,
    "raw_public_source_payloads_opened": False,
    "static_raster_payloads_opened": False,
    "predictor_values_opened_or_computed": False,
    "model_fit_or_prediction_performed": False,
    "external_target_or_qa_values_opened": False,
    "final_evaluation_outputs_opened": False,
}


class MulticityPlanSourceEvidenceHotfixTransitionV10Error(ValueError):
    """Raised when the exact v10 hotfix transition cannot authenticate."""


def transition_code_paths() -> tuple[str, ...]:
    """Return every runtime file whose post-hotfix history must remain fixed."""

    return tuple(
        dict.fromkeys(
            (
                *SOURCE_EVIDENCE_CODE_PATHS,
                V9_MODULE_PATH,
                V9_SCRIPT_PATH,
                V10_MODULE_PATH,
                V10_SCRIPT_PATH,
            )
        )
    )


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_from_bytes(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            f"Cannot parse {label}."
        ) from exc
    if not isinstance(payload, dict):
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            f"{label} must be a JSON object."
        )
    return payload


def _read_current_json(path: Path) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "Canonical planning is not one regular file."
        )
    before = path.read_bytes()
    after = path.read_bytes()
    if before != after:
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "Canonical planning changed while read."
        )
    payload = _json_from_bytes(before, label="canonical planning")
    recorded = payload.get("commit_sha256")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or _canonical_sha256(body) != recorded:
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "Canonical planning internal commit is invalid."
        )
    return payload, before


def _validate_v9(payload: Mapping[str, Any], raw: bytes) -> None:
    expected = {
        "schema_version": 9,
        "algorithm_version": "multicity-planning-readiness-v9",
        "state": "planning_ready",
        "planning_stage": (
            "portable_predictor_contract_v1_deferred_"
            "missing_source_evidence_stage_authorized"
        ),
        "next_safe_stage": "stage_missing_portable_predictor_source_evidence_v1",
        "commit_sha256": V9_INTERNAL_COMMIT_SHA256,
    }
    if len(raw) != V9_BYTES or _sha256_bytes(raw) != V9_FILE_SHA256:
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "Canonical planning-v9 bytes changed."
        )
    for key, value in expected.items():
        if not _strict_equal(payload.get(key), value):
            raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
                f"Canonical planning-v9 field changed: {key}"
            )
    if not _strict_equal(payload.get("authorized_now"), expected_authorized_now()):
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "Canonical planning-v9 permissions changed."
        )
    if not _strict_equal(
        payload.get("portable_predictor_source_evidence_stage_authorization_scope"),
        expected_plan_authorization_scope(),
    ):
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "Canonical planning-v9 source-evidence scope changed."
        )


def _verify_resume_checkpoints(project_root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for expected in RESUME_CHECKPOINTS:
        relative = str(expected["path"])
        path = project_root / relative
        if path.is_symlink() or not path.is_file():
            raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
                f"Authorized resume checkpoint is missing: {relative}"
            )
        raw = path.read_bytes()
        payload = _json_from_bytes(raw, label=relative)
        recorded = payload.get("commit_sha256")
        body = {key: value for key, value in payload.items() if key != "commit_sha256"}
        observed = {
            "path": relative,
            "bytes": len(raw),
            "file_sha256": _sha256_bytes(raw),
            "commit_sha256": recorded,
            "state": payload.get("state"),
        }
        if (
            not isinstance(recorded, str)
            or _canonical_sha256(body) != recorded
            or not _strict_equal(observed, expected)
        ):
            raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
                f"Authorized resume checkpoint changed: {relative}"
            )
        records.append(observed)
    return records


def _parse_status_paths(raw: bytes) -> set[str]:
    fields = raw.split(b"\0")
    if fields[-1:] != [b""]:
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "Git status is not valid NUL-delimited output."
        )
    observed: set[str] = set()
    allowed = set(TRACKED_OUTPUT_PATHS)
    for field in fields[:-1]:
        if not field.startswith(b"?? "):
            raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
                "The v10 transition permits only append-only untracked outputs."
            )
        try:
            relative = field[3:].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
                "Git status contains a non-UTF-8 path."
            ) from exc
        if relative not in allowed or relative in observed:
            raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
                f"Unexpected dirty path at the v10 boundary: {relative}"
            )
        observed.add(relative)
    return observed


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
    head = head.strip()
    if branch.strip() != "main" or head != origin.strip():
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "The v10 transition requires synchronized main."
        )
    if expected_head is not None and head != expected_head:
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "HEAD changed between v10 transition gates."
        )
    untracked = _parse_status_paths(status)
    if untracked and untracked != set(RESUME_CHECKPOINT_PATHS):
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "Only the two exact v9 geography checkpoints may precede v10 publication."
        )
    _verify_resume_checkpoints(project_root)
    for relative in required_paths:
        path = project_root / relative
        if path.is_symlink() or not path.is_file():
            raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
                f"A required v10 input is not one regular file: {relative}"
            )
        _, oid, _ = _git_regular_blob(
            project_root,
            commit=head,
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
            raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
                f"A required v10 input differs from HEAD: {relative}"
            )
    return head


def _implementation_delta(project_root: Path, precondition: str) -> None:
    ancestry = _run_git(project_root, "rev-list", "--parents", "-n", "1", precondition)
    assert isinstance(ancestry, str)
    if ancestry.split() != [precondition, V9_PUBLICATION_COMMIT]:
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "The hotfix implementation must be the direct child of planning v9."
        )
    raw = _run_git(
        project_root,
        "diff-tree",
        "--no-commit-id",
        "--name-status",
        "-r",
        "-z",
        "--no-renames",
        V9_PUBLICATION_COMMIT,
        precondition,
        binary=True,
    )
    assert isinstance(raw, bytes)
    fields = raw.split(b"\0")
    if fields[-1:] != [b""] or len(fields[:-1]) % 2:
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "The hotfix implementation delta is malformed."
        )
    try:
        pairs = frozenset(
            (
                fields[index].decode("ascii"),
                fields[index + 1].decode("utf-8"),
            )
            for index in range(0, len(fields) - 1, 2)
        )
    except UnicodeDecodeError as exc:
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "The hotfix delta contains noncanonical text."
        ) from exc
    if len(pairs) != len(EXPECTED_IMPLEMENTATION_DELTA) or pairs != (
        EXPECTED_IMPLEMENTATION_DELTA
    ):
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "The hotfix implementation changed a path outside its exact allowlist."
        )


def _code_records_at_commit(
    project_root: Path,
    *,
    commit: str,
) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for relative in transition_code_paths():
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


def _build_v10_payload(
    predecessor: Mapping[str, Any],
    *,
    predecessor_bytes: int,
    precondition_commit: str,
    code_files: Mapping[str, Mapping[str, object]],
) -> dict[str, Any]:
    payload = deepcopy(dict(predecessor))
    old_transition = deepcopy(payload["transition"])
    payload.update(
        {
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "planning_stage": PLANNING_STAGE,
            "next_safe_stage": NEXT_SAFE_STAGE,
            "predecessor_v9": {
                "path": PLAN_PATH,
                "bytes": predecessor_bytes,
                "file_sha256": V9_FILE_SHA256,
                "commit_sha256": V9_INTERNAL_COMMIT_SHA256,
                "publication_git_commit": V9_PUBLICATION_COMMIT,
                "writer_precondition_git_commit": V9_PRECONDITION_COMMIT,
            },
            "transition": {
                "from_schema_version": 9,
                "to_schema_version": 10,
                "scope": "runtime_hotfix_only_permissions_and_locks_unchanged",
                "consumed_v9_transition": old_transition,
                "failure_evidence": {
                    "exception_type": "TypeError",
                    "failing_operation": (
                        "canonical_frame_sha256_for_non_geometry_"
                        "source_footprint_parquet"
                    ),
                    "failure_boundary": (
                        "before_source_footprint_manifest_and_before_static_raster_download"
                    ),
                    "root_cause": "required_explicit_sort_by_argument_omitted",
                },
                "authorized_fix": {
                    "use_explicit_sort_by": ["variable", "year", "concept_id"],
                    "apply_to_non_geometry_record_commit_and_verification": True,
                    "source_products_changed": False,
                    "candidate_feature_rules_changed": False,
                    "tracked_output_paths_changed": False,
                    "permissions_changed": False,
                    "locks_changed": False,
                },
                "resume_checkpoints": deepcopy(list(RESUME_CHECKPOINTS)),
                "implementation_delta": [
                    {"status": status, "path": path}
                    for status, path in sorted(EXPECTED_IMPLEMENTATION_DELTA)
                ],
                "writer_precondition": {
                    "branch": "main",
                    "git_head": precondition_commit,
                    "origin_main_equal": True,
                    "allowed_untracked_paths": list(RESUME_CHECKPOINT_PATHS),
                },
                "code_files": deepcopy(dict(code_files)),
                "access_contract": deepcopy(TRANSITION_ACCESS_CONTRACT),
            },
        }
    )
    payload.pop("commit_sha256", None)
    if not _strict_equal(payload["authorized_now"], predecessor["authorized_now"]):
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "The v10 hotfix changed scientific permissions."
        )
    if not _strict_equal(payload["locks"], predecessor["locks"]):
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "The v10 hotfix changed scientific locks."
        )
    if not _strict_equal(
        payload["portable_predictor_source_evidence_stage_authorization_scope"],
        predecessor["portable_predictor_source_evidence_stage_authorization_scope"],
    ):
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "The v10 hotfix changed the authorized source-evidence scope."
        )
    payload["commit_sha256"] = _canonical_sha256(payload)
    return payload


def _validate_exact_v10_payload(
    observed: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> None:
    recorded = observed.get("commit_sha256")
    body = {key: value for key, value in observed.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or _canonical_sha256(body) != recorded:
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "The v10 planning internal commit is invalid."
        )
    if not _strict_equal(observed, expected):
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "The v10 planning record differs from its full reconstruction."
        )


def _require_v10_history(
    project_root: Path,
    *,
    publication_commit: str,
    precondition_commit: str,
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
    if ancestry.split() != [publication_commit, precondition_commit]:
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "The v10 publication is not the direct child of its implementation."
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
        f"{V9_PUBLICATION_COMMIT}..{publication_commit}",
        "--",
        PLAN_PATH,
    )
    assert isinstance(history, str)
    if [line for line in history.splitlines() if line] != [publication_commit]:
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "PLAN_READINESS changed outside the one v9-to-v10 publication."
        )
    if not _is_ancestor(project_root, publication_commit, current_head):
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "The v10 publication is not an ancestor of current HEAD."
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
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "PLAN_READINESS changed after v10 publication."
        )
    current_raw, _, _ = _git_regular_blob(
        project_root,
        commit=current_head,
        relative_path=PLAN_PATH,
    )
    if current_raw != published_raw:
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "Current PLAN_READINESS differs from v10 publication."
        )


def _require_runtime_unchanged(
    project_root: Path,
    *,
    precondition_commit: str,
    current_head: str,
) -> None:
    for relative in transition_code_paths():
        history = _run_git(
            project_root,
            "log",
            "--format=%H",
            f"{precondition_commit}..{current_head}",
            "--",
            relative,
        )
        assert isinstance(history, str)
        if history.strip():
            raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
                f"A v10-authorized runtime changed: {relative}"
            )


def _locate_v10_publication_commit(
    project_root: Path,
    payload: Mapping[str, Any],
    *,
    current_head: str,
) -> str:
    transition = _require_mapping(payload.get("transition"), label="v10 transition")
    writer = _require_mapping(
        transition.get("writer_precondition"),
        label="v10 writer precondition",
    )
    precondition = writer.get("git_head")
    if not isinstance(precondition, str):
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "The v10 precondition Git commit is missing."
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
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "The exact v10 transition must have one unique direct publication."
        )
    return candidates[0]


def authenticate_historical_v10_payload(
    project_root: str | Path,
    payload: Mapping[str, Any],
    *,
    publication_commit: str | None = None,
    current_head: str | None = None,
) -> dict[str, Any]:
    """Reconstruct v10 from immutable v9, the hotfix delta, and code blobs."""

    root = Path(project_root).resolve()
    transition = _require_mapping(payload.get("transition"), label="v10 transition")
    writer = _require_mapping(
        transition.get("writer_precondition"),
        label="v10 writer precondition",
    )
    precondition = writer.get("git_head")
    if not isinstance(precondition, str) or re.fullmatch(r"[0-9a-f]{40}", precondition) is None:
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "The v10 precondition Git commit is invalid."
        )
    _implementation_delta(root, precondition)
    predecessor, predecessor_raw = _historical_json(
        root,
        commit=V9_PUBLICATION_COMMIT,
        relative_path=PLAN_PATH,
    )
    _validate_v9(predecessor, predecessor_raw)
    try:
        authenticate_historical_v9_payload(
            root,
            predecessor,
            publication_commit=V9_PUBLICATION_COMMIT,
            current_head=V9_PUBLICATION_COMMIT,
        )
    except Exception as exc:
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "Historical planning v9 failed authentication."
        ) from exc
    plan_at_precondition, _, _ = _git_regular_blob(
        root,
        commit=precondition,
        relative_path=PLAN_PATH,
    )
    if plan_at_precondition != predecessor_raw:
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "PLAN_READINESS changed in the implementation hotfix commit."
        )
    _verify_resume_checkpoints(root)
    code_files = _code_records_at_commit(root, commit=precondition)
    expected = _build_v10_payload(
        predecessor,
        predecessor_bytes=len(predecessor_raw),
        precondition_commit=precondition,
        code_files=code_files,
    )
    _validate_exact_v10_payload(payload, expected)
    if publication_commit is not None:
        if re.fullmatch(r"[0-9a-f]{40}", publication_commit) is None:
            raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
                "The v10 publication Git commit is invalid."
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
            raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
                "The supplied v10 payload differs from its publication blob."
            )
        end = publication_commit if current_head is None else current_head
        _require_v10_history(
            root,
            publication_commit=publication_commit,
            precondition_commit=precondition,
            published_raw=published_raw,
            current_head=end,
        )
        if not _strict_equal(
            _code_records_at_commit(root, commit=publication_commit),
            code_files,
        ):
            raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
                "The v10 publication changed an authorized runtime blob."
            )
        _require_runtime_unchanged(
            root,
            precondition_commit=precondition,
            current_head=end,
        )
    return deepcopy(dict(payload))


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def authorize_multicity_source_evidence_hotfix_resume(
    *,
    project_root: str | Path | None = None,
    output_path: str | Path = PLAN_PATH,
    write: bool = True,
) -> dict[str, Any]:
    """Create or authenticate the exact planning-v10 runtime hotfix."""

    root = _default_project_root() if project_root is None else Path(project_root)
    root = root.resolve()
    destination = Path(output_path)
    if not destination.is_absolute():
        destination = root / destination
    if destination.resolve() != (root / PLAN_PATH).resolve():
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "The v10 transition may replace only canonical PLAN_READINESS.json."
        )
    predecessor, predecessor_raw = _historical_json(
        root,
        commit=V9_PUBLICATION_COMMIT,
        relative_path=PLAN_PATH,
    )
    _validate_v9(predecessor, predecessor_raw)
    required = tuple(dict.fromkeys((*transition_code_paths(), PLAN_PATH)))
    head = _git_preflight(root, required_paths=required)
    current, current_raw = _read_current_json(destination)
    if _sha256_bytes(current_raw) == V9_FILE_SHA256:
        if not write:
            raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
                "PLAN_READINESS is still v9; planning v10 has not been written."
            )
        if current_raw != predecessor_raw:
            raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
                "Current v9 bytes differ from the canonical historical blob."
            )
        authenticate_historical_v9_payload(
            root,
            predecessor,
            publication_commit=V9_PUBLICATION_COMMIT,
            current_head=V9_PUBLICATION_COMMIT,
        )
        _implementation_delta(root, head)
        code_files = _code_records_at_commit(root, commit=head)
        payload = _build_v10_payload(
            predecessor,
            predecessor_bytes=len(predecessor_raw),
            precondition_commit=head,
            code_files=code_files,
        )
        _git_preflight(root, required_paths=required, expected_head=head)
        if destination.read_bytes() != predecessor_raw:
            raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
                "PLAN_READINESS changed before the v10 write boundary."
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
    publication = _locate_v10_publication_commit(root, current, current_head=head)
    authenticated = authenticate_historical_v10_payload(
        root,
        current,
        publication_commit=publication,
        current_head=head,
    )
    _git_preflight(root, required_paths=required, expected_head=head)
    if destination.read_bytes() != _expected_json_bytes(authenticated):
        raise MulticityPlanSourceEvidenceHotfixTransitionV10Error(
            "Current v10 bytes changed after authentication."
        )
    return authenticated
