"""Freeze the audited GSHHG source and point-distance algorithm.

This is an evidence-only decision stage. It reads the exact planning record,
tracked prerequisite records, the seven source-only diagnostic rows embedded
in the tracked L3 success record, and local Git metadata. It never opens the
ignored diagnostic CSV, the GSHHG archive, a vector dataset, eligible-land
support, a predictor, a model, or a target.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from la_heat.provenance import (
    atomic_json,
    canonical_sha256,
    code_runtime_fingerprint,
    sha256_file,
)

SCHEMA_VERSION: Final = 2
ALGORITHM_VERSION: Final = "portable-water-distance-freeze-decision-v2"
COMPLETE_STATE: Final = (
    "decision_complete_source_and_algorithm_frozen_predictor_closed"
)
OUTCOME: Final = (
    "freeze_gshhg_2_3_7_l1_l2_l3_source_and_point_distance_algorithm"
)
NEXT_SAFE_STAGE: Final = (
    "publish_tracked_only_plan_v8_after_water_distance_freeze"
)

DEFAULT_CONFIG: Final = Path(
    "configs/multicity/portable_water_distance_freeze_decision_v2.toml"
)
DEFAULT_MANIFEST: Final = Path(
    "manifests/multicity/reviews/portable_water_distance/"
    "WATER_DISTANCE_FREEZE_DECISION_V2.json"
)
V1_MANIFEST: Final = Path(
    "manifests/multicity/reviews/portable_water_distance/"
    "WATER_DISTANCE_FREEZE_DECISION.json"
)
PLAN_PATH: Final = Path("manifests/multicity/PLAN_READINESS.json")
CONFIG_SHA256: Final = (
    "7399abf755ee2323077be1299dc44b92c7972a60fcc30a5497e5e4fb9a9a0dde"
)
SUCCESS_FILE_SHA256: Final = (
    "9b206f449d71f23ff0f13d0adca436a2d433140560fef92646d48a7e5c522070"
)
SUCCESS_COMMIT_SHA256: Final = (
    "9b7f6c814bda4e97120a6768b88feae37ee73044883b2ec8cad10db8d4af0f0b"
)
SUCCESS_PUBLICATION_COMMIT: Final = (
    "0afb1f9868378f12e8fe8b66f5772fde6685ed1f"
)
CODE_PATHS: Final = (
    "configs/multicity/portable_water_distance_freeze_decision_v2.toml",
    "scripts/audit_multicity_portable_water_distance_freeze_v2.py",
    "scripts/authorize_multicity_water_distance_freeze.py",
    "src/la_heat/multicity/plan_freeze_transition_v7.py",
    "src/la_heat/multicity/portable_water_distance_freeze_v2.py",
    "src/la_heat/provenance.py",
)
PREREQUISITE_PATHS: Final = (
    V1_MANIFEST.as_posix(),
    (
        "manifests/multicity/reviews/portable_water_distance/"
        "GSHHG_L3_HIERARCHY_AUDIT_PREREGISTRATION.json"
    ),
    (
        "manifests/multicity/reviews/portable_water_distance/"
        "GSHHG_L3_HIERARCHY_AUDIT_V1_FAILURE.json"
    ),
    "configs/multicity/gshhg_l3_hierarchy_audit_amendment_v2.toml",
    (
        "manifests/multicity/reviews/portable_water_distance/"
        "GSHHG_L3_HIERARCHY_AUDIT.json"
    ),
)

EXPECTED_AUTHORIZED_NOW: Final = {
    "boundary_and_public_metadata_staging": True,
    "target_blind_source_geometry_review": False,
    "target_blind_gshhg_l3_hierarchy_preregistration": False,
    "target_blind_gshhg_l3_hierarchy_geometry_read": False,
    "portable_predictor_source_freeze": True,
    "predictor_construction": False,
    "model_fitting": False,
    "external_target_or_qa_value_access": False,
    "one_time_external_evaluation": False,
    "operational_forecast_claim": False,
}
EXPECTED_PLAN_LOCKS: Final = {
    "protocol_locked": False,
    "external_targets_unlocked": False,
    "external_target_values_read": False,
    "external_prediction_commit_exists": False,
    "portable_water_distance_source_locked": False,
    "portable_water_distance_algorithm_locked": False,
    "portable_water_distance_feature_names_frozen": False,
    "predictor_build_authorized": False,
    "protocol_lock_created": False,
}
EXPECTED_DECISION_LOCKS: Final = {
    "source_lock_created": True,
    "algorithm_lock_created": True,
    "feature_names_frozen": False,
    "predictor_build_authorized": False,
    "protocol_lock_created": False,
    "external_targets_unlocked": False,
    "external_target_values_read": False,
    "external_prediction_commit_exists": False,
}
EXPECTED_ACCESS_CONTRACT: Final = {
    "decision_program_network_requests": 0,
    "planning_manifest_read": True,
    "tracked_decision_config_bytes_read": True,
    "tracked_code_file_bytes_hashed": True,
    "tracked_json_prerequisite_manifest_bytes_read": True,
    "tracked_toml_amendment_bytes_read": True,
    "tracked_source_only_diagnostic_values_read_from_success_manifest": True,
    "historical_git_blob_bytes_read": True,
    "local_git_metadata_read": True,
    "untracked_path_names_checked_by_git_status": True,
    "untracked_file_contents_opened": False,
    "ignored_path_names_requested_from_git": False,
    "ignored_source_only_diagnostic_csv_opened": False,
    "gshhg_archive_bytes_opened_by_decision_program": False,
    "gshhg_archive_members_opened_by_decision_program": False,
    "geometry_opened": False,
    "census_or_other_public_geometry_opened": False,
    "eligible_land_grid_opened": False,
    "distance_values_computed": False,
    "distance_feature_surface_computed": False,
    "tract_aggregation_performed": False,
    "predictor_values_computed": False,
    "predictor_construction_performed": False,
    "model_fit_performed": False,
    "model_predictions_computed": False,
    "landsat_thermal_values_read": False,
    "landsat_target_qa_values_read": False,
    "external_lst_values_read": False,
    "external_target_files_opened": False,
    "final_evaluation_outputs_opened": False,
}

_EXPECTED_DIAGNOSTIC_VALUES: Final = {
    "chicago_il": (1162.3830922086756, 1162.3830922086756, 0.0),
    "houston_tx": (36286.50318330564, 36286.50318330564, 0.0),
    "los_angeles_ca": (20208.299356499057, 20208.299356499057, 0.0),
    "phoenix_az": (262207.7560500305, 262207.7560500305, 0.0),
    "l3_probe_parent_180507": (
        9369.53790830548,
        40318.218779557465,
        30948.680871251985,
    ),
    "l3_probe_parent_180515": (
        2883.988870337199,
        4027.016309388673,
        1143.0274390514742,
    ),
    "l3_probe_parent_180517": (
        2639.1974414631522,
        7535.043902954124,
        4895.846461490972,
    ),
}


class PortableWaterDistanceFreezeV2Error(ValueError):
    """Raised when the V2 freeze decision cannot authenticate."""


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
        raise PortableWaterDistanceFreezeV2Error(f"{label} must be an object.")
    return value


def _read_committed_json(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PortableWaterDistanceFreezeV2Error(
            f"Cannot parse authenticated JSON: {path}"
        ) from exc
    after = sha256_file(path)
    if before != after:
        raise RuntimeError(f"Authenticated file changed while read: {path}")
    if not isinstance(payload, dict):
        raise PortableWaterDistanceFreezeV2Error(
            f"Authenticated JSON must be an object: {path}"
        )
    recorded = payload.get("commit_sha256")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or canonical_sha256(body) != recorded:
        raise PortableWaterDistanceFreezeV2Error(
            f"Authenticated JSON internal commit is invalid: {path}"
        )
    return payload, before


def _read_config(path: str | Path) -> tuple[Path, dict[str, Any]]:
    project_root = Path(__file__).resolve().parents[3]
    requested = Path(path)
    resolved = (
        requested.resolve()
        if requested.is_absolute()
        else (project_root / requested).resolve()
    )
    if resolved != (project_root / DEFAULT_CONFIG).resolve():
        raise PortableWaterDistanceFreezeV2Error(
            "V2 may read only the canonical tracked decision config."
        )
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    if sha256_file(resolved) != CONFIG_SHA256:
        raise PortableWaterDistanceFreezeV2Error(
            "The canonical V2 decision config bytes changed."
        )
    try:
        with resolved.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PortableWaterDistanceFreezeV2Error(
            "Cannot parse the canonical V2 decision config."
        ) from exc
    if not isinstance(payload, dict):
        raise PortableWaterDistanceFreezeV2Error(
            "The V2 decision config must be a table."
        )
    decision = _require_mapping(payload.get("decision"), label="decision")
    expected_decision = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "decision_id": "portable_water_distance_freeze_decision_v2",
        "decision_date": "2026-08-01",
        "scope": "target-blind source-and-point-distance-algorithm freeze decision",
        "state": COMPLETE_STATE,
        "outcome": OUTCOME,
        "expected_experiment_id": "la_to_three_city_zero_shot_v1",
        "expected_experiment_semantic_sha256": (
            "6a52eb39486dc9e992927e5506b4071aaba6ec31dd910eeb0f9bcc51dea1736c"
        ),
    }
    if not _strict_equal(decision, expected_decision):
        raise PortableWaterDistanceFreezeV2Error(
            "The V2 decision identity or outcome changed."
        )
    if not _strict_equal(payload.get("locks"), EXPECTED_DECISION_LOCKS):
        raise PortableWaterDistanceFreezeV2Error("The V2 decision locks changed.")
    if not _strict_equal(payload.get("access_contract"), EXPECTED_ACCESS_CONTRACT):
        raise PortableWaterDistanceFreezeV2Error(
            "The evidence-only access contract changed."
        )
    next_gate = _require_mapping(payload.get("next_gate"), label="next_gate")
    if next_gate.get("stage_id") != NEXT_SAFE_STAGE:
        raise PortableWaterDistanceFreezeV2Error("The next safe stage changed.")
    outputs = _require_mapping(payload.get("outputs"), label="outputs")
    if (
        outputs.get("manifest") != DEFAULT_MANIFEST.as_posix()
        or outputs.get("overwrite_v1_deferred_manifest_allowed") is not False
        or outputs.get("append_only") is not True
    ):
        raise PortableWaterDistanceFreezeV2Error(
            "The append-only V2 output contract changed."
        )
    return resolved, payload


def expected_plan_authorization_scope() -> dict[str, Any]:
    """Return the exact tracked-only v7 permission consumed by this decision."""

    return {
        "decision_id": "portable_water_distance_freeze_decision_v2",
        "experiment_id": "la_to_three_city_zero_shot_v1",
        "experiment_semantic_sha256": (
            "6a52eb39486dc9e992927e5506b4071aaba6ec31dd910eeb0f9bcc51dea1736c"
        ),
        "decision_config_path": DEFAULT_CONFIG.as_posix(),
        "decision_config_sha256": CONFIG_SHA256,
        "decision_runtime_paths": list(CODE_PATHS),
        "planning_authentication_read_set": {
            "v6_predecessor": {
                "path": PLAN_PATH.as_posix(),
                "bytes": 13327,
                "file_sha256": (
                    "9a8f8b93ccfa89bf43354cb09d6d92fee1b436eb5edbd227b75d794dd49cac6c"
                ),
                "commit_sha256": (
                    "1789d828f212e0cd65f87c9427eb4a7fbd1697cc7170ebb98a80806659afbc86"
                ),
                "publication_git_commit": (
                    "6d48d5a6def99c8f9e9fe03997850046c693f538"
                ),
            },
            "v6_experiment_config_files": {
                "configs/multicity/experiment.toml": {
                    "sha256": (
                        "fd5330d83bf292204f05dc0aa64e0a535e45523060dd926e3a9b42f8969e5349"
                    ),
                    "bytes": 5765,
                },
                "configs/multicity/cities/los_angeles_ca.toml": {
                    "sha256": (
                        "ff580b99e2c6d69cef5241c92c6d709984151ed2091c330f0911609136bb94e0"
                    ),
                    "bytes": 231,
                },
                "configs/multicity/cities/phoenix_az.toml": {
                    "sha256": (
                        "138acdd0b6061044fee098d59a5b8b91ac620e21bf556285cad185e0cc0ec61c"
                    ),
                    "bytes": 214,
                },
                "configs/multicity/cities/houston_tx.toml": {
                    "sha256": (
                        "fcc2da008bbce8f0be37913eb3e0f55f56dd8a4aa9baab22c29376cfdf13c4d3"
                    ),
                    "bytes": 214,
                },
                "configs/multicity/cities/chicago_il.toml": {
                    "sha256": (
                        "3d4bccc557d2e1d5b2d4854329137f2ff05ca817d18d2c9e5f81267305d2b229"
                    ),
                    "bytes": 214,
                },
            },
            "v7_plan_publication": {
                "path": PLAN_PATH.as_posix(),
                "identity_rule": (
                    "reconstruct the complete exact v7 payload; require its "
                    "publication to be the sole PLAN_READINESS change after v6, "
                    "a direct child of the exact-v6 precondition, and unchanged "
                    "through the current synchronized HEAD"
                ),
            },
        },
        "tracked_read_set": {
            "deferred_v1_decision": {
                "path": V1_MANIFEST.as_posix(),
                "bytes": 10801,
                "file_sha256": (
                    "226788498dfd8c9eb0aa004d60667dfa712926d8bf443fd710e17a7f5f8d5805"
                ),
                "commit_sha256": (
                    "00e8ed677035f8f8315b7171fa8c969ca6c50c14b0114eff9e5024bb1c7b99b5"
                ),
                "publication_git_commit": (
                    "9209ec244319f14be7c2bcb8b56c38bee12256e0"
                ),
            },
            "l3_preregistration": {
                "path": (
                    "manifests/multicity/reviews/portable_water_distance/"
                    "GSHHG_L3_HIERARCHY_AUDIT_PREREGISTRATION.json"
                ),
                "bytes": 21892,
                "file_sha256": (
                    "ecb21bfa31f98dfe275f113ee13909fd30276e049ee0d2a05fca2b2a2bd4b47f"
                ),
                "commit_sha256": (
                    "7be642a7fd099d026c828e018d699f1c6a885de0d180d50ce7eda00e17e694a7"
                ),
                "publication_git_commit": (
                    "997e86d9ab06d22c04faad6fe714eacde53c9921"
                ),
            },
            "l3_v1_failure": {
                "path": (
                    "manifests/multicity/reviews/portable_water_distance/"
                    "GSHHG_L3_HIERARCHY_AUDIT_V1_FAILURE.json"
                ),
                "bytes": 9954,
                "file_sha256": (
                    "b5eb32e3de1702250e36a7eb81b2ea0c78551930a7f92abe5278d21c05a0ea9e"
                ),
                "commit_sha256": (
                    "e5b8e1e242276bcb530990ee070739f84e48177c431e556cfebb4819c92ea067"
                ),
                "publication_git_commit": (
                    "fbf20ed7a601af8e9f77ad768f1267b8a6503a0d"
                ),
            },
            "l3_v2_amendment": {
                "path": (
                    "configs/multicity/gshhg_l3_hierarchy_audit_amendment_v2.toml"
                ),
                "bytes": 4840,
                "file_sha256": (
                    "c60c2d699e94bca832a78b4959db9a5333b2aa3ae37bfdd72d9c0eb6f37ff127"
                ),
                "publication_git_commit": (
                    "e07ef369ea3310ec67956b06436f793f01c89942"
                ),
            },
            "l3_v2_success": {
                "path": (
                    "manifests/multicity/reviews/portable_water_distance/"
                    "GSHHG_L3_HIERARCHY_AUDIT.json"
                ),
                "bytes": 109139,
                "file_sha256": SUCCESS_FILE_SHA256,
                "commit_sha256": SUCCESS_COMMIT_SHA256,
                "publication_git_commit": SUCCESS_PUBLICATION_COMMIT,
            },
        },
        "source_only_diagnostic_values_may_be_read_from_l3_success": True,
        "decision_output_path": DEFAULT_MANIFEST.as_posix(),
        "overwrite_v1_deferred_manifest_allowed": False,
        "network_or_download_allowed": False,
        "archive_or_member_read_allowed": False,
        "geometry_or_other_public_source_read_allowed": False,
        "eligible_land_or_distance_surface_read_allowed": False,
        "predictor_model_target_or_result_read_allowed": False,
        "predictor_build_automatically_authorized_after_decision": False,
    }


def _validate_plan_payload(payload: Mapping[str, Any]) -> None:
    expected_identity = {
        "schema_version": 7,
        "algorithm_version": "multicity-planning-readiness-v7",
        "state": "planning_ready",
        "planning_stage": (
            "gshhg_l3_hierarchy_audit_complete_freeze_decision_authorized"
        ),
        "next_safe_stage": (
            "separate_portable_water_distance_source_and_algorithm_freeze_decision"
        ),
        "experiment_id": "la_to_three_city_zero_shot_v1",
        "config_semantic_sha256": (
            "6a52eb39486dc9e992927e5506b4071aaba6ec31dd910eeb0f9bcc51dea1736c"
        ),
    }
    for key, expected in expected_identity.items():
        if not _strict_equal(payload.get(key), expected):
            raise PortableWaterDistanceFreezeV2Error(
                f"Planning authorization {key} changed."
            )
    if not _strict_equal(payload.get("authorized_now"), EXPECTED_AUTHORIZED_NOW):
        raise PortableWaterDistanceFreezeV2Error(
            "Planning authorization opened a wrong permission."
        )
    if not _strict_equal(payload.get("locks"), EXPECTED_PLAN_LOCKS):
        raise PortableWaterDistanceFreezeV2Error(
            "Planning authorization changed experiment locks."
        )
    if not _strict_equal(
        payload.get("freeze_decision_authorization_scope"),
        expected_plan_authorization_scope(),
    ):
        raise PortableWaterDistanceFreezeV2Error(
            "The narrow freeze-decision authorization scope changed."
        )


def _authenticate_exact_v7_plan(
    project_root: Path,
    payload: Mapping[str, Any],
    *,
    publication_commit: str,
    current_head: str,
) -> None:
    """Delegate full v7 reconstruction without creating an import cycle."""

    try:
        from la_heat.multicity.plan_freeze_transition_v7 import (
            authenticate_historical_v7_payload,
        )

        authenticate_historical_v7_payload(
            project_root,
            payload,
            publication_commit=publication_commit,
            current_head=current_head,
        )
    except Exception as exc:
        if isinstance(exc, PortableWaterDistanceFreezeV2Error):
            raise
        raise PortableWaterDistanceFreezeV2Error(
            "The complete historical v7 planning authorization failed."
        ) from exc


def _validate_runtime_against_v7_plan(
    plan: Mapping[str, Any],
    runtime: Mapping[str, Any],
) -> None:
    code_files = _require_mapping(plan.get("code_files"), label="v7 code_files")
    if set(code_files) != set(CODE_PATHS):
        raise PortableWaterDistanceFreezeV2Error(
            "The v7 frozen decision-runtime path set changed."
        )
    expected_hashes: dict[str, str] = {}
    for relative_path in CODE_PATHS:
        record = _require_mapping(
            code_files.get(relative_path),
            label=f"v7 code record {relative_path}",
        )
        value = record.get("sha256")
        if not isinstance(value, str):
            raise PortableWaterDistanceFreezeV2Error(
                f"The v7 code hash is missing: {relative_path}"
            )
        expected_hashes[relative_path] = value
    if not _strict_equal(runtime.get("files"), expected_hashes):
        raise PortableWaterDistanceFreezeV2Error(
            "The current V2 runtime hashes differ from the v7 frozen code files."
        )
    if not _strict_equal(runtime.get("relative_paths"), list(CODE_PATHS)):
        raise PortableWaterDistanceFreezeV2Error(
            "The current V2 runtime path order differs from v7."
        )


def _run_git(project_root: Path, *args: str, text: bool = True) -> str | bytes:
    completed = subprocess.run(
        ["git", "-C", str(project_root), *args],
        check=True,
        capture_output=True,
        text=text,
    )
    return completed.stdout.strip() if text else completed.stdout


def _git_preflight(
    project_root: Path,
    *,
    required_paths: tuple[str, ...],
) -> dict[str, Any]:
    branch = str(_run_git(project_root, "branch", "--show-current"))
    head = str(_run_git(project_root, "rev-parse", "HEAD"))
    origin = str(_run_git(project_root, "rev-parse", "origin/main"))
    status = str(
        _run_git(project_root, "status", "--porcelain=v1", "--untracked-files=all")
    )
    if branch != "main" or head != origin or status:
        raise PortableWaterDistanceFreezeV2Error(
            "V2 decision requires clean synchronized main."
        )
    if re.fullmatch(r"[0-9a-f]{40}", head) is None:
        raise PortableWaterDistanceFreezeV2Error("Git HEAD is invalid.")
    for relative_path in dict.fromkeys(required_paths):
        tree = str(
            _run_git(
                project_root,
                "ls-tree",
                head,
                "--",
                relative_path,
            )
        )
        parts = tree.split(maxsplit=3)
        if (
            len(parts) != 4
            or parts[0] not in {"100644", "100755"}
            or parts[1] != "blob"
            or parts[3] != relative_path
        ):
            raise PortableWaterDistanceFreezeV2Error(
                f"Required V2 input is not one regular HEAD blob: {relative_path}"
            )
        worktree_oid = str(
            _run_git(
                project_root,
                "hash-object",
                f"--path={relative_path}",
                "--",
                relative_path,
            )
        )
        if worktree_oid != parts[2]:
            raise PortableWaterDistanceFreezeV2Error(
                "A required V2 input differs from HEAD, including through an "
                f"index visibility flag: {relative_path}"
            )
    return {
        "branch": branch,
        "head": head,
        "origin_main": origin,
        "head_equals_origin_main": True,
        "working_tree_clean_before_decision": True,
    }


def _git_blob_sha256(project_root: Path, commit: str, relative_path: str) -> str:
    raw = _run_git(project_root, "show", f"{commit}:{relative_path}", text=False)
    if not isinstance(raw, bytes):
        raise AssertionError("Binary Git read returned text.")
    import hashlib

    return hashlib.sha256(raw).hexdigest()


def _require_publication_blob(
    project_root: Path,
    *,
    commit: str,
    relative_path: str,
    expected_sha256: str,
) -> None:
    try:
        observed = _git_blob_sha256(project_root, commit, relative_path)
    except subprocess.CalledProcessError as exc:
        raise PortableWaterDistanceFreezeV2Error(
            f"Cannot authenticate historical Git blob: {relative_path}"
        ) from exc
    if observed != expected_sha256:
        raise PortableWaterDistanceFreezeV2Error(
            f"Historical Git blob changed: {relative_path}"
        )


def _authenticate_terminal_publication(
    project_root: Path,
    *,
    terminal_file_sha256: str,
    expected_parent_commit: str,
    current_head: str,
) -> str:
    """Bind the append-only terminal to its unique first Git publication."""

    log = str(
        _run_git(
            project_root,
            "log",
            "--diff-filter=A",
            "--format=%H",
            "--",
            DEFAULT_MANIFEST.as_posix(),
        )
    )
    additions = [line for line in log.splitlines() if line]
    if len(additions) != 1:
        raise PortableWaterDistanceFreezeV2Error(
            "The V2 terminal must have one unique append-only Git publication."
        )
    publication_commit = additions[0]
    if (
        _git_blob_sha256(
            project_root,
            publication_commit,
            DEFAULT_MANIFEST.as_posix(),
        )
        != terminal_file_sha256
    ):
        raise PortableWaterDistanceFreezeV2Error(
            "The first V2 terminal publication differs from the current terminal."
        )
    ancestry = str(
        _run_git(
            project_root,
            "rev-list",
            "--parents",
            "-n",
            "1",
            publication_commit,
        )
    ).split()
    if len(ancestry) != 2 or ancestry[1] != expected_parent_commit:
        raise PortableWaterDistanceFreezeV2Error(
            "The V2 publication is not the direct child of its planning commit."
        )
    descendant_check = subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "merge-base",
            "--is-ancestor",
            publication_commit,
            current_head,
        ],
        check=False,
        capture_output=True,
    )
    if descendant_check.returncode != 0:
        raise PortableWaterDistanceFreezeV2Error(
            "The canonical V2 publication is not an ancestor of HEAD."
        )
    later_history = str(
        _run_git(
            project_root,
            "log",
            "--format=%H",
            f"{publication_commit}..{current_head}",
            "--",
            DEFAULT_MANIFEST.as_posix(),
        )
    )
    if later_history.strip():
        raise PortableWaterDistanceFreezeV2Error(
            "The append-only V2 terminal changed after its first publication."
        )
    return publication_commit


def _authenticate_prerequisites(
    project_root: Path,
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    records: dict[str, Any] = {}
    payloads: dict[str, Any] = {}
    prerequisites = _require_mapping(config.get("prerequisites"), label="prerequisites")
    for name in ("deferred_v1_decision", "l3_preregistration", "l3_v1_failure"):
        settings = _require_mapping(prerequisites.get(name), label=name)
        relative = str(settings["path"])
        payload, file_sha = _read_committed_json(project_root / relative)
        if (
            file_sha != settings["expected_file_sha256"]
            or payload.get("commit_sha256") != settings["expected_commit_sha256"]
            or payload.get("state") != settings["expected_state"]
        ):
            raise PortableWaterDistanceFreezeV2Error(
                f"Prerequisite identity changed: {name}"
            )
        payloads[name] = payload
        records[name] = {
            "path": relative,
            "bytes": (project_root / relative).stat().st_size,
            "file_sha256": file_sha,
            "commit_sha256": payload["commit_sha256"],
            "state": payload["state"],
        }
        publication = settings.get("publication_git_commit")
        if isinstance(publication, str):
            _require_publication_blob(
                project_root,
                commit=publication,
                relative_path=relative,
                expected_sha256=file_sha,
            )
            records[name]["publication_git_commit"] = publication

    amendment = _require_mapping(prerequisites.get("l3_v2_amendment"), label="amendment")
    amendment_path = project_root / str(amendment["path"])
    if sha256_file(amendment_path) != amendment["expected_file_sha256"]:
        raise PortableWaterDistanceFreezeV2Error("The V2 amendment bytes changed.")
    _require_publication_blob(
        project_root,
        commit=str(amendment["publication_git_commit"]),
        relative_path=str(amendment["path"]),
        expected_sha256=str(amendment["expected_file_sha256"]),
    )
    records["l3_v2_amendment"] = dict(amendment)

    success_settings = _require_mapping(
        prerequisites.get("l3_v2_success"), label="l3_v2_success"
    )
    success_path = project_root / str(success_settings["path"])
    success, success_sha = _read_committed_json(success_path)
    if (
        success_sha != SUCCESS_FILE_SHA256
        or success.get("commit_sha256") != SUCCESS_COMMIT_SHA256
        or success.get("state") != success_settings["expected_state"]
    ):
        raise PortableWaterDistanceFreezeV2Error(
            "The canonical L3 success identity changed."
        )
    _require_publication_blob(
        project_root,
        commit=SUCCESS_PUBLICATION_COMMIT,
        relative_path=str(success_settings["path"]),
        expected_sha256=SUCCESS_FILE_SHA256,
    )
    _validate_l3_success(success, config)
    payloads["l3_v2_success"] = success
    records["l3_v2_success"] = {
        "path": str(success_settings["path"]),
        "bytes": success_path.stat().st_size,
        "file_sha256": success_sha,
        "commit_sha256": success["commit_sha256"],
        "state": success["state"],
        "publication_git_commit": SUCCESS_PUBLICATION_COMMIT,
    }

    failure = payloads["l3_v1_failure"]
    failure_access = _require_mapping(
        failure.get("access_contract"), label="V1 failure access"
    )
    if (
        failure.get("phase") != "phase_1_structure"
        or failure.get("gate") != "selected_l2_normalized_wkb_sha256"
        or failure_access.get("probe_derived") is not False
        or failure_access.get("distance_values_computed") is not False
    ):
        raise PortableWaterDistanceFreezeV2Error(
            "The preserved V1 fail-before-distance evidence changed."
        )
    return records, payloads


def _validate_l3_success(
    success: Mapping[str, Any],
    config: Mapping[str, Any],
) -> None:
    decision = _require_mapping(success.get("decision"), label="success.decision")
    expected_decision = {
        "audit_passed": True,
        "source_frozen": False,
        "algorithm_frozen": False,
        "predictor_build_authorized": False,
        "next_safe_stage": (
            "separate_portable_water_distance_source_and_algorithm_freeze_decision"
        ),
    }
    if not _strict_equal(decision, expected_decision):
        raise PortableWaterDistanceFreezeV2Error(
            "The L3 audit decision gates changed."
        )
    hierarchy = _require_mapping(
        success.get("hierarchy_audit"), label="success.hierarchy_audit"
    )
    numerical = _require_mapping(
        success.get("numerical_audit"), label="success.numerical_audit"
    )
    if (
        hierarchy.get("all_structural_gates_passed") is not True
        or numerical.get("all_numerical_gates_passed") is not True
    ):
        raise PortableWaterDistanceFreezeV2Error(
            "The L3 audit did not retain every completed gate."
        )

    source_lock = _require_mapping(config.get("source_lock"), label="source_lock")
    archive = _require_mapping(success.get("source_archive"), label="source_archive")
    archive_expected = {
        "bytes": source_lock["archive_bytes"],
        "sha256": source_lock["archive_sha256"],
        "published_md5": source_lock["published_md5"],
        "member_count": source_lock["archive_member_count"],
        "member_inventory_sha256": source_lock["archive_member_inventory_sha256"],
        "authorized_member_count": source_lock["authorized_l1_l2_l3_member_count"],
        "unauthorized_member_open_count": 0,
        "geometry_exported_or_redistributed": False,
    }
    for key, expected in archive_expected.items():
        if not _strict_equal(archive.get(key), expected):
            raise PortableWaterDistanceFreezeV2Error(
                f"Authenticated source_archive.{key} changed."
            )

    layers = _require_mapping(success.get("source_layers"), label="source_layers")
    l1 = _require_mapping(layers.get("l1_original"), label="l1_original")
    l2 = _require_mapping(layers.get("l2_original"), label="l2_original")
    repair = _require_mapping(layers.get("l1_repair"), label="l1_repair")
    selected_l2 = _require_mapping(layers.get("selected_l2"), label="selected_l2")
    l3 = _require_mapping(layers.get("l3"), label="l3")
    selected_l3 = _require_mapping(
        l3.get("selected_direct_descendants"), label="selected_l3"
    )
    topology = _require_mapping(l3.get("topology"), label="l3.topology")
    checks = (
        (l1.get("row_count"), source_lock["l1_row_count"], "L1 rows"),
        (
            l1.get("attribute_geometry_semantic_sha256"),
            source_lock["l1_full_layer_semantic_sha256"],
            "L1 semantic hash",
        ),
        (
            repair.get("source_id"),
            source_lock["l1_repaired_source_id"],
            "L1 repair identity",
        ),
        (
            repair.get("original_normalized_wkb_sha256"),
            source_lock["l1_original_normalized_wkb_sha256"],
            "L1 original hash",
        ),
        (
            repair.get("repaired_normalized_wkb_sha256"),
            source_lock["l1_repaired_normalized_wkb_sha256"],
            "L1 repaired hash",
        ),
        (l2.get("row_count"), source_lock["l2_row_count"], "L2 rows"),
        (
            l2.get("attribute_geometry_semantic_sha256"),
            source_lock["l2_full_layer_semantic_sha256"],
            "L2 semantic hash",
        ),
        (
            selected_l2.get("source_ids"),
            source_lock["selected_l2_source_ids"],
            "selected L2 IDs",
        ),
        (
            selected_l2.get("normalized_wkb_sha256"),
            source_lock["selected_l2_normalized_wkb_sha256"],
            "selected L2 hashes",
        ),
        (l3.get("row_count"), source_lock["l3_row_count"], "L3 rows"),
        (
            l3.get("full_layer_attribute_geometry_semantic_sha256"),
            source_lock["l3_full_layer_semantic_sha256"],
            "L3 semantic hash",
        ),
        (
            selected_l3.get("row_count"),
            source_lock["selected_l3_direct_descendant_count"],
            "selected L3 rows",
        ),
        (
            list(selected_l3.get("counts_by_parent", {}).values()),
            source_lock["selected_l3_counts_by_parent"],
            "selected L3 parent counts",
        ),
        (
            selected_l3.get("attribute_geometry_semantic_sha256"),
            source_lock["selected_l3_semantic_sha256"],
            "selected L3 semantic hash",
        ),
        (
            selected_l3.get("exterior_linework_semantic_sha256"),
            source_lock["selected_l3_exterior_linework_semantic_sha256"],
            "selected L3 linework hash",
        ),
        (
            topology.get("selected_exterior_segment_count"),
            source_lock["selected_l3_exterior_segment_count"],
            "selected L3 segment count",
        ),
    )
    for observed, expected, label in checks:
        if not _strict_equal(observed, expected):
            raise PortableWaterDistanceFreezeV2Error(f"{label} changed.")

    audit_locks = _require_mapping(success.get("locks"), label="success.locks")
    if any(value is not False for value in audit_locks.values()):
        raise PortableWaterDistanceFreezeV2Error(
            "The source audit unexpectedly created a lock."
        )


def _diagnostic_evidence(
    success: Mapping[str, Any],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Extract the seven source-only values from the tracked success record."""

    settings = _require_mapping(
        _require_mapping(config.get("prerequisites"), label="prerequisites").get(
            "source_only_diagnostics"
        ),
        label="source_only_diagnostics",
    )
    numerical = _require_mapping(
        success.get("numerical_audit"),
        label="success.numerical_audit",
    )
    fixed = numerical.get("fixed_v2_replays")
    probes = numerical.get("real_l3_probes")
    if not isinstance(fixed, list) or not isinstance(probes, list):
        raise PortableWaterDistanceFreezeV2Error(
            "The tracked source-only diagnostic collections changed."
        )
    if (
        len(fixed) != settings["fixed_v2_replay_rows"]
        or len(probes) != settings["real_l3_probe_rows"]
    ):
        raise PortableWaterDistanceFreezeV2Error(
            "The tracked source-only diagnostic row groups changed."
        )

    rows: list[dict[str, Any]] = []
    for raw in fixed:
        record = _require_mapping(raw, label="fixed V2 replay")
        rows.append(
            {
                "point_id": record["point_id"],
                "evidence_kind": record["point_kind"],
                "distance_m": record["distance_m"],
                "l1_l2_only_distance_m": record["l1_l2_only_distance_m"],
                "l3_improvement_m": 0.0,
            }
        )
    for raw in probes:
        record = _require_mapping(raw, label="real L3 probe")
        rows.append(
            {
                "point_id": record["point_id"],
                "evidence_kind": record["point_kind"],
                "distance_m": record["inclusive_distance_m"],
                "l1_l2_only_distance_m": record["l1_l2_only_distance_m"],
                "l3_improvement_m": record["strict_improvement_m"],
            }
        )
    rows.sort(key=lambda row: str(row["point_id"]))
    observed = {
        str(row["point_id"]): (
            row["distance_m"],
            row["l1_l2_only_distance_m"],
            row["l3_improvement_m"],
        )
        for row in rows
    }
    if not _strict_equal(observed, _EXPECTED_DIAGNOSTIC_VALUES):
        raise PortableWaterDistanceFreezeV2Error(
            "The tracked source-only diagnostic values changed."
        )
    if len(rows) != settings["expected_rows"]:
        raise PortableWaterDistanceFreezeV2Error(
            "The tracked source-only diagnostic row count changed."
        )
    expected_source = {
        "path": settings["source_manifest_path"],
        "file_sha256": settings["source_manifest_file_sha256"],
        "commit_sha256": settings["source_manifest_commit_sha256"],
    }
    if not _strict_equal(
        expected_source,
        {
            "path": (
                "manifests/multicity/reviews/portable_water_distance/"
                "GSHHG_L3_HIERARCHY_AUDIT.json"
            ),
            "file_sha256": SUCCESS_FILE_SHA256,
            "commit_sha256": SUCCESS_COMMIT_SHA256,
        },
    ):
        raise PortableWaterDistanceFreezeV2Error(
            "The source-only diagnostic manifest identity changed."
        )
    return {
        "source": expected_source,
        "rows": rows,
        "row_count": len(rows),
        "values_commit_sha256": canonical_sha256(rows),
        "ignored_local_csv_opened": False,
    }


