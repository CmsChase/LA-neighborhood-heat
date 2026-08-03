"""Planning V16: authorize bounded Sentinel content decoding and resume."""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, Final

from la_heat.multicity import (
    plan_worldcover_mosaic_alignment_hotfix_transition_v15 as v15,
)
from la_heat.provenance import canonical_sha256

SCHEMA_VERSION: Final = 16
ALGORITHM_VERSION: Final = "multicity-planning-readiness-v16"
PLANNING_STAGE: Final = (
    "missing_support_calibration_evidence_v1_sentinel_content_encoding_"
    "hotfix_resume_authorized"
)
NEXT_SAFE_STAGE: Final = (
    "stage_target_blind_missing_support_and_calibration_evidence_v1"
)
PLAN_PATH: Final = v15.PLAN_PATH
IMPLEMENTATION_BASE_COMMIT: Final = "e465011a5926382f9e41a8628d9775de92f829ae"
V15_PUBLICATION_COMMIT: Final = IMPLEMENTATION_BASE_COMMIT
V15_IMPLEMENTATION_COMMIT: Final = "dc1c782fa404b7ff54eda62b4d8b9dca03336cb5"
V15_BYTES: Final = 62_383
V15_FILE_SHA256: Final = (
    "ada23261dd944193c76b4cf9de2bc025e53e8942fdb03aa003d4878952c02ac7"
)
V15_INTERNAL_COMMIT_SHA256: Final = (
    "7871becf14b624d052f93be827de48e379d1d1bfe7af2c4fb86a26632bbf854a"
)
OLD_CONFIG_SHA256: Final = (
    "37f522c374a95b78de8f7e7c4bc28dd7d9005eeac9a599d073444c964d8be9dd"
)

CONFIG_PATH: Final = v15.CONFIG_PATH
EXECUTOR_MODULE_PATH: Final = v15.EXECUTOR_MODULE_PATH
SENTINEL_MODULE_PATH: Final = v15.SENTINEL_MODULE_PATH
SENTINEL_TEST_PATH: Final = "tests/test_multicity_sentinel_calibration_smoke_v1.py"
TRANSITION_MODULE_PATH: Final = (
    "src/la_heat/multicity/plan_sentinel_content_encoding_hotfix_transition_v16.py"
)
AUTHORIZATION_SCRIPT_PATH: Final = (
    "scripts/authorize_multicity_sentinel_content_encoding_hotfix_v16.py"
)
TRANSITION_TEST_PATH: Final = (
    "tests/test_multicity_plan_sentinel_content_encoding_hotfix_transition_v16.py"
)

EXPECTED_IMPLEMENTATION_DELTA: Final = frozenset(
    {
        ("M", CONFIG_PATH),
        ("M", EXECUTOR_MODULE_PATH),
        ("M", SENTINEL_MODULE_PATH),
        ("M", SENTINEL_TEST_PATH),
        ("A", TRANSITION_MODULE_PATH),
        ("A", AUTHORIZATION_SCRIPT_PATH),
        ("A", TRANSITION_TEST_PATH),
    }
)

WORLDCOVER_RESUME_CHECKPOINTS_V16: Final = (
    {
        "path": (
            "manifests/multicity/cities/houston_tx/eligible_support/"
            "WORLDCOVER_ELIGIBLE_SUPPORT_V1.json"
        ),
        "bytes": 7_739,
        "file_sha256": (
            "060b090224e86976cdbb1537f51b8eab003249e4441b7533b076a8eb7d13d9a5"
        ),
        "commit_sha256": (
            "e24427bcb4a3b049f971c370f0e7ff1f6653ad7f222f4028d2ba86dec86fbb91"
        ),
        "state": "complete_target_blind_city_worldcover_support",
        "city_id": "houston_tx",
    },
    {
        "path": (
            "manifests/multicity/cities/chicago_il/eligible_support/"
            "WORLDCOVER_ELIGIBLE_SUPPORT_V1.json"
        ),
        "bytes": 7_710,
        "file_sha256": (
            "f0434db27ae59a87a8e14a76af1969fe638b804f0ca390a2bf8bd3e96c87ea01"
        ),
        "commit_sha256": (
            "9d5b54df7f6562ddc2bb1540cac36c4d61b866fcb9f8a49a7c2b4e7fee3b56ef"
        ),
        "state": "complete_target_blind_city_worldcover_support",
        "city_id": "chicago_il",
    },
    {
        "path": (
            "manifests/multicity/reviews/portable_predictor_contract/"
            "FOUR_CITY_WORLDCOVER_ELIGIBLE_SUPPORT_V1.json"
        ),
        "bytes": 4_066,
        "file_sha256": (
            "4b36fc6eb22e8e1a611f29f58b2ddd3db8c1803271ed7ea6a3d2c07110578279"
        ),
        "commit_sha256": (
            "2a7a714df11f8f93858ce0d80185240441e8f938be4e23ade41110091f263e82"
        ),
        "state": "complete_target_blind_four_city_worldcover_support",
    },
)
RESUME_CHECKPOINTS: Final = (
    *v15.RESUME_CHECKPOINTS,
    *WORLDCOVER_RESUME_CHECKPOINTS_V16,
)
RESUME_CHECKPOINT_PATHS: Final = tuple(
    str(record["path"]) for record in RESUME_CHECKPOINTS
)

