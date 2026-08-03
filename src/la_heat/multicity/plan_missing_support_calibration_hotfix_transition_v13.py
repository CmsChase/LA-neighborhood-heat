"""Planning V13: authorize one exact WorldCover path-prefix hotfix and resume."""

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

from la_heat.provenance import canonical_sha256

SCHEMA_VERSION: Final = 13
ALGORITHM_VERSION: Final = "multicity-planning-readiness-v13"
PLANNING_STAGE: Final = (
    "missing_support_calibration_evidence_v1_worldcover_asset_path_"
    "hotfix_resume_authorized"
)
NEXT_SAFE_STAGE: Final = (
    "stage_target_blind_missing_support_and_calibration_evidence_v1"
)
PLAN_PATH: Final = "manifests/multicity/PLAN_READINESS.json"
IMPLEMENTATION_BASE_COMMIT: Final = "297d47f10c648e31f78135038d1b09f7548a9e5a"
V12_PUBLICATION_COMMIT: Final = IMPLEMENTATION_BASE_COMMIT
V12_IMPLEMENTATION_COMMIT: Final = "59de258a1e07c8693defa2e7dbdf34385e479ae7"
V12_BYTES: Final = 52_152
V12_FILE_SHA256: Final = (
    "7b1613130d9068183c0ad4e5867dbbbf5a442f9e73a05cb5d307421f544bd6de"
)
V12_INTERNAL_COMMIT_SHA256: Final = (
    "09d464d52485e1318927a6b820aff26610d6143356609b882f8ed4fe56be59b3"
)
OLD_CONFIG_SHA256: Final = (
    "7d99eabe524532c4c5fa0ef8eaa42236b0b635e835bfa5b1a80c53ed99c304ff"
)

