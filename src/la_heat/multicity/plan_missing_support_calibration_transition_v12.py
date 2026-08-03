"""Tracked-only planning V12 for target-blind support/calibration evidence."""

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

SCHEMA_VERSION: Final = 12
ALGORITHM_VERSION: Final = "multicity-planning-readiness-v12"
PLANNING_STAGE: Final = (
    "portable_predictor_contract_v2_deferred_missing_support_and_calibration_"
    "evidence_authorized"
)
NEXT_SAFE_STAGE: Final = (
    "stage_target_blind_missing_support_and_calibration_evidence_v1"
)
PLAN_PATH: Final = "manifests/multicity/PLAN_READINESS.json"
IMPLEMENTATION_BASE_COMMIT: Final = "9365e737f950beb513b9477f8005cbf60600dfdf"
V11_PUBLICATION_COMMIT: Final = "162aee78ab692192a4b86fa7fcb9d709c2531c46"
V11_BYTES: Final = 61_661
V11_FILE_SHA256: Final = (
    "e156fe6dce713deffa244e51fc30991be132be898041e89553e2997e74263421"
)
V11_INTERNAL_COMMIT_SHA256: Final = (
    "eb79d28d58d1a914f0f08100a5dd84611b11da75303de6277496b80f6b5d73aa"
)
V2_PUBLICATION_COMMIT: Final = "570238b42aff1ecf7d8f6043a04f33b3efef01bd"
V2_TERMINAL_PATH: Final = (
    "manifests/multicity/reviews/portable_predictor_contract/"
    "PORTABLE_PREDICTOR_CONTRACT_FREEZE_V2.json"
)
V2_BYTES: Final = 54_290
V2_FILE_SHA256: Final = (
    "0a851312b135c7c955ac238f84b7dad3a64609cf1d2f54fc343174c912bb8e34"
)
V2_INTERNAL_COMMIT_SHA256: Final = (
    "425562de6b11254485fe14b00735d8b5da5c4de0485e789201d07e969ba49100"
)

CONFIG_PATH: Final = (
    "configs/multicity/missing_support_calibration_evidence_v1.toml"
)
EXECUTOR_MODULE_PATH: Final = (
    "src/la_heat/multicity/missing_support_calibration_evidence_v1.py"
)
TRANSITION_MODULE_PATH: Final = (
    "src/la_heat/multicity/plan_missing_support_calibration_transition_v12.py"
)
AUTHORIZATION_SCRIPT_PATH: Final = (
    "scripts/authorize_multicity_missing_support_calibration_evidence_v12.py"
)
STAGE_SCRIPT_PATH: Final = (
    "scripts/stage_multicity_missing_support_calibration_evidence_v1.py"
)

EXPECTED_IMPLEMENTATION_DELTA: Final = frozenset(
    {
        ("A", CONFIG_PATH),
        ("A", STAGE_SCRIPT_PATH),
        ("A", AUTHORIZATION_SCRIPT_PATH),
        ("A", "src/la_heat/multicity/four_city_geography_contract_v1.py"),
        ("A", EXECUTOR_MODULE_PATH),
        ("A", TRANSITION_MODULE_PATH),
        ("A", "src/la_heat/multicity/sentinel_calibration_smoke_v1.py"),
        ("A", "src/la_heat/multicity/worldcover_eligible_support_evidence_v1.py"),
        ("A", "tests/test_multicity_four_city_geography_contract_v1.py"),
        ("A", "tests/test_multicity_missing_support_calibration_evidence_v1.py"),
        ("A", "tests/test_multicity_plan_missing_support_calibration_transition_v12.py"),
        ("A", "tests/test_multicity_sentinel_calibration_smoke_v1.py"),
        ("A", "tests/test_multicity_worldcover_eligible_support_evidence_v1.py"),
    }
)

AUTHORIZED_NOW: Final = {
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
    "complete_four_city_geography_contract_and_los_angeles_parity_evidence",
    "complete_four_city_worldcover_item_mosaic_and_eligible_support_evidence",
    "complete_external_city_sentinel_asset_calibration_smoke_evidence",
    "complete_separate_portable_predictor_contract_v3_decision",
    "authorize_predictor_construction_with_separate_tracked_only_transition",
    "promote_protocol_from_draft_with_separate_lock",
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
    "sentinel_asset_or_product_metadata_opened": False,
    "predictor_values_opened_or_computed": False,
    "predictor_construction_performed": False,
    "model_fit_performed": False,
    "model_predictions_computed": False,
    "external_target_or_qa_values_read": False,
    "landsat_thermal_values_read": False,
    "final_evaluation_outputs_opened": False,
}


