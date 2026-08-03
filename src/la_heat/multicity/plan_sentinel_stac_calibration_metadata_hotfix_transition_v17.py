"""Planning V17: accept absent optional Sentinel STAC calibration metadata."""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, Final

from la_heat.multicity import (
    plan_sentinel_content_encoding_hotfix_transition_v16 as v16,
)
from la_heat.provenance import canonical_sha256

SCHEMA_VERSION: Final = 17
ALGORITHM_VERSION: Final = "multicity-planning-readiness-v17"
PLANNING_STAGE: Final = (
    "missing_support_calibration_evidence_v1_sentinel_stac_calibration_"
    "metadata_hotfix_resume_authorized"
)
NEXT_SAFE_STAGE: Final = "stage_target_blind_missing_support_and_calibration_evidence_v1"
PLAN_PATH: Final = v16.PLAN_PATH
IMPLEMENTATION_BASE_COMMIT: Final = "2c2b8d42e08ed8321b6ccc2ad4f20750795f48dc"
V16_PUBLICATION_COMMIT: Final = IMPLEMENTATION_BASE_COMMIT
V16_IMPLEMENTATION_COMMIT: Final = "e47ba81e236338afd3ff25889020fafd5c0b2825"
V16_BYTES: Final = 64_092
V16_FILE_SHA256: Final = "284acbfa2121e87f88297e1ba6891a017a17a1b85cc523d6e7804b13635daf46"
V16_INTERNAL_COMMIT_SHA256: Final = (
    "baa26d0ff471e4175a658a159a6deeea787931cbf6e9e69029eda7723ef5d4d4"
)
OLD_CONFIG_SHA256: Final = "d703f216eb80b187c390ae8efdf49c68615bf16614657706249c2f052ea50dba"

