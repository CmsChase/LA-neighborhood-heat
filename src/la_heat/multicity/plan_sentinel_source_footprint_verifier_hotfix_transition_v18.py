"""Planning V18: use the frozen source-footprint verifier for each city."""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, Final

from la_heat.multicity import (
    plan_sentinel_stac_calibration_metadata_hotfix_transition_v17 as v17,
)
from la_heat.provenance import canonical_sha256

SCHEMA_VERSION: Final = 18
ALGORITHM_VERSION: Final = "multicity-planning-readiness-v18"
PLANNING_STAGE: Final = (
    "missing_support_calibration_evidence_v1_source_footprint_verifier_hotfix_resume_authorized"
)
NEXT_SAFE_STAGE: Final = "stage_target_blind_missing_support_and_calibration_evidence_v1"
PLAN_PATH: Final = v17.PLAN_PATH
IMPLEMENTATION_BASE_COMMIT: Final = "6dd1a7998c048e54668e8fc531089d70a081144d"
V17_PUBLICATION_COMMIT: Final = IMPLEMENTATION_BASE_COMMIT
V17_IMPLEMENTATION_COMMIT: Final = "ca4442eda22798654f7da15c7618e7e030583451"
V17_BYTES: Final = 65_205
V17_FILE_SHA256: Final = "e8ae1b93bb884cc606a3e2e70df2f30a94a52c75b8800f04735069367d02e171"
V17_INTERNAL_COMMIT_SHA256: Final = (
    "8666329104dcd9ad46ca43465b782530ea2fe1984348592349229ba9481659d1"
)
OLD_CONFIG_SHA256: Final = "a3bc3611afa50933fa05ace3413b3bc328a9357341d64d59ca14bd3cac1c74cc"

CONFIG_PATH: Final = v17.CONFIG_PATH
EXECUTOR_MODULE_PATH: Final = v17.EXECUTOR_MODULE_PATH
SENTINEL_MODULE_PATH: Final = v17.SENTINEL_MODULE_PATH
SENTINEL_TEST_PATH: Final = v17.SENTINEL_TEST_PATH
V17_TRANSITION_TEST_PATH: Final = v17.TRANSITION_TEST_PATH
TRANSITION_MODULE_PATH: Final = (
    "src/la_heat/multicity/plan_sentinel_source_footprint_verifier_hotfix_transition_v18.py"
)
AUTHORIZATION_SCRIPT_PATH: Final = (
    "scripts/authorize_multicity_sentinel_source_footprint_verifier_hotfix_v18.py"
)
TRANSITION_TEST_PATH: Final = (
    "tests/test_multicity_plan_sentinel_source_footprint_verifier_hotfix_transition_v18.py"
)

EXPECTED_IMPLEMENTATION_DELTA: Final = frozenset(
    {
        ("M", CONFIG_PATH),
        ("M", EXECUTOR_MODULE_PATH),
        ("M", SENTINEL_MODULE_PATH),
        ("M", SENTINEL_TEST_PATH),
        ("M", V17_TRANSITION_TEST_PATH),
        ("A", TRANSITION_MODULE_PATH),
        ("A", AUTHORIZATION_SCRIPT_PATH),
        ("A", TRANSITION_TEST_PATH),
    }
)

PHOENIX_SENTINEL_RESUME_CHECKPOINT_V18: Final = {
    "path": (
        "manifests/multicity/cities/phoenix_az/sentinel_calibration_smoke/"
        "SENTINEL_CALIBRATION_SMOKE_V1.json"
    ),
    "bytes": 21_998,
    "file_sha256": ("d36074e08da1130f1ee8c56fe21b48942537a9865626efa95c935a9407e8c664"),
    "commit_sha256": ("1183bab7a5ebad26e809b9e3a7eacb1460a3c65f921bf6202615c79bd85b53ff"),
    "state": "complete_target_blind_city_sentinel_calibration_smoke",
    "city_id": "phoenix_az",
}
RESUME_CHECKPOINTS: Final = (
    *v17.RESUME_CHECKPOINTS,
    PHOENIX_SENTINEL_RESUME_CHECKPOINT_V18,
)
RESUME_CHECKPOINT_PATHS: Final = tuple(str(record["path"]) for record in RESUME_CHECKPOINTS)