class MulticityPlanMissingSupportCalibrationTransitionV12Error(ValueError):
    """Raised when the exact tracked-only V12 transition fails."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _expected_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(payload), indent=2).encode("utf-8")


def _json_bytes(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            f"Cannot parse authenticated JSON: {label}"
        ) from exc
    if not isinstance(payload, dict):
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            f"Authenticated JSON is not an object: {label}"
        )
    recorded = payload.get("commit_sha256")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or canonical_sha256(body) != recorded:
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
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
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            f"Git authentication failed for {' '.join(arguments)}: {stderr.strip()}"
        )
    return completed.stdout


def _is_ancestor(project_root: Path, ancestor: str, descendant: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(project_root), "merge-base", "--is-ancestor", ancestor, descendant],
        check=False,
        capture_output=True,
    )
    return completed.returncode == 0


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
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            f"Required path is not one exact regular Git blob: {relative_path}"
        )
    raw = _run_git(project_root, "show", f"{commit}:{relative_path}", binary=True)
    assert isinstance(raw, bytes)
    return raw, parts[2], parts[0]


def _parse_delta(raw: bytes) -> frozenset[tuple[str, str]]:
    fields = raw.split(b"\0")
    if fields[-1:] != [b""] or len(fields[:-1]) % 2:
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            "Git delta is not valid NUL-delimited status/path output."
        )
    return frozenset(
        (
            fields[index].decode("ascii"),
            fields[index + 1].decode("utf-8"),
        )
        for index in range(0, len(fields) - 1, 2)
    )


def _implementation_delta(project_root: Path, implementation: str) -> None:
    parents = str(
        _run_git(project_root, "rev-list", "--parents", "-n", "1", implementation)
    ).split()
    if parents != [implementation, IMPLEMENTATION_BASE_COMMIT]:
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            "The V12 implementation is not the base commit's direct child."
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
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            "The V12 implementation changed a path outside its exact allowlist."
        )


def _preflight_executor_import(project_root: Path) -> tuple[str, str]:
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
    if branch != "main" or head != origin or status:
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            "The V12 executor may be imported only from clean synchronized main."
        )
    additions = str(
        _run_git(
            project_root,
            "log",
            "--format=%H",
            "--diff-filter=A",
            head,
            "--",
            EXECUTOR_MODULE_PATH,
        )
    ).splitlines()
    if len(additions) != 1:
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            "The V12 executor must have one exact Git addition."
        )
    implementation = additions[0]
    _implementation_delta(project_root, implementation)
    later = str(
        _run_git(
            project_root,
            "log",
            "--format=%H",
            f"{implementation}..{head}",
            "--",
            EXECUTOR_MODULE_PATH,
        )
    )
    if later.strip():
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            "The V12 executor changed after its implementation commit."
        )
    _, oid, _ = _git_blob(
        project_root, commit=head, relative_path=EXECUTOR_MODULE_PATH
    )
    worktree_oid = str(
        _run_git(
            project_root,
            "hash-object",
            f"--path={EXECUTOR_MODULE_PATH}",
            "--",
            EXECUTOR_MODULE_PATH,
        )
    ).strip()
    if worktree_oid != oid:
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            "The V12 executor differs from its authenticated HEAD blob."
        )
    return implementation, head


def _executor_contract(project_root: Path) -> tuple[tuple[str, ...], dict[str, Any], str]:
    implementation, import_head = _preflight_executor_import(project_root)
    module = importlib.import_module(
        "la_heat.multicity.missing_support_calibration_evidence_v1"
    )
    try:
        code_paths = tuple(module.CODE_PATHS)
        scope = deepcopy(module.expected_plan_authorization_scope())
        authorized = deepcopy(module.expected_authorized_now())
    except (AttributeError, TypeError) as exc:
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            "The V12 executor does not expose its exact planning contract."
        ) from exc
    if authorized != AUTHORIZED_NOW or scope.get("tracked_output_paths") is None:
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            "The V12 executor authorization contract changed."
        )
    if scope.get("configuration") != {
        "path": CONFIG_PATH,
        "sha256": module.CONFIG_SHA256,
    }:
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            "The V12 configuration identity changed."
        )
    return code_paths, scope, implementation


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


def _historical_inputs(project_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    v11_raw, _, _ = _git_blob(
        project_root, commit=V11_PUBLICATION_COMMIT, relative_path=PLAN_PATH
    )
    if len(v11_raw) != V11_BYTES or _sha(v11_raw) != V11_FILE_SHA256:
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            "Historical planning V11 bytes changed."
        )
    v11 = _json_bytes(v11_raw, label="historical planning V11")
    if (
        v11.get("schema_version") != 11
        or v11.get("algorithm_version") != "multicity-planning-readiness-v11"
        or v11.get("commit_sha256") != V11_INTERNAL_COMMIT_SHA256
        or v11.get("authorized_now", {}).get(
            "portable_predictor_source_and_calibration_contract_freeze_v2"
        )
        is not True
    ):
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            "Historical planning V11 contract changed."
        )
    v2_raw, _, _ = _git_blob(
        project_root,
        commit=V2_PUBLICATION_COMMIT,
        relative_path=V2_TERMINAL_PATH,
    )
    if len(v2_raw) != V2_BYTES or _sha(v2_raw) != V2_FILE_SHA256:
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            "Historical predictor-contract V2 bytes changed."
        )
    v2 = _json_bytes(v2_raw, label="predictor-contract V2 terminal")
    if (
        v2.get("commit_sha256") != V2_INTERNAL_COMMIT_SHA256
        or v2.get("state")
        != "decision_complete_candidate_rules_frozen_contract_deferred_predictor_closed"
        or v2.get("outcome")
        != "defer_for_geography_worldcover_support_and_sentinel_calibration_evidence"
        or v2.get("decision", {}).get("predictor_build_authorized_now") is not False
        or v2.get("access_contract", {}).get("external_target_or_qa_values_read")
        is not False
    ):
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            "Predictor-contract V2 terminal changed."
        )
    return v11, v2


def _build_payload(
    predecessor: Mapping[str, Any],
    terminal: Mapping[str, Any],
    *,
    implementation: str,
    code_files: Mapping[str, Any],
    authorization_scope: Mapping[str, Any],
) -> dict[str, Any]:
    payload = deepcopy(dict(predecessor))
    previous_transition = payload.pop("transition")
    previous_scope = payload.pop(
        "portable_predictor_contract_freeze_v2_authorization_scope"
    )
    payload.pop("commit_sha256")
    payload["schema_version"] = SCHEMA_VERSION
    payload["algorithm_version"] = ALGORITHM_VERSION
    payload["planning_stage"] = PLANNING_STAGE
    payload["code_files"] = deepcopy(dict(code_files))
    payload["transition"] = {
        "id": (
            "consume_deferred_predictor_contract_v2_and_authorize_missing_"
            "support_calibration_evidence_v1"
        ),
        "mode": "tracked_json_toml_code_and_local_git_only",
        "predecessor_plan_readiness": {
            "path": PLAN_PATH,
            "source_git_commit": V11_PUBLICATION_COMMIT,
            "bytes": V11_BYTES,
            "file_sha256": V11_FILE_SHA256,
            "commit_sha256": V11_INTERNAL_COMMIT_SHA256,
            "state": "planning_ready",
            "planning_stage": predecessor["planning_stage"],
            "next_safe_stage": predecessor["next_safe_stage"],
        },
        "predictor_contract_v2_terminal": {
            "path": V2_TERMINAL_PATH,
            "source_git_commit": V2_PUBLICATION_COMMIT,
            "bytes": V2_BYTES,
            "file_sha256": V2_FILE_SHA256,
            "commit_sha256": V2_INTERNAL_COMMIT_SHA256,
            "state": terminal["state"],
            "outcome": terminal["outcome"],
            "observed_blockers": terminal["decision"]["new_v2_blockers_observed"],
        },
        "implementation": {
            "base_git_commit": IMPLEMENTATION_BASE_COMMIT,
            "implementation_git_commit": implementation,
            "delta": [
                {"status": status, "path": path}
                for status, path in sorted(EXPECTED_IMPLEMENTATION_DELTA)
            ],
        },
        "consumed_v11_transition_sha256": canonical_sha256(previous_transition),
        "consumed_v2_authorization_scope_sha256": canonical_sha256(previous_scope),
        "writer_precondition": {
            "branch": "main",
            "git_head": implementation,
            "origin_main_equal": True,
            "worktree_clean": True,
        },
        "authorization_effective_only_when": {
            "this_exact_plan_readiness_is_git_tracked": True,
            "publication_is_implementation_direct_child": True,
            "branch_is_main": True,
            "worktree_is_clean": True,
            "head_equals_local_origin_main": True,
            "v12_check_only_passes_before_evidence_staging": True,
        },
    }
    payload["transition_access_contract"] = deepcopy(TRANSITION_ACCESS_CONTRACT)
    payload["authorized_now"] = deepcopy(AUTHORIZED_NOW)
    payload["locks"] = deepcopy(LOCKS)
    payload["blockers_before_predictor_build"] = list(
        BLOCKERS_BEFORE_PREDICTOR_BUILD
    )
    payload["next_safe_stage"] = NEXT_SAFE_STAGE
    payload["consumed_predictor_contract_freeze_v2_authorization"] = {
        "decision_id": "portable_predictor_contract_freeze_v2",
        "terminal_path": V2_TERMINAL_PATH,
        "terminal_file_sha256": V2_FILE_SHA256,
        "terminal_commit_sha256": V2_INTERNAL_COMMIT_SHA256,
        "terminal_publication_git_commit": V2_PUBLICATION_COMMIT,
        "outcome": terminal["outcome"],
        "old_decision_permission_now": False,
        "predictor_model_target_or_result_access": False,
    }
    payload["missing_support_calibration_evidence_v1_authorization_scope"] = deepcopy(
        dict(authorization_scope)
    )
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def _validate_boundary(
    predecessor: Mapping[str, Any], payload: Mapping[str, Any]
) -> None:
    old = dict(predecessor["authorized_now"])
    expected_old = {
        key: value
        for key, value in AUTHORIZED_NOW.items()
        if key
        != "portable_predictor_missing_support_and_calibration_evidence_staging"
    }
    expected_old["portable_predictor_source_and_calibration_contract_freeze_v2"] = True
    if old != expected_old:
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            "Planning V11 permission map changed."
        )
    if payload["authorized_now"] != AUTHORIZED_NOW or sum(
        bool(value) for value in payload["authorized_now"].values()
    ) != 1:
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            "V12 must open exactly one evidence permission."
        )
    if payload["locks"] != predecessor["locks"] or payload["locks"] != LOCKS:
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            "V12 changed a scientific lock."
        )
    for forbidden in (
        "predictor_construction",
        "model_fitting",
        "external_target_or_qa_value_access",
        "one_time_external_evaluation",
        "operational_forecast_claim",
    ):
        if payload["authorized_now"][forbidden]:
            raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
                f"V12 illegally opened {forbidden}."
            )


def _publication_commit(project_root: Path, *, implementation: str, head: str) -> str:
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
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            "Canonical planning V12 must have one exact publication commit."
        )
    publication = changes[0]
    parents = str(
        _run_git(project_root, "rev-list", "--parents", "-n", "1", publication)
    ).split()
    if parents != [publication, implementation]:
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            "Canonical planning V12 is not the implementation direct child."
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
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            "Canonical planning V12 publication changed more than PLAN_READINESS.json."
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


def authorize_multicity_missing_support_calibration_evidence_v12(
    *,
    project_root: str | Path | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Create or authenticate the one-permission planning V12 transition."""

    root = (
        Path(project_root).resolve()
        if project_root is not None
        else Path(__file__).resolve().parents[3]
    )
    code_paths, scope, implementation = _executor_contract(root)
    head = str(_run_git(root, "rev-parse", "HEAD")).strip()
    code_files = _code_records(root, commit=implementation, paths=code_paths)
    predecessor, terminal = _historical_inputs(root)
    expected = _build_payload(
        predecessor,
        terminal,
        implementation=implementation,
        code_files=code_files,
        authorization_scope=scope,
    )
    _validate_boundary(predecessor, expected)
    plan_path = root / PLAN_PATH
    current = plan_path.read_bytes()
    if write:
        if head != implementation or len(current) != V11_BYTES or _sha(current) != V11_FILE_SHA256:
            raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
                "V12 may be written only from the exact implementation commit and V11 bytes."
            )
        _atomic_replace(_expected_bytes(expected), plan_path)
        return expected

    branch = str(_run_git(root, "branch", "--show-current")).strip()
    origin = str(_run_git(root, "rev-parse", "origin/main")).strip()
    status = _run_git(
        root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        binary=True,
    )
    assert isinstance(status, bytes)
    if branch != "main" or head != origin or status:
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            "V12 check-only requires clean synchronized main."
        )
    _publication_commit(root, implementation=implementation, head=head)
    observed = _json_bytes(current, label="canonical planning V12")
    if current != _expected_bytes(expected) or observed != expected:
        raise MulticityPlanMissingSupportCalibrationTransitionV12Error(
            "Canonical planning V12 bytes changed."
        )
    return observed