CONFIG_PATH: Final = v16.CONFIG_PATH
EXECUTOR_MODULE_PATH: Final = v16.EXECUTOR_MODULE_PATH
SENTINEL_MODULE_PATH: Final = v16.SENTINEL_MODULE_PATH
SENTINEL_TEST_PATH: Final = v16.SENTINEL_TEST_PATH
TRANSITION_MODULE_PATH: Final = (
    "src/la_heat/multicity/plan_sentinel_stac_calibration_metadata_hotfix_transition_v17.py"
)
AUTHORIZATION_SCRIPT_PATH: Final = (
    "scripts/authorize_multicity_sentinel_stac_calibration_metadata_hotfix_v17.py"
)
TRANSITION_TEST_PATH: Final = (
    "tests/test_multicity_plan_sentinel_stac_calibration_metadata_hotfix_transition_v17.py"
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

RESUME_CHECKPOINTS: Final = v16.RESUME_CHECKPOINTS
RESUME_CHECKPOINT_PATHS: Final = v16.RESUME_CHECKPOINT_PATHS

AUTHORIZED_FIX: Final = {
    "sentinel_stac_raster_calibration_requirement_before": (
        "exactly_one_raster_bands_record_per_asset"
    ),
    "sentinel_stac_raster_calibration_policy_after": (
        "all_seven_absent_record_unavailable_or_all_seven_single_records_cross_checked"
    ),
    "official_provider_schema_observation": (
        "sentinel_2_l2a_item_uses_eo_sat_projection_without_raster_extension"
    ),
    "product_metadata_xml_remains_decode_authority": True,
    "stac_calibration_values_synthesized_from_xml": False,
    "missing_stac_calibration_counted_as_match": False,
    "partial_or_malformed_stac_calibration_rejected": True,
    "native_dn_cog_storage_validation_before": "record_only",
    "native_dn_cog_storage_validation_after": (
        "single_band_expected_dtype_nodata_0_scale_1_offset_0"
    ),
    "failing_stage": "external_city_sentinel_calibration_smoke_v1",
    "completed_worldcover_terminal_preserved": True,
    "completed_checkpoint_count": 10,
    "sentinel_probe_selection_changed": False,
    "sentinel_band_set_or_window_changed": False,
    "sentinel_decode_formula_changed": False,
    "collection_or_asset_changed": False,
    "network_endpoint_or_budget_changed": False,
    "tracked_output_paths_changed": False,
    "permissions_changed": False,
    "locks_changed": False,
    "conflicting_next_plan_version_replaced": "v17",
    "successful_evidence_next_plan_version": "v18",
}

TRANSITION_ACCESS_CONTRACT: Final = {
    "network_requests": 0,
    "tracked_code_configuration_and_git_blobs_read": True,
    "exact_target_blind_checkpoint_manifests_read": True,
    "raw_stac_or_product_metadata_opened": False,
    "raw_raster_payloads_opened": False,
    "predictor_values_opened_or_computed": False,
    "model_fit_or_prediction_performed": False,
    "external_target_or_qa_values_read": False,
    "landsat_thermal_values_read": False,
    "final_evaluation_outputs_opened": False,
}


class MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(ValueError):
    """Raised when the exact V17 resume transition fails authentication."""


def _parse_status(raw: bytes) -> frozenset[str]:
    fields = raw.split(b"\0")
    if fields[-1:] != [b""]:
        raise MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(
            "Git status is not NUL terminated."
        )
    observed: set[str] = set()
    for field in fields[:-1]:
        if not field.startswith(b"?? "):
            raise MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(
                "V17 permits only append-only untracked resume checkpoints."
            )
        path = field[3:].decode("utf-8")
        if path not in RESUME_CHECKPOINT_PATHS or path in observed:
            raise MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(
                f"Unexpected dirty path at V17 boundary: {path}"
            )
        observed.add(path)
    return frozenset(observed)


def _verify_resume_checkpoints(project_root: Path) -> list[dict[str, Any]]:
    try:
        records = v16._verify_resume_checkpoints(project_root)
    except v16.MulticityPlanSentinelContentEncodingHotfixV16Error as exc:
        raise MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(str(exc)) from exc
    if tuple(record["path"] for record in records) != RESUME_CHECKPOINT_PATHS:
        raise MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(
            "V17 resume checkpoint order changed."
        )
    return records


def _preflight(project_root: Path, *, allow_clean: bool = False) -> str:
    helper = v16.v15.v14
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
        raise MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(
            "V17 requires synchronized main."
        )
    observed = _parse_status(status)
    permitted = {frozenset(RESUME_CHECKPOINT_PATHS)}
    if allow_clean:
        permitted.add(frozenset())
    if observed not in permitted:
        raise MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(
            "V17 requires either its exact ten checkpoints or a clean worktree."
        )
    _verify_resume_checkpoints(project_root)
    return head


def _implementation_delta(project_root: Path, implementation: str) -> None:
    helper = v16.v15.v14
    parents = str(
        helper._run_git(project_root, "rev-list", "--parents", "-n", "1", implementation)
    ).split()
    if parents != [implementation, IMPLEMENTATION_BASE_COMMIT]:
        raise MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(
            "V17 implementation is not the V16 publication's direct child."
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
        raise MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(
            "V17 implementation changed a path outside its exact allowlist."
        )


def _executor_contract(
    project_root: Path, *, implementation: str
) -> tuple[tuple[str, ...], dict[str, Any], dict[str, bool]]:
    helper = v16.v15.v14
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
            raise MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(
                f"V17 executor input differs from HEAD: {relative}"
            )
    module = importlib.import_module("la_heat.multicity.missing_support_calibration_evidence_v1")
    code_paths = tuple(module.CODE_PATHS)
    scope = deepcopy(module.expected_plan_authorization_scope())
    authorized = deepcopy(module.expected_authorized_now())
    if scope.get("configuration") != {
        "path": CONFIG_PATH,
        "sha256": module.CONFIG_SHA256,
    }:
        raise MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(
            "V17 configuration identity changed."
        )
    if sum(bool(value) for value in authorized.values()) != 1:
        raise MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(
            "V17 executor permission map changed."
        )
    return code_paths, scope, authorized


def transition_code_paths(executor_paths: tuple[str, ...]) -> tuple[str, ...]:
    """Return runtime and transition files frozen at V17 implementation."""

    return tuple(
        dict.fromkeys(
            (
                *v16.transition_code_paths(executor_paths),
                TRANSITION_MODULE_PATH,
                AUTHORIZATION_SCRIPT_PATH,
            )
        )
    )


def _historical_v16(project_root: Path) -> tuple[dict[str, Any], bytes]:
    helper = v16.v15.v14
    raw, _, _ = helper._git_blob(
        project_root,
        commit=V16_PUBLICATION_COMMIT,
        relative_path=PLAN_PATH,
    )
    if len(raw) != V16_BYTES or helper._sha(raw) != V16_FILE_SHA256:
        raise MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(
            "Historical planning V16 bytes changed."
        )
    payload = helper._json_bytes(raw, label="historical planning V16")
    scope = payload.get("missing_support_calibration_evidence_v1_authorization_scope", {})
    if (
        payload.get("schema_version") != 16
        or payload.get("algorithm_version") != "multicity-planning-readiness-v16"
        or payload.get("commit_sha256") != V16_INTERNAL_COMMIT_SHA256
        or scope.get("configuration") != {"path": CONFIG_PATH, "sha256": OLD_CONFIG_SHA256}
        or payload.get("authorized_now", {}).get(
            "portable_predictor_missing_support_and_calibration_evidence_staging"
        )
        is not True
        or payload.get("transition", {}).get("authorized_fix") != v16.AUTHORIZED_FIX
    ):
        raise MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(
            "Historical planning V16 contract changed."
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
        "id": "accept_absent_optional_stac_raster_calibration_and_resume",
        "mode": "tracked_code_config_git_and_exact_target_blind_checkpoints_only",
        "predecessor_v16": {
            "path": PLAN_PATH,
            "publication_git_commit": V16_PUBLICATION_COMMIT,
            "implementation_git_commit": V16_IMPLEMENTATION_COMMIT,
            "bytes": V16_BYTES,
            "file_sha256": V16_FILE_SHA256,
            "commit_sha256": V16_INTERNAL_COMMIT_SHA256,
            "authorized_fix_sha256": canonical_sha256(v16.AUTHORIZED_FIX),
        },
        "consumed_v16_transition_sha256": canonical_sha256(previous_transition),
        "failure_evidence": {
            "completed_overall_tasks": 2,
            "completed_checkpoint_count": 10,
            "failing_task": "external_city_sentinel_calibration_smoke_v1",
            "exception_type": "MissingSupportCalibrationEvidenceV1Error",
            "failing_gate": "Sentinel B02 lacks one raster:bands record",
            "provider_item_raster_extension_declared": False,
            "product_metadata_xml_available_as_decode_authority": True,
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
        raise MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(
            "V17 changed a scientific lock."
        )
    if payload["authorized_now"] != predecessor["authorized_now"]:
        raise MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(
            "V17 changed a scientific permission."
        )
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def _publication_commit(project_root: Path, *, implementation: str, head: str) -> str:
    helper = v16.v15.v14
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
        raise MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(
            "Planning V17 must have one exact publication commit."
        )
    publication = changes[0]
    parents = str(
        helper._run_git(project_root, "rev-list", "--parents", "-n", "1", publication)
    ).split()
    if parents != [publication, implementation]:
        raise MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(
            "Planning V17 is not the hotfix implementation's direct child."
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
        raise MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(
            "Planning V17 publication changed more than PLAN_READINESS.json."
        )
    return publication


def authorize_multicity_sentinel_stac_calibration_metadata_hotfix_v17(
    *, project_root: str | Path | None = None, write: bool = True
) -> dict[str, Any]:
    """Create or authenticate planning V17 and its ten resume checkpoints."""

    helper = v16.v15.v14
    root = (
        Path(project_root).resolve()
        if project_root is not None
        else Path(__file__).resolve().parents[3]
    )
    plan_path = root / PLAN_PATH
    predecessor, predecessor_raw = _historical_v16(root)
    current_raw = plan_path.read_bytes()
    writing_from_v16 = current_raw == predecessor_raw
    head = _preflight(root, allow_clean=not writing_from_v16)

    if writing_from_v16:
        if not write:
            raise MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(
                "PLAN_READINESS is still V16."
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
            raise MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(
                "Planning changed before the V17 write boundary."
            )
        helper._atomic_replace(helper._expected_bytes(payload), plan_path)
        return payload

    observed = helper._json_bytes(current_raw, label="canonical planning V17")
    transition = observed.get("transition", {})
    implementation = transition.get("implementation", {}).get("implementation_git_commit")
    if not isinstance(implementation, str):
        raise MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(
            "Planning V17 implementation identity is missing."
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
        raise MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(
            "Canonical planning V17 bytes changed."
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
            raise MulticityPlanSentinelStacCalibrationMetadataHotfixV17Error(
                f"V17-authorized runtime changed after implementation: {relative}"
            )
    return observed