AUTHORIZED_FIX: Final = {
    "source_footprint_verifier_before": (
        "metadata_discovery_authorization_verifier_for_all_external_cities"
    ),
    "source_footprint_verifier_after": (
        "phoenix_legacy_published_verifier_and_houston_chicago_source_evidence_v1_verifier"
    ),
    "existing_source_footprint_manifests_only": True,
    "source_footprint_rediscovery_performed": False,
    "source_metadata_table_or_values_changed": False,
    "network_requests_added": False,
    "failing_stage": "external_city_sentinel_calibration_smoke_v1",
    "completed_phoenix_sentinel_checkpoint_preserved": True,
    "completed_checkpoint_count": 11,
    "sentinel_probe_selection_changed": False,
    "sentinel_band_set_or_window_changed": False,
    "sentinel_decode_formula_changed": False,
    "stac_calibration_policy_changed": False,
    "collection_or_asset_changed": False,
    "network_endpoint_or_budget_changed": False,
    "tracked_output_paths_changed": False,
    "permissions_changed": False,
    "locks_changed": False,
    "conflicting_next_plan_version_replaced": "v18",
    "successful_evidence_next_plan_version": "v19",
}

TRANSITION_ACCESS_CONTRACT: Final = {
    "network_requests": 0,
    "tracked_code_configuration_and_git_blobs_read": True,
    "exact_target_blind_checkpoint_manifests_read": True,
    "raw_source_metadata_or_raster_payloads_opened": False,
    "predictor_values_opened_or_computed": False,
    "model_fit_or_prediction_performed": False,
    "external_target_or_qa_values_read": False,
    "landsat_thermal_values_read": False,
    "final_evaluation_outputs_opened": False,
}


class MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(ValueError):
    """Raised when the exact V18 resume transition fails authentication."""


def _parse_status(raw: bytes) -> frozenset[str]:
    fields = raw.split(b"\0")
    if fields[-1:] != [b""]:
        raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
            "Git status is not NUL terminated."
        )
    observed: set[str] = set()
    for field in fields[:-1]:
        if not field.startswith(b"?? "):
            raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
                "V18 permits only append-only untracked resume checkpoints."
            )
        path = field[3:].decode("utf-8")
        if path not in RESUME_CHECKPOINT_PATHS or path in observed:
            raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
                f"Unexpected dirty path at V18 boundary: {path}"
            )
        observed.add(path)
    return frozenset(observed)


def _verify_resume_checkpoints(project_root: Path) -> list[dict[str, Any]]:
    try:
        records = v17._verify_resume_checkpoints(project_root)
    except v17.MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error as exc:
        raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(str(exc)) from exc
    expected = PHOENIX_SENTINEL_RESUME_CHECKPOINT_V18
    relative = str(expected["path"])
    path = project_root / relative
    if not path.is_file() or path.is_symlink():
        raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
            f"Resume checkpoint is missing: {relative}"
        )
    raw = path.read_bytes()
    payload = v17.v16.v15.v14._json_bytes(raw, label=relative)
    observed: dict[str, Any] = {
        "path": relative,
        "bytes": len(raw),
        "file_sha256": v17.v16.v15.v14._sha(raw),
        "commit_sha256": payload["commit_sha256"],
        "state": payload.get("state"),
        "city_id": payload.get("city_id"),
    }
    if observed != expected:
        raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
            "Phoenix Sentinel resume checkpoint changed."
        )
    records.append(observed)
    if tuple(record["path"] for record in records) != RESUME_CHECKPOINT_PATHS:
        raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
            "V18 resume checkpoint order changed."
        )
    return records


def _preflight(project_root: Path, *, allow_clean: bool = False) -> str:
    helper = v17.v16.v15.v14
    branch = str(helper._run_git(project_root, "branch", "--show-current")).strip()
    head = str(helper._run_git(project_root, "rev-parse", "HEAD")).strip()
    origin = str(helper._run_git(project_root, "rev-parse", "origin/main")).strip()
    status = helper._run_git(
        project_root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        binary=True,
    )
    assert isinstance(status, bytes)
    if branch != "main" or head != origin:
        raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
            "V18 requires synchronized main."
        )
    observed = _parse_status(status)
    permitted = {frozenset(RESUME_CHECKPOINT_PATHS)}
    if allow_clean:
        permitted.add(frozenset())
    if observed not in permitted:
        raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
            "V18 requires either its exact eleven checkpoints or a clean worktree."
        )
    _verify_resume_checkpoints(project_root)
    return head