CONFIG_PATH: Final = (
    "configs/multicity/missing_support_calibration_evidence_v1.toml"
)
EXECUTOR_MODULE_PATH: Final = (
    "src/la_heat/multicity/missing_support_calibration_evidence_v1.py"
)
TRANSITION_MODULE_PATH: Final = (
    "src/la_heat/multicity/plan_missing_support_calibration_hotfix_transition_v13.py"
)
AUTHORIZATION_SCRIPT_PATH: Final = (
    "scripts/authorize_multicity_missing_support_calibration_hotfix_v13.py"
)
TRANSITION_TEST_PATH: Final = (
    "tests/test_multicity_plan_missing_support_calibration_hotfix_transition_v13.py"
)
WORLDCOVER_TEST_PATH: Final = (
    "tests/test_multicity_worldcover_eligible_support_evidence_v1.py"
)
WORLDCOVER_MODULE_PATH: Final = (
    "src/la_heat/multicity/worldcover_eligible_support_evidence_v1.py"
)
SENTINEL_MODULE_PATH: Final = (
    "src/la_heat/multicity/sentinel_calibration_smoke_v1.py"
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

RESUME_CHECKPOINTS: Final = (
    {
        "path": (
            "manifests/multicity/cities/los_angeles_ca/geography/"
            "GEOGRAPHY_CONTRACT_V1.json"
        ),
        "bytes": 8_770,
        "file_sha256": (
            "af1949e3b75fb31278aa4e496f3cb628bcdc732232ba3124766d3979a6067fdf"
        ),
        "commit_sha256": (
            "f6105bbc1060013c98e63eafca9ca227d4d8538cfc2fb91afd38f5c616e690e3"
        ),
        "state": "complete_target_blind_city_geography_evidence",
    },
    {
        "path": (
            "manifests/multicity/cities/phoenix_az/geography/"
            "GEOGRAPHY_CONTRACT_V1.json"
        ),
        "bytes": 8_653,
        "file_sha256": (
            "04cd83097a56b9154a335db155ff7544fe31ef867f94cc8d07d6c5feeb8ca05e"
        ),
        "commit_sha256": (
            "776d752cc8d51bc0b5c30da1d77fd5fa26b9e3ad17c259d5e0ddfe4592c10eee"
        ),
        "state": "complete_target_blind_city_geography_evidence",
    },
    {
        "path": (
            "manifests/multicity/cities/houston_tx/geography/"
            "GEOGRAPHY_CONTRACT_V1.json"
        ),
        "bytes": 6_700,
        "file_sha256": (
            "d59acaece62cdbc479ded735ce66c1f3812ffb61d816f2d0e521b2dbe8169515"
        ),
        "commit_sha256": (
            "b72342417216ca354318167a3f5ccae8906df309ac071b4093c5ddf3745dbbfb"
        ),
        "state": "complete_target_blind_city_geography_evidence",
    },
    {
        "path": (
            "manifests/multicity/cities/chicago_il/geography/"
            "GEOGRAPHY_CONTRACT_V1.json"
        ),
        "bytes": 6_697,
        "file_sha256": (
            "96dfd6ad524a362f90f508e5a7c706a3dac41b7e89fd4b9e5ae2ab6ba557e7d8"
        ),
        "commit_sha256": (
            "8eb9427e9b599908e4f9c9fc04676a7962a8e1863a1603af9a581069954bed17"
        ),
        "state": "complete_target_blind_city_geography_evidence",
    },
    {
        "path": (
            "manifests/multicity/reviews/portable_predictor_contract/"
            "FOUR_CITY_GEOGRAPHY_CONTRACT_V1.json"
        ),
        "bytes": 2_665,
        "file_sha256": (
            "ec5a3bb6b97e56f18689b11a412034bd7a14d3905f3cd769f010e8c61a47c0ee"
        ),
        "commit_sha256": (
            "dc6e58b0081e90f603270475f471ecda533e3bff731ab6624cf7939455ca3fc3"
        ),
        "state": "complete_target_blind_four_city_geography_evidence",
    },
)
RESUME_CHECKPOINT_PATHS: Final = tuple(
    str(record["path"]) for record in RESUME_CHECKPOINTS
)

AUTHORIZED_FIX: Final = {
    "worldcover_provider_host": "ai4edataeuwest.blob.core.windows.net",
    "rejected_path_prefix": "/esa-worldcover/",
    "authorized_exact_path_prefix": "/esa-worldcover/v100/2020/map/",
    "asset_path_prefix_by_host": {
        "esa-worldcover.s3.eu-central-1.amazonaws.com": "/v100/2020/map/",
        "ai4edataeuwest.blob.core.windows.net": (
            "/esa-worldcover/v100/2020/map/"
        ),
    },
    "cross_host_prefix_reuse_allowed": False,
    "conflicting_next_plan_version_replaced": "v13",
    "successful_evidence_next_plan_version": "v14",
    "collection_year_version_or_asset_changed": False,
    "tracked_output_paths_changed": False,
    "permissions_changed": False,
    "locks_changed": False,
}

TRANSITION_ACCESS_CONTRACT: Final = {
    "network_requests": 0,
    "tracked_code_configuration_and_git_blobs_read": True,
    "exact_geography_checkpoint_manifests_read": True,
    "raw_source_or_raster_payloads_opened": False,
    "predictor_values_opened_or_computed": False,
    "model_fit_or_prediction_performed": False,
    "external_target_or_qa_values_read": False,
    "landsat_thermal_values_read": False,
    "final_evaluation_outputs_opened": False,
}


class MulticityPlanMissingSupportCalibrationHotfixV13Error(ValueError):
    """Raised when the exact V13 resume transition fails authentication."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _expected_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(payload), indent=2).encode("utf-8")


def _json_bytes(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
            f"Cannot parse authenticated JSON: {label}"
        ) from exc
    if not isinstance(payload, dict):
        raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
            f"Authenticated JSON is not an object: {label}"
        )
    recorded = payload.get("commit_sha256")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or canonical_sha256(body) != recorded:
        raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
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
        raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
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
        raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
            f"Required path is not one regular Git blob: {relative_path}"
        )
    raw = _run_git(project_root, "show", f"{commit}:{relative_path}", binary=True)
    assert isinstance(raw, bytes)
    return raw, parts[2], parts[0]


def _parse_delta(raw: bytes) -> frozenset[tuple[str, str]]:
    fields = raw.split(b"\0")
    if fields[-1:] != [b""] or len(fields[:-1]) % 2:
        raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
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
        raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
            "Git status is not NUL terminated."
        )
    observed: set[str] = set()
    for field in fields[:-1]:
        if not field.startswith(b"?? "):
            raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
                "V13 permits only append-only untracked resume checkpoints."
            )
        path = field[3:].decode("utf-8")
        if path not in RESUME_CHECKPOINT_PATHS or path in observed:
            raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
                f"Unexpected dirty path at V13 boundary: {path}"
            )
        observed.add(path)
    return frozenset(observed)


def transition_code_paths(executor_paths: tuple[str, ...]) -> tuple[str, ...]:
    """Return runtime and V13 transition files frozen at implementation."""

    return tuple(
        dict.fromkeys(
            (*executor_paths, TRANSITION_MODULE_PATH, AUTHORIZATION_SCRIPT_PATH)
        )
    )


def _verify_resume_checkpoints(project_root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for expected in RESUME_CHECKPOINTS:
        relative = str(expected["path"])
        path = project_root / relative
        if not path.is_file() or path.is_symlink():
            raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
                f"Resume checkpoint is missing: {relative}"
            )
        raw = path.read_bytes()
        payload = _json_bytes(raw, label=relative)
        observed = {
            "path": relative,
            "bytes": len(raw),
            "file_sha256": _sha(raw),
            "commit_sha256": payload["commit_sha256"],
            "state": payload.get("state"),
        }
        if observed != expected:
            raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
                f"Resume checkpoint changed: {relative}"
            )
        records.append(observed)
    return records


def _implementation_delta(project_root: Path, implementation: str) -> None:
    parents = str(
        _run_git(project_root, "rev-list", "--parents", "-n", "1", implementation)
    ).split()
    if parents != [implementation, IMPLEMENTATION_BASE_COMMIT]:
        raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
            "V13 implementation is not the V12 publication's direct child."
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
        raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
            "V13 implementation changed a path outside its exact allowlist."
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
        raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
            "V13 requires synchronized main."
        )
    observed_status = _parse_status(status)
    permitted_statuses = {frozenset(RESUME_CHECKPOINT_PATHS)}
    if allow_clean:
        permitted_statuses.add(frozenset())
    if observed_status not in permitted_statuses:
        raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
            "V13 requires either its exact five untracked resume checkpoints "
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
            raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
                f"V13 executor input differs from HEAD: {relative}"
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
        raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
            "V13 configuration identity changed."
        )
    if sum(bool(value) for value in authorized.values()) != 1:
        raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
            "V13 executor permission map changed."
        )
    return code_paths, scope, authorized


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


def _historical_v12(project_root: Path) -> tuple[dict[str, Any], bytes]:
    raw, _, _ = _git_blob(
        project_root,
        commit=V12_PUBLICATION_COMMIT,
        relative_path=PLAN_PATH,
    )
    if len(raw) != V12_BYTES or _sha(raw) != V12_FILE_SHA256:
        raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
            "Historical planning V12 bytes changed."
        )
    payload = _json_bytes(raw, label="historical planning V12")
    scope = payload.get(
        "missing_support_calibration_evidence_v1_authorization_scope", {}
    )
    if (
        payload.get("schema_version") != 12
        or payload.get("algorithm_version") != "multicity-planning-readiness-v12"
        or payload.get("commit_sha256") != V12_INTERNAL_COMMIT_SHA256
        or scope.get("configuration")
        != {"path": CONFIG_PATH, "sha256": OLD_CONFIG_SHA256}
        or payload.get("authorized_now", {}).get(
            "portable_predictor_missing_support_and_calibration_evidence_staging"
        )
        is not True
    ):
        raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
            "Historical planning V12 contract changed."
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
        "id": "authorize_exact_worldcover_v100_blob_path_prefix_and_resume_v12_evidence",
        "mode": "tracked_code_config_git_and_exact_target_blind_checkpoints_only",
        "predecessor_v12": {
            "path": PLAN_PATH,
            "publication_git_commit": V12_PUBLICATION_COMMIT,
            "implementation_git_commit": V12_IMPLEMENTATION_COMMIT,
            "bytes": V12_BYTES,
            "file_sha256": V12_FILE_SHA256,
            "commit_sha256": V12_INTERNAL_COMMIT_SHA256,
        },
        "consumed_v12_transition_sha256": canonical_sha256(previous_transition),
        "failure_evidence": {
            "completed_tasks": 1,
            "failing_task": "four_city_worldcover_eligible_support",
            "exception_type": "MissingSupportCalibrationEvidenceV1Error",
            "failing_gate": "WorldCover GET path is outside the allowlist",
            "request_sent_for_rejected_asset": False,
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
        raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
            "V13 changed a scientific lock."
        )
    if payload["authorized_now"] != predecessor["authorized_now"]:
        raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
            "V13 changed a scientific permission."
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
        raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
            "Planning V13 must have one exact publication commit."
        )
    publication = changes[0]
    parents = str(
        _run_git(project_root, "rev-list", "--parents", "-n", "1", publication)
    ).split()
    if parents != [publication, implementation]:
        raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
            "Planning V13 is not the hotfix implementation's direct child."
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
        raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
            "Planning V13 publication changed more than PLAN_READINESS.json."
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


def authorize_multicity_missing_support_calibration_hotfix_v13(
    *,
    project_root: str | Path | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Create or authenticate planning V13 and its five resume checkpoints."""

    root = (
        Path(project_root).resolve()
        if project_root is not None
        else Path(__file__).resolve().parents[3]
    )
    plan_path = root / PLAN_PATH
    predecessor, predecessor_raw = _historical_v12(root)
    current_raw = plan_path.read_bytes()
    writing_from_v12 = current_raw == predecessor_raw
    head = _preflight(root, allow_clean=not writing_from_v12)

    if writing_from_v12:
        if not write:
            raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
                "PLAN_READINESS is still V12."
            )
        implementation = head
        code_paths, scope, authorized = _executor_contract(
            root, implementation=implementation
        )
        code_files = _code_records(
            root, commit=implementation, paths=code_paths
        )
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
            raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
                "Planning changed before the V13 write boundary."
            )
        _atomic_replace(_expected_bytes(payload), plan_path)
        return payload

    observed = _json_bytes(current_raw, label="canonical planning V13")
    transition = observed.get("transition", {})
    implementation = transition.get("implementation", {}).get(
        "implementation_git_commit"
    )
    if not isinstance(implementation, str):
        raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
            "Planning V13 implementation identity is missing."
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
        raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
            "Canonical planning V13 bytes changed."
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
            raise MulticityPlanMissingSupportCalibrationHotfixV13Error(
                f"V13-authorized runtime changed after implementation: {relative}"
            )
    return observed
