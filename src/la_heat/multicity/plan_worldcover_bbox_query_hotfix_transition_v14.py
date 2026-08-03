"""Planning V14: authorize the preregistered WorldCover bbox query and resume."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
import uuid
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, Final

from la_heat.multicity import (
    plan_missing_support_calibration_hotfix_transition_v13 as v13,
)
from la_heat.provenance import canonical_sha256

SCHEMA_VERSION: Final = 14
ALGORITHM_VERSION: Final = "multicity-planning-readiness-v14"
PLANNING_STAGE: Final = (
    "missing_support_calibration_evidence_v1_worldcover_bbox_query_"
    "hotfix_resume_authorized"
)
NEXT_SAFE_STAGE: Final = (
    "stage_target_blind_missing_support_and_calibration_evidence_v1"
)
PLAN_PATH: Final = v13.PLAN_PATH
IMPLEMENTATION_BASE_COMMIT: Final = "0a83032c73c2cfe2b925278702970b3ab74551bb"
V13_PUBLICATION_COMMIT: Final = IMPLEMENTATION_BASE_COMMIT
V13_IMPLEMENTATION_COMMIT: Final = "823a67cda98f6b824d13e9150b7ab6b94471445b"
V13_BYTES: Final = 60_178
V13_FILE_SHA256: Final = (
    "ab1e361eb1f395764754a0adecff42f2ec5220642d0157d2e6d172d8ba555d6b"
)
V13_INTERNAL_COMMIT_SHA256: Final = (
    "95c223b2abe826f9121aa451e4ea9e06fa459b1b8aa27c31c8be59f24f51d7de"
)
OLD_CONFIG_SHA256: Final = (
    "11f298c9f3020154286af475c5c336fadde9519cb75e9a553d5c10bc7b761350"
)

CONFIG_PATH: Final = v13.CONFIG_PATH
EXECUTOR_MODULE_PATH: Final = v13.EXECUTOR_MODULE_PATH
WORLDCOVER_MODULE_PATH: Final = v13.WORLDCOVER_MODULE_PATH
SENTINEL_MODULE_PATH: Final = v13.SENTINEL_MODULE_PATH
WORLDCOVER_TEST_PATH: Final = v13.WORLDCOVER_TEST_PATH
TRANSITION_MODULE_PATH: Final = (
    "src/la_heat/multicity/plan_worldcover_bbox_query_hotfix_transition_v14.py"
)
AUTHORIZATION_SCRIPT_PATH: Final = (
    "scripts/authorize_multicity_worldcover_bbox_query_hotfix_v14.py"
)
TRANSITION_TEST_PATH: Final = (
    "tests/test_multicity_plan_worldcover_bbox_query_hotfix_transition_v14.py"
)

EXPECTED_IMPLEMENTATION_DELTA: Final = frozenset(
    {
        ("M", CONFIG_PATH),
        ("M", EXECUTOR_MODULE_PATH),
        ("M", SENTINEL_MODULE_PATH),
        ("M", WORLDCOVER_MODULE_PATH),
        ("M", WORLDCOVER_TEST_PATH),
        ("A", TRANSITION_MODULE_PATH),
        ("A", AUTHORIZATION_SCRIPT_PATH),
        ("A", TRANSITION_TEST_PATH),
    }
)

WORLDCOVER_RESUME_CHECKPOINTS: Final = (
    {
        "path": (
            "manifests/multicity/cities/los_angeles_ca/eligible_support/"
            "WORLDCOVER_ELIGIBLE_SUPPORT_V1.json"
        ),
        "bytes": 6_122,
        "file_sha256": (
            "cfc228675524b20adf36935cc4a240c995af0d3437a576e07de29e6f985f1258"
        ),
        "commit_sha256": (
            "4fc8323acc0eca002a6d8fc82f1cc9bca721076dd50b7027efe3a3769f0c685e"
        ),
        "state": "complete_target_blind_city_worldcover_support",
        "city_id": "los_angeles_ca",
    },
    {
        "path": (
            "manifests/multicity/cities/phoenix_az/eligible_support/"
            "WORLDCOVER_ELIGIBLE_SUPPORT_V1.json"
        ),
        "bytes": 6_059,
        "file_sha256": (
            "eb9309cb120668551090f50ea7ad5a7bc75d41afaa697de162a8454491787e04"
        ),
        "commit_sha256": (
            "7eb0196636025ef307c7d0c9371139a3fb1b4161949b2751a24981a77fd3aa66"
        ),
        "state": "complete_target_blind_city_worldcover_support",
        "city_id": "phoenix_az",
    },
)
RESUME_CHECKPOINTS: Final = (
    *v13.RESUME_CHECKPOINTS,
    *WORLDCOVER_RESUME_CHECKPOINTS,
)
RESUME_CHECKPOINT_PATHS: Final = tuple(
    str(record["path"]) for record in RESUME_CHECKPOINTS
)

AUTHORIZED_FIX: Final = {
    "worldcover_stac_candidate_query_before": "full_city_polygon_intersects",
    "worldcover_stac_candidate_query_after": "bbox_only",
    "preregistered_candidate_query": (
        "bbox_only_then_exact_positive_polygon_intersection"
    ),
    "exact_positive_polygon_item_selection_after_query": True,
    "selected_item_rule_changed": False,
    "server_failure_status": 413,
    "completed_worldcover_city_checkpoints_preserved": [
        "los_angeles_ca",
        "phoenix_az",
    ],
    "completed_asset_cache_objects_preserved": 2,
    "worldcover_asset_path_authorization_changed": False,
    "collection_year_version_or_asset_changed": False,
    "tracked_output_paths_changed": False,
    "permissions_changed": False,
    "locks_changed": False,
    "conflicting_next_plan_version_replaced": "v14",
    "successful_evidence_next_plan_version": "v15",
}

TRANSITION_ACCESS_CONTRACT: Final = {
    "network_requests": 0,
    "tracked_code_configuration_and_git_blobs_read": True,
    "exact_target_blind_checkpoint_manifests_read": True,
    "raw_source_or_raster_payloads_opened": False,
    "predictor_values_opened_or_computed": False,
    "model_fit_or_prediction_performed": False,
    "external_target_or_qa_values_read": False,
    "landsat_thermal_values_read": False,
    "final_evaluation_outputs_opened": False,
}


class MulticityPlanWorldCoverBboxQueryHotfixV14Error(ValueError):
    """Raised when the exact V14 resume transition fails authentication."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _expected_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(payload), indent=2).encode("utf-8")