AUTHORIZED_FIX: Final = {
    "sentinel_http_body_check_before": (
        "decoded_body_length_equals_encoded_content_length"
    ),
    "sentinel_http_body_check_after": (
        "identity_requires_exact_length_encoded_bounds_both_representations"
    ),
    "allowed_content_encodings": ["identity", "gzip", "deflate", "br", "zstd"],
    "declared_encoded_byte_limit_preserved": True,
    "decoded_byte_limit_enforced": True,
    "total_byte_accounting": "maximum_of_declared_encoded_and_decoded_bytes",
    "failing_stage": "external_city_sentinel_calibration_smoke_v1",
    "repeated_identical_failures_observed": 8,
    "completed_worldcover_terminal_preserved": True,
    "completed_checkpoint_count": 10,
    "sentinel_probe_selection_changed": False,
    "sentinel_band_or_window_contract_changed": False,
    "collection_or_asset_changed": False,
    "tracked_output_paths_changed": False,
    "permissions_changed": False,
    "locks_changed": False,
    "conflicting_next_plan_version_replaced": "v16",
    "successful_evidence_next_plan_version": "v17",
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


class MulticityPlanSentinelContentEncodingHotfixV16Error(ValueError):
    """Raised when the exact V16 resume transition fails authentication."""


def _parse_status(raw: bytes) -> frozenset[str]:
    fields = raw.split(b"\0")
    if fields[-1:] != [b""]:
        raise MulticityPlanSentinelContentEncodingHotfixV16Error(
            "Git status is not NUL terminated."
        )
    observed: set[str] = set()
    for field in fields[:-1]:
        if not field.startswith(b"?? "):
            raise MulticityPlanSentinelContentEncodingHotfixV16Error(
                "V16 permits only append-only untracked resume checkpoints."
            )
        path = field[3:].decode("utf-8")
        if path not in RESUME_CHECKPOINT_PATHS or path in observed:
            raise MulticityPlanSentinelContentEncodingHotfixV16Error(
                f"Unexpected dirty path at V16 boundary: {path}"
            )
        observed.add(path)
    return frozenset(observed)


def _verify_resume_checkpoints(project_root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for expected in RESUME_CHECKPOINTS:
        relative = str(expected["path"])
        path = project_root / relative
        if not path.is_file() or path.is_symlink():
            raise MulticityPlanSentinelContentEncodingHotfixV16Error(
                f"Resume checkpoint is missing: {relative}"
            )
        raw = path.read_bytes()
        payload = v15.v14._json_bytes(raw, label=relative)
        observed: dict[str, Any] = {
            "path": relative,
            "bytes": len(raw),
            "file_sha256": v15.v14._sha(raw),
            "commit_sha256": payload["commit_sha256"],
            "state": payload.get("state"),
        }
        if "city_id" in expected:
            observed["city_id"] = payload.get("city_id")
        if observed != expected:
            raise MulticityPlanSentinelContentEncodingHotfixV16Error(
                f"Resume checkpoint changed: {relative}"
            )
        records.append(observed)
    return records


def _preflight(project_root: Path, *, allow_clean: bool = False) -> str:
    branch = str(v15.v14._run_git(project_root, "branch", "--show-current")).strip()
    head = str(v15.v14._run_git(project_root, "rev-parse", "HEAD")).strip()
    origin = str(v15.v14._run_git(project_root, "rev-parse", "origin/main")).strip()
    status = v15.v14._run_git(
        project_root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        binary=True,
    )
    assert isinstance(status, bytes)
    if branch != "main" or head != origin:
        raise MulticityPlanSentinelContentEncodingHotfixV16Error(
            "V16 requires synchronized main."
        )
    observed_status = _parse_status(status)
    permitted_statuses = {frozenset(RESUME_CHECKPOINT_PATHS)}
    if allow_clean:
        permitted_statuses.add(frozenset())
    if observed_status not in permitted_statuses:
        raise MulticityPlanSentinelContentEncodingHotfixV16Error(
            "V16 requires either its exact ten untracked resume checkpoints "
            "or a clean post-publication worktree."
        )
    _verify_resume_checkpoints(project_root)
    return head


def _implementation_delta(project_root: Path, implementation: str) -> None:
    parents = str(
        v15.v14._run_git(
            project_root, "rev-list", "--parents", "-n", "1", implementation
        )
    ).split()
    if parents != [implementation, IMPLEMENTATION_BASE_COMMIT]:
        raise MulticityPlanSentinelContentEncodingHotfixV16Error(
            "V16 implementation is not the V15 publication's direct child."
        )
    raw = v15.v14._run_git(
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
    if v15.v14._parse_delta(raw) != EXPECTED_IMPLEMENTATION_DELTA:
        raise MulticityPlanSentinelContentEncodingHotfixV16Error(
            "V16 implementation changed a path outside its exact allowlist."
        )


def _executor_contract(
    project_root: Path, *, implementation: str
) -> tuple[tuple[str, ...], dict[str, Any], dict[str, bool]]:
    _implementation_delta(project_root, implementation)
    for relative in (CONFIG_PATH, EXECUTOR_MODULE_PATH):
        _, oid, _ = v15.v14._git_blob(
            project_root, commit=implementation, relative_path=relative
        )
        worktree_oid = str(
            v15.v14._run_git(
                project_root,
                "hash-object",
                f"--path={relative}",
                "--",
                relative,
            )
        ).strip()
        if worktree_oid != oid:
            raise MulticityPlanSentinelContentEncodingHotfixV16Error(
                f"V16 executor input differs from HEAD: {relative}"
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
        raise MulticityPlanSentinelContentEncodingHotfixV16Error(
            "V16 configuration identity changed."
        )
    if sum(bool(value) for value in authorized.values()) != 1:
        raise MulticityPlanSentinelContentEncodingHotfixV16Error(
            "V16 executor permission map changed."
        )
    return code_paths, scope, authorized


def transition_code_paths(executor_paths: tuple[str, ...]) -> tuple[str, ...]:
    """Return runtime and transition files frozen at V16 implementation."""

    return tuple(
        dict.fromkeys(
            (
                *executor_paths,
                v15.v14.v13.TRANSITION_MODULE_PATH,
                v15.v14.TRANSITION_MODULE_PATH,
                v15.TRANSITION_MODULE_PATH,
                TRANSITION_MODULE_PATH,
                AUTHORIZATION_SCRIPT_PATH,
            )
        )
    )


def _historical_v15(project_root: Path) -> tuple[dict[str, Any], bytes]:
    raw, _, _ = v15.v14._git_blob(
        project_root,
        commit=V15_PUBLICATION_COMMIT,
        relative_path=PLAN_PATH,
    )
    if len(raw) != V15_BYTES or v15.v14._sha(raw) != V15_FILE_SHA256:
        raise MulticityPlanSentinelContentEncodingHotfixV16Error(
            "Historical planning V15 bytes changed."
        )
    payload = v15.v14._json_bytes(raw, label="historical planning V15")
    scope = payload.get(
        "missing_support_calibration_evidence_v1_authorization_scope", {}
    )
    if (
        payload.get("schema_version") != 15
        or payload.get("algorithm_version") != "multicity-planning-readiness-v15"
        or payload.get("commit_sha256") != V15_INTERNAL_COMMIT_SHA256
        or scope.get("configuration")
        != {"path": CONFIG_PATH, "sha256": OLD_CONFIG_SHA256}
        or payload.get("authorized_now", {}).get(
            "portable_predictor_missing_support_and_calibration_evidence_staging"
        )
        is not True
        or payload.get("transition", {}).get("authorized_fix") != v15.AUTHORIZED_FIX
    ):
        raise MulticityPlanSentinelContentEncodingHotfixV16Error(
            "Historical planning V15 contract changed."
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
        "id": "bound_encoded_and_decoded_sentinel_http_bodies_and_resume",
        "mode": "tracked_code_config_git_and_exact_target_blind_checkpoints_only",
        "predecessor_v15": {
            "path": PLAN_PATH,
            "publication_git_commit": V15_PUBLICATION_COMMIT,
            "implementation_git_commit": V15_IMPLEMENTATION_COMMIT,
            "bytes": V15_BYTES,
            "file_sha256": V15_FILE_SHA256,
            "commit_sha256": V15_INTERNAL_COMMIT_SHA256,
            "authorized_fix_sha256": canonical_sha256(v15.AUTHORIZED_FIX),
        },
        "consumed_v15_transition_sha256": canonical_sha256(previous_transition),
        "failure_evidence": {
            "completed_overall_tasks": 2,
            "completed_checkpoint_count": 10,
            "failing_task": "external_city_sentinel_calibration_smoke_v1",
            "exception_type": "MissingSupportCalibrationEvidenceV1Error",
            "failing_gate": "decoded body disagrees with encoded Content-Length",
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
        raise MulticityPlanSentinelContentEncodingHotfixV16Error(
            "V16 changed a scientific lock."
        )
    if payload["authorized_now"] != predecessor["authorized_now"]:
        raise MulticityPlanSentinelContentEncodingHotfixV16Error(
            "V16 changed a scientific permission."
        )
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def _publication_commit(
    project_root: Path, *, implementation: str, head: str
) -> str:
    changes = str(
        v15.v14._run_git(
            project_root,
            "log",
            "--format=%H",
            f"{implementation}..{head}",
            "--",
            PLAN_PATH,
        )
    ).splitlines()
    if len(changes) != 1:
        raise MulticityPlanSentinelContentEncodingHotfixV16Error(
            "Planning V16 must have one exact publication commit."
        )
    publication = changes[0]
    parents = str(
        v15.v14._run_git(
            project_root, "rev-list", "--parents", "-n", "1", publication
        )
    ).split()
    if parents != [publication, implementation]:
        raise MulticityPlanSentinelContentEncodingHotfixV16Error(
            "Planning V16 is not the hotfix implementation's direct child."
        )
    raw = v15.v14._run_git(
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
    if v15.v14._parse_delta(raw) != frozenset({("M", PLAN_PATH)}):
        raise MulticityPlanSentinelContentEncodingHotfixV16Error(
            "Planning V16 publication changed more than PLAN_READINESS.json."
        )
    return publication


def authorize_multicity_sentinel_content_encoding_hotfix_v16(
    *,
    project_root: str | Path | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Create or authenticate planning V16 and its ten resume checkpoints."""

    root = (
        Path(project_root).resolve()
        if project_root is not None
        else Path(__file__).resolve().parents[3]
    )
    plan_path = root / PLAN_PATH
    predecessor, predecessor_raw = _historical_v15(root)
    current_raw = plan_path.read_bytes()
    writing_from_v15 = current_raw == predecessor_raw
    head = _preflight(root, allow_clean=not writing_from_v15)

    if writing_from_v15:
        if not write:
            raise MulticityPlanSentinelContentEncodingHotfixV16Error(
                "PLAN_READINESS is still V15."
            )
        implementation = head
        code_paths, scope, authorized = _executor_contract(
            root, implementation=implementation
        )
        code_files = v15.v14._code_records(
            root, commit=implementation, paths=code_paths
        )
        transition_files = v15.v14._code_records(
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
            raise MulticityPlanSentinelContentEncodingHotfixV16Error(
                "Planning changed before the V16 write boundary."
            )
        v15.v14._atomic_replace(v15.v14._expected_bytes(payload), plan_path)
        return payload

    observed = v15.v14._json_bytes(current_raw, label="canonical planning V16")
    transition = observed.get("transition", {})
    implementation = transition.get("implementation", {}).get(
        "implementation_git_commit"
    )
    if not isinstance(implementation, str):
        raise MulticityPlanSentinelContentEncodingHotfixV16Error(
            "Planning V16 implementation identity is missing."
        )
    code_paths, scope, authorized = _executor_contract(
        root, implementation=implementation
    )
    code_files = v15.v14._code_records(
        root, commit=implementation, paths=code_paths
    )
    transition_paths = transition_code_paths(code_paths)
    transition_files = v15.v14._code_records(
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
    if current_raw != v15.v14._expected_bytes(expected) or observed != expected:
        raise MulticityPlanSentinelContentEncodingHotfixV16Error(
            "Canonical planning V16 bytes changed."
        )
    _publication_commit(root, implementation=implementation, head=head)
    for relative in transition_paths:
        history = str(
            v15.v14._run_git(
                root,
                "log",
                "--format=%H",
                f"{implementation}..{head}",
                "--",
                relative,
            )
        )
        if history.strip():
            raise MulticityPlanSentinelContentEncodingHotfixV16Error(
                f"V16-authorized runtime changed after implementation: {relative}"
            )
    return observed
