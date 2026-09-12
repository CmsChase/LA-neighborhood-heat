"""Append-only repair for valid target-date/acquisition many-to-many membership."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

import pandas as pd

from la_heat.multicity import m3_blind_predictor_sentinel_static_assembly_v1 as parent
from la_heat.provenance import atomic_json, canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-blind-predictor-sentinel-membership-repair-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_BLIND_PREDICTOR_SENTINEL_MEMBERSHIP_REPAIR_V1_AUTHORIZATION.json"
)
EXPECTED_PARENT_AUTHORIZATION_COMMIT: Final = (
    "3311d3876dcbe9da53646a5127832616de0d67bd82e19907c6ed843b1565eaaf"
)
CODE_PATHS: Final = (
    "scripts/run_m3_blind_predictor_sentinel_membership_repair_v1.py",
    "src/la_heat/multicity/m3_blind_predictor_sentinel_membership_repair_v1.py",
)
JOIN_KEYS: Final = ("physical_acquisition_id", "acquisition_local_date")


class M3BlindMembershipRepairError(RuntimeError):
    """Raised when the narrow repair cannot be authenticated or applied."""


def _read_committed(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    unsigned = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if payload.get("commit_sha256") != canonical_sha256(unsigned):
        raise M3BlindMembershipRepairError(f"Invalid commit: {path}")
    return payload


def _record(root: Path, relative: str | Path) -> dict[str, Any]:
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise M3BlindMembershipRepairError(f"Missing file: {relative}")
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    parent_auth = parent.authenticate_authorization(root)
    if parent_auth.get("commit_sha256") != EXPECTED_PARENT_AUTHORIZATION_COMMIT:
        raise M3BlindMembershipRepairError("Parent authorization changed.")
    files = [_record(root, path) for path in CODE_PATHS]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "m3_blind_predictor_sentinel_membership_repair_authorized",
        "parent_authorization": {
            **_record(root, parent.AUTHORIZATION_PATH),
            "commit_sha256": parent_auth["commit_sha256"],
        },
        "incident": {
            "phase": "offline_assembly_canary",
            "city_id": "miami_fl",
            "error_type": "pandas.errors.MergeError",
            "old_validation": "one_to_many",
            "new_validation": "many_to_many",
            "join_keys": list(JOIN_KEYS),
            "reason": (
                "one physical acquisition legitimately belongs to multiple "
                "target-date windows"
            ),
        },
        "code_identity": {"files": files, "set_sha256": canonical_sha256(files)},
        "permissions": {
            "apply_only_exact_membership_merge_validation_repair": True,
            "reuse_parent_value_read_and_output_permissions": True,
            "network_or_href_reads": False,
            "read_daymet_landsat_qa_or_target_values": False,
            "fit_predict_score_or_evaluate": False,
        },
        "audit": {
            "value_files_opened_or_statted_while_authorizing": 0,
            "network_or_href_reads": 0,
            "blind_targets_sealed": True,
        },
        "next_safe_stage": "retry_parent_offline_canary_through_narrow_repair_runner",
    }
    payload["claim_id"] = canonical_sha256(payload)
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def create_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    expected = build_authorization(root)
    path = root / AUTHORIZATION_PATH
    if path.exists():
        if _read_committed(path) != expected:
            raise M3BlindMembershipRepairError("Append-only repair authorization drifted.")
    else:
        atomic_json(expected, path)
    return authenticate_authorization(root)


def authenticate_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observed = _read_committed(root / AUTHORIZATION_PATH)
    if observed != build_authorization(root):
        raise M3BlindMembershipRepairError("Repair authorization drifted.")
    return observed


@contextmanager
def _repair_merge(project_root: Path) -> Iterator[list[int]]:
    authenticate_authorization(project_root)
    original = pd.DataFrame.merge
    applied = [0]

    def repaired(frame: pd.DataFrame, *args: Any, **kwargs: Any) -> pd.DataFrame:
        on = kwargs.get("on")
        other = args[0] if args else kwargs.get("right")
        exact = (
            kwargs.get("validate") == "one_to_many"
            and tuple(on or ()) == JOIN_KEYS
            and "target_date" in frame.columns
            and "lag_days" in frame.columns
            and isinstance(other, pd.DataFrame)
            and "tract_geoid" in other.columns
        )
        if exact:
            authenticate_authorization(project_root)
            kwargs["validate"] = "many_to_many"
            applied[0] += 1
        return original(frame, *args, **kwargs)

    pd.DataFrame.merge = repaired  # type: ignore[method-assign]
    try:
        yield applied
    finally:
        pd.DataFrame.merge = original  # type: ignore[method-assign]


def run(project_root: str | Path, *, canary: bool) -> dict[str, Any]:
    root = Path(project_root).resolve()
    authenticate_authorization(root)
    with _repair_merge(root) as applied:
        result = parent.run(root, canary=canary)
    if applied[0] < 1:
        raise M3BlindMembershipRepairError("Expected membership repair was not applied.")
    result = dict(result)
    result["membership_many_to_many_repairs_applied"] = applied[0]
    return result