def _json_bytes(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
            f"Cannot parse authenticated JSON: {label}"
        ) from exc
    if not isinstance(payload, dict):
        raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
            f"Authenticated JSON is not an object: {label}"
        )
    recorded = payload.get("commit_sha256")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or canonical_sha256(body) != recorded:
        raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
            f"Authenticated JSON internal commit is invalid: {label}"
        )
    return payload


def _run_git(
    project_root: Path, *arguments: str, binary: bool = False
) -> str | bytes:
    completed = subprocess.run(
        ["git", "-C", str(project_root), *arguments],
        check=False,
        capture_output=True,
        text=not binary,
    )
    if completed.returncode:
        stderr = (
            completed.stderr.decode("utf-8", errors="replace")
            if binary
            else completed.stderr
        )
        raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
            f"Git authentication failed for {' '.join(arguments)}: {stderr.strip()}"
        )
    return completed.stdout


def _git_blob(
    project_root: Path, *, commit: str, relative_path: str
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
        raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
            f"Required path is not one regular Git blob: {relative_path}"
        )
    raw = _run_git(project_root, "show", f"{commit}:{relative_path}", binary=True)
    assert isinstance(raw, bytes)
    return raw, parts[2], parts[0]


def _parse_delta(raw: bytes) -> frozenset[tuple[str, str]]:
    fields = raw.split(b"\0")
    if fields[-1:] != [b""] or len(fields[:-1]) % 2:
        raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
            "Git delta is not valid NUL-delimited status/path output."
        )
    return frozenset(
        (
            fields[index].decode("ascii"),
            fields[index + 1].decode("utf-8"),
        )
        for index in range(0, len(fields) - 1, 2)
    )


def _parse_status(raw: bytes) -> frozenset[str]:
    fields = raw.split(b"\0")
    if fields[-1:] != [b""]:
        raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
            "Git status is not NUL terminated."
        )
    observed: set[str] = set()
    for field in fields[:-1]:
        if not field.startswith(b"?? "):
            raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
                "V14 permits only append-only untracked resume checkpoints."
            )
        path = field[3:].decode("utf-8")
        if path not in RESUME_CHECKPOINT_PATHS or path in observed:
            raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
                f"Unexpected dirty path at V14 boundary: {path}"
            )
        observed.add(path)
    return frozenset(observed)