def _build_payload(
    config_path: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    project_root = config_path.parents[2]
    if (project_root / DEFAULT_MANIFEST).exists():
        raise PortableWaterDistanceFreezeV2Error(
            "The append-only V2 decision already exists."
        )
    if not (project_root / V1_MANIFEST).is_file():
        raise PortableWaterDistanceFreezeV2Error(
            "The preserved V1 deferred decision is missing."
        )
    repository = _git_preflight(
        project_root,
        required_paths=(PLAN_PATH.as_posix(), *CODE_PATHS, *PREREQUISITE_PATHS),
    )

    plan, plan_sha = _read_committed_json(project_root / PLAN_PATH)
    _validate_plan_payload(plan)
    _authenticate_exact_v7_plan(
        project_root,
        plan,
        publication_commit=str(repository["head"]),
        current_head=str(repository["head"]),
    )
    prerequisites, prerequisite_payloads = _authenticate_prerequisites(
        project_root,
        config,
    )
    diagnostics = _diagnostic_evidence(
        prerequisite_payloads["l3_v2_success"],
        config,
    )

    code_sha, code_runtime = code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=CODE_PATHS,
        algorithm_version=ALGORITHM_VERSION,
    )
    code_runtime["relative_paths"] = list(CODE_PATHS)
    code_runtime["sha256"] = code_sha
    _validate_runtime_against_v7_plan(plan, code_runtime)

    return _compose_payload(
        config_path=config_path,
        config=config,
        repository=repository,
        plan=plan,
        plan_file_sha256=plan_sha,
        plan_bytes=(project_root / PLAN_PATH).stat().st_size,
        prerequisites=prerequisites,
        diagnostics=diagnostics,
        code_runtime=code_runtime,
    )