def _implementation_delta(project_root: Path, implementation: str) -> None:
    helper = v17.v16.v15.v14
    parents = str(
        helper._run_git(project_root, "rev-list", "--parents", "-n", "1", implementation)
    ).split()
    if parents != [implementation, IMPLEMENTATION_BASE_COMMIT]:
        raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
            "V18 implementation is not the V17 publication's direct child."
        )
    raw = helper._run_git(
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
    if helper._parse_delta(raw) != EXPECTED_IMPLEMENTATION_DELTA:
        raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
            "V18 implementation changed a path outside its exact allowlist."
        )


def _executor_contract(
    project_root: Path, *, implementation: str
) -> tuple[tuple[str, ...], dict[str, Any], dict[str, bool]]:
    helper = v17.v16.v15.v14
    _implementation_delta(project_root, implementation)
    for relative in (CONFIG_PATH, EXECUTOR_MODULE_PATH, SENTINEL_MODULE_PATH):
        _, oid, _ = helper._git_blob(project_root, commit=implementation, relative_path=relative)
        worktree_oid = str(
            helper._run_git(
                project_root,
                "hash-object",
                f"--path={relative}",
                "--",
                relative,
            )
        ).strip()
        if worktree_oid != oid:
            raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
                f"V18 executor input differs from HEAD: {relative}"
            )
    module = importlib.import_module("la_heat.multicity.missing_support_calibration_evidence_v1")
    code_paths = tuple(module.CODE_PATHS)
    scope = deepcopy(module.expected_plan_authorization_scope())
    authorized = deepcopy(module.expected_authorized_now())
    if scope.get("configuration") != {
        "path": CONFIG_PATH,
        "sha256": module.CONFIG_SHA256,
    }:
        raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
            "V18 configuration identity changed."
        )
    if sum(bool(value) for value in authorized.values()) != 1:
        raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
            "V18 executor permission map changed."
        )
    return code_paths, scope, authorized


def transition_code_paths(executor_paths: tuple[str, ...]) -> tuple[str, ...]:
    """Return runtime and transition files frozen at V18 implementation."""

    return tuple(
        dict.fromkeys(
            (
                *v17.transition_code_paths(executor_paths),
                TRANSITION_MODULE_PATH,
                AUTHORIZATION_SCRIPT_PATH,
            )
        )
    )