def _verify_resume_checkpoints(project_root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for expected in RESUME_CHECKPOINTS:
        relative = str(expected["path"])
        path = project_root / relative
        if not path.is_file() or path.is_symlink():
            raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
                f"Resume checkpoint is missing: {relative}"
            )
        raw = path.read_bytes()
        payload = _json_bytes(raw, label=relative)
        observed: dict[str, Any] = {
            "path": relative,
            "bytes": len(raw),
            "file_sha256": _sha(raw),
            "commit_sha256": payload["commit_sha256"],
            "state": payload.get("state"),
        }
        if "city_id" in expected:
            observed["city_id"] = payload.get("city_id")
        if observed != expected:
            raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
                f"Resume checkpoint changed: {relative}"
            )
        records.append(observed)
    return records


def _implementation_delta(project_root: Path, implementation: str) -> None:
    parents = str(
        _run_git(project_root, "rev-list", "--parents", "-n", "1", implementation)
    ).split()
    if parents != [implementation, IMPLEMENTATION_BASE_COMMIT]:
        raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
            "V14 implementation is not the V13 publication's direct child."
        )
    raw = _run_git(
        project_root,
        "diff-tree",
        "--no-commit-id",
        "--name-status",
        "-r",
        "-z",
        "--no-renames",
        IMPLEMENTATION_BASE_COMMIT,
        implementation,
        binary=True,
    )
    assert isinstance(raw, bytes)
    if _parse_delta(raw) != EXPECTED_IMPLEMENTATION_DELTA:
        raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
            "V14 implementation changed a path outside its exact allowlist."
        )


def _preflight(project_root: Path, *, allow_clean: bool = False) -> str:
    branch = str(_run_git(project_root, "branch", "--show-current")).strip()
    head = str(_run_git(project_root, "rev-parse", "HEAD")).strip()
    origin = str(_run_git(project_root, "rev-parse", "origin/main")).strip()
    status = _run_git(
        project_root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        binary=True,
    )
    assert isinstance(status, bytes)
    if branch != "main" or head != origin:
        raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
            "V14 requires synchronized main."
        )
    observed_status = _parse_status(status)
    permitted_statuses = {frozenset(RESUME_CHECKPOINT_PATHS)}
    if allow_clean:
        permitted_statuses.add(frozenset())
    if observed_status not in permitted_statuses:
        raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
            "V14 requires either its exact seven untracked resume checkpoints "
            "or a clean post-publication worktree."
        )
    _verify_resume_checkpoints(project_root)
    return head


def _executor_contract(
    project_root: Path, *, implementation: str
) -> tuple[tuple[str, ...], dict[str, Any], dict[str, bool]]:
    _implementation_delta(project_root, implementation)
    for relative in (CONFIG_PATH, EXECUTOR_MODULE_PATH):
        _, oid, _ = _git_blob(
            project_root, commit=implementation, relative_path=relative
        )
        worktree_oid = str(
            _run_git(
                project_root,
                "hash-object",
                f"--path={relative}",
                "--",
                relative,
            )
        ).strip()
        if worktree_oid != oid:
            raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
                f"V14 executor input differs from HEAD: {relative}"
            )
    module = importlib.import_module(
        "la_heat.multicity.missing_support_calibration_evidence_v1"
    )
    code_paths = tuple(module.CODE_PATHS)
    scope = deepcopy(module.expected_plan_authorization_scope())
    authorized = deepcopy(module.expected_authorized_now())
    if scope.get("configuration") != {
        "path": CONFIG_PATH,
        "sha256": module.CONFIG_SHA256,
    }:
        raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
            "V14 configuration identity changed."
        )
    if sum(bool(value) for value in authorized.values()) != 1:
        raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
            "V14 executor permission map changed."
        )
    return code_paths, scope, authorized


def transition_code_paths(executor_paths: tuple[str, ...]) -> tuple[str, ...]:
    """Return runtime and transition files frozen at V14 implementation."""

    return tuple(
        dict.fromkeys(
            (
                *executor_paths,
                v13.TRANSITION_MODULE_PATH,
                TRANSITION_MODULE_PATH,
                AUTHORIZATION_SCRIPT_PATH,
            )
        )
    )