def _compose_payload(
    *,
    config_path: Path,
    config: Mapping[str, Any],
    repository: Mapping[str, Any],
    plan: Mapping[str, Any],
    plan_file_sha256: str,
    plan_bytes: int,
    prerequisites: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
    code_runtime: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the one exact terminal from already authenticated inputs."""

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": COMPLETE_STATE,
        "decision_id": config["decision"]["decision_id"],
        "decision_date": config["decision"]["decision_date"],
        "decision_scope": config["decision"]["scope"],
        "outcome": OUTCOME,
        "experiment_id": config["decision"]["expected_experiment_id"],
        "experiment_semantic_sha256": config["decision"][
            "expected_experiment_semantic_sha256"
        ],
        "repository": repository,
        "planning_authorization": {
            "path": PLAN_PATH.as_posix(),
            "bytes": plan_bytes,
            "file_sha256": plan_file_sha256,
            "commit_sha256": plan["commit_sha256"],
            "publication_git_commit": repository["head"],
            "state": plan["state"],
            "planning_stage": plan["planning_stage"],
            "authorized_now": plan["authorized_now"],
        },
        "prerequisites": prerequisites,
        "diagnostic_table": diagnostics,
        "source_lock": config["source_lock"],
        "algorithm_lock": config["algorithm_lock"],
        "license_record": config["license_record"],
        "locks": config["locks"],
        "access_contract": config["access_contract"],
        "next_gate": config["next_gate"],
        "decision_config": {
            "path": DEFAULT_CONFIG.as_posix(),
            "bytes": config_path.stat().st_size,
            "file_sha256": sha256_file(config_path),
        },
        "code_runtime": code_runtime,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def _authenticate_existing(
    config_path: Path,
    config: dict[str, Any],
    destination: Path,
) -> dict[str, Any]:
    project_root = config_path.parents[2]
    payload, payload_file_sha = _read_committed_json(destination)
    current_repository = _git_preflight(
        project_root,
        required_paths=(
            PLAN_PATH.as_posix(),
            *CODE_PATHS,
            *PREREQUISITE_PATHS,
            DEFAULT_MANIFEST.as_posix(),
        ),
    )
    plan_record = _require_mapping(
        payload.get("planning_authorization"), label="planning_authorization"
    )
    plan_commit = plan_record.get("publication_git_commit")
    if not isinstance(plan_commit, str):
        raise PortableWaterDistanceFreezeV2Error(
            "The historical v7 planning commit is missing."
        )
    _authenticate_terminal_publication(
        project_root,
        terminal_file_sha256=payload_file_sha,
        expected_parent_commit=plan_commit,
        current_head=str(current_repository["head"]),
    )
    historical_plan = _run_git(
        project_root,
        "show",
        f"{plan_commit}:{PLAN_PATH.as_posix()}",
        text=False,
    )
    if not isinstance(historical_plan, bytes):
        raise AssertionError("Historical planning bytes were decoded.")
    import hashlib

    if hashlib.sha256(historical_plan).hexdigest() != plan_record.get("file_sha256"):
        raise PortableWaterDistanceFreezeV2Error(
            "The historical v7 planning blob changed."
        )
    try:
        parsed_plan = json.loads(historical_plan.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PortableWaterDistanceFreezeV2Error(
            "Cannot parse the historical v7 planning blob."
        ) from exc
    if not isinstance(parsed_plan, dict):
        raise PortableWaterDistanceFreezeV2Error(
            "The historical v7 planning blob is not an object."
        )
    recorded = parsed_plan.get("commit_sha256")
    body = {
        key: value for key, value in parsed_plan.items() if key != "commit_sha256"
    }
    if recorded != canonical_sha256(body):
        raise PortableWaterDistanceFreezeV2Error(
            "The historical v7 planning internal commit is invalid."
        )
    _validate_plan_payload(parsed_plan)
    _authenticate_exact_v7_plan(
        project_root,
        parsed_plan,
        publication_commit=plan_commit,
        current_head=str(current_repository["head"]),
    )
    if (
        plan_record.get("bytes") != len(historical_plan)
        or plan_record.get("commit_sha256") != parsed_plan.get("commit_sha256")
    ):
        raise PortableWaterDistanceFreezeV2Error(
            "The historical v7 planning identity changed."
        )
    prerequisites, prerequisite_payloads = _authenticate_prerequisites(
        project_root,
        config,
    )
    diagnostics = _diagnostic_evidence(
        prerequisite_payloads["l3_v2_success"],
        config,
    )

    code_sha, expected_runtime = code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=CODE_PATHS,
        algorithm_version=ALGORITHM_VERSION,
    )
    expected_runtime["relative_paths"] = list(CODE_PATHS)
    expected_runtime["sha256"] = code_sha
    _validate_runtime_against_v7_plan(parsed_plan, expected_runtime)

    expected_repository = {
        "branch": "main",
        "head": plan_commit,
        "origin_main": plan_commit,
        "head_equals_origin_main": True,
        "working_tree_clean_before_decision": True,
    }
    expected = _compose_payload(
        config_path=config_path,
        config=config,
        repository=expected_repository,
        plan=parsed_plan,
        plan_file_sha256=str(plan_record.get("file_sha256")),
        plan_bytes=len(historical_plan),
        prerequisites=prerequisites,
        diagnostics=diagnostics,
        code_runtime=expected_runtime,
    )
    if not _strict_equal(payload, expected):
        raise PortableWaterDistanceFreezeV2Error(
            "The committed V2 decision differs from the one exact authenticated "
            "terminal."
        )
    return payload


def audit_portable_water_distance_freeze_v2(
    config_path: str | Path = DEFAULT_CONFIG,
    *,
    output_path: str | Path = DEFAULT_MANIFEST,
    write: bool = True,
) -> dict[str, Any]:
    """Create or authenticate the append-only V2 freeze decision."""

    resolved_config, config = _read_config(config_path)
    project_root = resolved_config.parents[2]
    destination = Path(output_path)
    if not destination.is_absolute():
        destination = project_root / destination
    if destination.resolve() == (project_root / V1_MANIFEST).resolve():
        raise PortableWaterDistanceFreezeV2Error(
            "V2 may never overwrite the preserved V1 deferred decision."
        )
    if destination.resolve() != (project_root / DEFAULT_MANIFEST).resolve():
        raise PortableWaterDistanceFreezeV2Error(
            "V2 may only create or authenticate the canonical append-only output."
        )
    if destination.exists():
        return _authenticate_existing(resolved_config, config, destination)
    if not write:
        raise FileNotFoundError(destination)
    payload = _build_payload(resolved_config, config)
    atomic_json(payload, destination)
    return payload