def _historical_v17(project_root: Path) -> tuple[dict[str, Any], bytes]:
    helper = v17.v16.v15.v14
    raw, _, _ = helper._git_blob(
        project_root,
        commit=V17_PUBLICATION_COMMIT,
        relative_path=PLAN_PATH,
    )
    if len(raw) != V17_BYTES or helper._sha(raw) != V17_FILE_SHA256:
        raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
            "Historical planning V17 bytes changed."
        )
    payload = helper._json_bytes(raw, label="historical planning V17")
    scope = payload.get("missing_support_calibration_evidence_v1_authorization_scope", {})
    if (
        payload.get("schema_version") != 17
        or payload.get("algorithm_version") != "multicity-planning-readiness-v17"
        or payload.get("commit_sha256") != V17_INTERNAL_COMMIT_SHA256
        or scope.get("configuration") != {"path": CONFIG_PATH, "sha256": OLD_CONFIG_SHA256}
        or payload.get("authorized_now", {}).get(
            "portable_predictor_missing_support_and_calibration_evidence_staging"
        )
        is not True
        or payload.get("transition", {}).get("authorized_fix") != v17.AUTHORIZED_FIX
    ):
        raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
            "Historical planning V17 contract changed."
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
    payload["missing_support_calibration_evidence_v1_authorization_scope"] = deepcopy(
        dict(authorization_scope)
    )
    payload["transition"] = {
        "id": "route_frozen_source_footprints_to_their_published_verifiers_and_resume",
        "mode": "tracked_code_config_git_and_exact_target_blind_checkpoints_only",
        "predecessor_v17": {
            "path": PLAN_PATH,
            "publication_git_commit": V17_PUBLICATION_COMMIT,
            "implementation_git_commit": V17_IMPLEMENTATION_COMMIT,
            "bytes": V17_BYTES,
            "file_sha256": V17_FILE_SHA256,
            "commit_sha256": V17_INTERNAL_COMMIT_SHA256,
            "authorized_fix_sha256": canonical_sha256(v17.AUTHORIZED_FIX),
        },
        "consumed_v17_transition_sha256": canonical_sha256(previous_transition),
        "failure_evidence": {
            "completed_overall_tasks": 2,
            "completed_checkpoint_count": 11,
            "completed_sentinel_cities": ["phoenix_az"],
            "failing_task": "external_city_sentinel_calibration_smoke_v1",
            "failing_city": "houston_tx",
            "exception_type": "SourceFootprintError",
            "failing_gate": "metadata discovery is not authorized for houston_tx",
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
        raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
            "V18 changed a scientific lock."
        )
    if payload["authorized_now"] != predecessor["authorized_now"]:
        raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
            "V18 changed a scientific permission."
        )
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def _publication_commit(project_root: Path, *, implementation: str, head: str) -> str:
    helper = v17.v16.v15.v14
    changes = str(
        helper._run_git(
            project_root,
            "log",
            "--format=%H",
            f"{implementation}..{head}",
            "--",
            PLAN_PATH,
        )
    ).splitlines()
    if len(changes) != 1:
        raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
            "Planning V18 must have one exact publication commit."
        )
    publication = changes[0]
    parents = str(
        helper._run_git(project_root, "rev-list", "--parents", "-n", "1", publication)
    ).split()
    if parents != [publication, implementation]:
        raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
            "Planning V18 is not the hotfix implementation's direct child."
        )
    raw = helper._run_git(
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
    if helper._parse_delta(raw) != frozenset({("M", PLAN_PATH)}):
        raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
            "Planning V18 publication changed more than PLAN_READINESS.json."
        )
    return publication


def authorize_multicity_sentinel_source_footprint_verifier_hotfix_v18(
    *, project_root: str | Path | None = None, write: bool = True
) -> dict[str, Any]:
    """Create or authenticate planning V18 and its eleven resume checkpoints."""

    helper = v17.v16.v15.v14
    root = (
        Path(project_root).resolve()
        if project_root is not None
        else Path(__file__).resolve().parents[3]
    )
    plan_path = root / PLAN_PATH
    predecessor, predecessor_raw = _historical_v17(root)
    current_raw = plan_path.read_bytes()
    writing_from_v17 = current_raw == predecessor_raw
    head = _preflight(root, allow_clean=not writing_from_v17)

    if writing_from_v17:
        if not write:
            raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
                "PLAN_READINESS is still V17."
            )
        implementation = head
        code_paths, scope, authorized = _executor_contract(root, implementation=implementation)
        code_files = helper._code_records(root, commit=implementation, paths=code_paths)
        transition_files = helper._code_records(
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
            raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
                "Planning changed before the V18 write boundary."
            )
        helper._atomic_replace(helper._expected_bytes(payload), plan_path)
        return payload

    observed = helper._json_bytes(current_raw, label="canonical planning V18")
    transition = observed.get("transition", {})
    implementation = transition.get("implementation", {}).get("implementation_git_commit")
    if not isinstance(implementation, str):
        raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
            "Planning V18 implementation identity is missing."
        )
    code_paths, scope, authorized = _executor_contract(root, implementation=implementation)
    code_files = helper._code_records(root, commit=implementation, paths=code_paths)
    transition_paths = transition_code_paths(code_paths)
    transition_files = helper._code_records(root, commit=implementation, paths=transition_paths)
    expected = _build_payload(
        predecessor,
        implementation=implementation,
        code_files=code_files,
        transition_code_files=transition_files,
        authorization_scope=scope,
        authorized_now=authorized,
    )
    if current_raw != helper._expected_bytes(expected) or observed != expected:
        raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
            "Canonical planning V18 bytes changed."
        )
    _publication_commit(root, implementation=implementation, head=head)
    for relative in transition_paths:
        history = str(
            helper._run_git(
                root,
                "log",
                "--format=%H",
                f"{implementation}..{head}",
                "--",
                relative,
            )
        )
        if history.strip():
            raise MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error(
                f"V18-authorized runtime changed after implementation: {relative}"
            )
    return observed