def _code_records(
    project_root: Path, *, commit: str, paths: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for relative in paths:
        raw, oid, mode = _git_blob(
            project_root, commit=commit, relative_path=relative
        )
        records[relative] = {
            "sha256": _sha(raw),
            "bytes": len(raw),
            "git_blob_oid": oid,
            "git_mode": mode,
        }
    return records


def _historical_v13(project_root: Path) -> tuple[dict[str, Any], bytes]:
    raw, _, _ = _git_blob(
        project_root,
        commit=V13_PUBLICATION_COMMIT,
        relative_path=PLAN_PATH,
    )
    if len(raw) != V13_BYTES or _sha(raw) != V13_FILE_SHA256:
        raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
            "Historical planning V13 bytes changed."
        )
    payload = _json_bytes(raw, label="historical planning V13")
    scope = payload.get(
        "missing_support_calibration_evidence_v1_authorization_scope", {}
    )
    if (
        payload.get("schema_version") != 13
        or payload.get("algorithm_version") != "multicity-planning-readiness-v13"
        or payload.get("commit_sha256") != V13_INTERNAL_COMMIT_SHA256
        or scope.get("configuration")
        != {"path": CONFIG_PATH, "sha256": OLD_CONFIG_SHA256}
        or payload.get("authorized_now", {}).get(
            "portable_predictor_missing_support_and_calibration_evidence_staging"
        )
        is not True
        or payload.get("transition", {}).get("authorized_fix") != v13.AUTHORIZED_FIX
    ):
        raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
            "Historical planning V13 contract changed."
        )
    return payload, raw


def _build_payload(
    predecessor: Mapping[str, Any],
    *,
    implementation: str,
    code_files: Mapping[str, Any],
    transition_code_files: Mapping[str, Any],
    authorization_scope: Mapping[str, Any],
    authorized_now: Mapping[str, bool],
) -> dict[str, Any]:
    payload = deepcopy(dict(predecessor))
    previous_transition = payload.pop("transition")
    payload.pop("commit_sha256")
    payload["schema_version"] = SCHEMA_VERSION
    payload["algorithm_version"] = ALGORITHM_VERSION
    payload["planning_stage"] = PLANNING_STAGE
    payload["next_safe_stage"] = NEXT_SAFE_STAGE
    payload["code_files"] = deepcopy(dict(code_files))
    payload["authorized_now"] = deepcopy(dict(authorized_now))
    payload[
        "missing_support_calibration_evidence_v1_authorization_scope"
    ] = deepcopy(dict(authorization_scope))
    payload["transition"] = {
        "id": "use_preregistered_worldcover_bbox_candidate_query_and_resume",
        "mode": "tracked_code_config_git_and_exact_target_blind_checkpoints_only",
        "predecessor_v13": {
            "path": PLAN_PATH,
            "publication_git_commit": V13_PUBLICATION_COMMIT,
            "implementation_git_commit": V13_IMPLEMENTATION_COMMIT,
            "bytes": V13_BYTES,
            "file_sha256": V13_FILE_SHA256,
            "commit_sha256": V13_INTERNAL_COMMIT_SHA256,
            "authorized_fix_sha256": canonical_sha256(v13.AUTHORIZED_FIX),
        },
        "consumed_v13_transition_sha256": canonical_sha256(previous_transition),
        "failure_evidence": {
            "completed_overall_tasks": 1,
            "completed_worldcover_city_checkpoints": 2,
            "failing_task": "four_city_worldcover_eligible_support",
            "failing_city": "houston_tx",
            "exception_type": "HTTPError",
            "http_status": 413,
            "failing_gate": "Planetary Computer STAC request body too large",
            "external_target_or_qa_values_read": False,
        },
        "authorized_fix": deepcopy(AUTHORIZED_FIX),
        "resume_checkpoints": deepcopy(list(RESUME_CHECKPOINTS)),
        "implementation": {
            "base_git_commit": IMPLEMENTATION_BASE_COMMIT,
            "implementation_git_commit": implementation,
            "delta": [
                {"status": status, "path": path}
                for status, path in sorted(EXPECTED_IMPLEMENTATION_DELTA)
            ],
        },
        "transition_code_files": deepcopy(dict(transition_code_files)),
        "writer_precondition": {
            "branch": "main",
            "git_head": implementation,
            "origin_main_equal": True,
            "allowed_untracked_paths": list(RESUME_CHECKPOINT_PATHS),
        },
        "access_contract": deepcopy(TRANSITION_ACCESS_CONTRACT),
    }
    if payload["locks"] != predecessor["locks"]:
        raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
            "V14 changed a scientific lock."
        )
    if payload["authorized_now"] != predecessor["authorized_now"]:
        raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
            "V14 changed a scientific permission."
        )
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def _publication_commit(
    project_root: Path, *, implementation: str, head: str
) -> str:
    changes = str(
        _run_git(
            project_root,
            "log",
            "--format=%H",
            f"{implementation}..{head}",
            "--",
            PLAN_PATH,
        )
    ).splitlines()
    if len(changes) != 1:
        raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
            "Planning V14 must have one exact publication commit."
        )
    publication = changes[0]
    parents = str(
        _run_git(project_root, "rev-list", "--parents", "-n", "1", publication)
    ).split()
    if parents != [publication, implementation]:
        raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
            "Planning V14 is not the hotfix implementation's direct child."
        )
    raw = _run_git(
        project_root,
        "diff-tree",
        "--no-commit-id",
        "--name-status",
        "-r",
        "-z",
        "--no-renames",
        implementation,
        publication,
        binary=True,
    )
    assert isinstance(raw, bytes)
    if _parse_delta(raw) != frozenset({("M", PLAN_PATH)}):
        raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
            "Planning V14 publication changed more than PLAN_READINESS.json."
        )
    return publication


def _atomic_replace(content: bytes, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.partial")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def authorize_multicity_worldcover_bbox_query_hotfix_v14(
    *,
    project_root: str | Path | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Create or authenticate planning V14 and its seven resume checkpoints."""

    root = (
        Path(project_root).resolve()
        if project_root is not None
        else Path(__file__).resolve().parents[3]
    )
    plan_path = root / PLAN_PATH
    predecessor, predecessor_raw = _historical_v13(root)
    current_raw = plan_path.read_bytes()
    writing_from_v13 = current_raw == predecessor_raw
    head = _preflight(root, allow_clean=not writing_from_v13)

    if writing_from_v13:
        if not write:
            raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
                "PLAN_READINESS is still V13."
            )
        implementation = head
        code_paths, scope, authorized = _executor_contract(
            root, implementation=implementation
        )
        code_files = _code_records(root, commit=implementation, paths=code_paths)
        transition_files = _code_records(
            root,
            commit=implementation,
            paths=transition_code_paths(code_paths),
        )
        payload = _build_payload(
            predecessor,
            implementation=implementation,
            code_files=code_files,
            transition_code_files=transition_files,
            authorization_scope=scope,
            authorized_now=authorized,
        )
        if plan_path.read_bytes() != predecessor_raw:
            raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
                "Planning changed before the V14 write boundary."
            )
        _atomic_replace(_expected_bytes(payload), plan_path)
        return payload

    observed = _json_bytes(current_raw, label="canonical planning V14")
    transition = observed.get("transition", {})
    implementation = transition.get("implementation", {}).get(
        "implementation_git_commit"
    )
    if not isinstance(implementation, str):
        raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
            "Planning V14 implementation identity is missing."
        )
    code_paths, scope, authorized = _executor_contract(
        root, implementation=implementation
    )
    code_files = _code_records(root, commit=implementation, paths=code_paths)
    transition_paths = transition_code_paths(code_paths)
    transition_files = _code_records(
        root, commit=implementation, paths=transition_paths
    )
    expected = _build_payload(
        predecessor,
        implementation=implementation,
        code_files=code_files,
        transition_code_files=transition_files,
        authorization_scope=scope,
        authorized_now=authorized,
    )
    if current_raw != _expected_bytes(expected) or observed != expected:
        raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
            "Canonical planning V14 bytes changed."
        )
    _publication_commit(root, implementation=implementation, head=head)
    for relative in transition_paths:
        history = str(
            _run_git(
                root,
                "log",
                "--format=%H",
                f"{implementation}..{head}",
                "--",
                relative,
            )
        )
        if history.strip():
            raise MulticityPlanWorldCoverBboxQueryHotfixV14Error(
                f"V14-authorized runtime changed after implementation: {relative}"
            )
    return observed
