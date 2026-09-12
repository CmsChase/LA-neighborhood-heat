"""Append-only repair for an already-correct Sentinel lineage city column."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

import pandas as pd

from la_heat.multicity import m3_blind_predictor_sentinel_membership_repair_v1 as parent
from la_heat.provenance import atomic_json, canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-blind-predictor-sentinel-lineage-city-repair-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_BLIND_PREDICTOR_SENTINEL_LINEAGE_CITY_REPAIR_V1_AUTHORIZATION.json"
)
EXPECTED_PARENT_COMMIT: Final = (
    "b44c0cf8098137945c60d993f2c4e2b890a4a9c71da86cff29b4a146d0c89ff7"
)
CODE_PATHS: Final = (
    "scripts/run_m3_blind_predictor_sentinel_lineage_city_repair_v1.py",
    "src/la_heat/multicity/m3_blind_predictor_sentinel_lineage_city_repair_v1.py",
)


class M3BlindLineageCityRepairError(RuntimeError):
    """Raised when the lineage city repair cannot be proven safe."""


def _read_committed(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if payload.get("commit_sha256") != canonical_sha256(body):
        raise M3BlindLineageCityRepairError(f"Invalid commit: {path}")
    return payload


def _record(root: Path, relative: str | Path) -> dict[str, Any]:
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise M3BlindLineageCityRepairError(f"Missing file: {relative}")
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    parent_auth = parent.authenticate_authorization(root)
    if parent_auth.get("commit_sha256") != EXPECTED_PARENT_COMMIT:
        raise M3BlindLineageCityRepairError("Parent repair authorization changed.")
    files = [_record(root, path) for path in CODE_PATHS]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "m3_blind_predictor_sentinel_lineage_city_repair_authorized",
        "parent_repair_authorization": {
            **_record(root, parent.AUTHORIZATION_PATH),
            "commit_sha256": parent_auth["commit_sha256"],
        },
        "incident": {
            "phase": "offline_assembly_canary",
            "city_id": "miami_fl",
            "error_type": "ValueError",
            "cause": "lineage already contains the correct city_id before canonical insert",
            "operation": "validate all existing values, drop duplicate, reinsert same value",
        },
        "code_identity": {"files": files, "set_sha256": canonical_sha256(files)},
        "permissions": {
            "apply_only_validated_duplicate_city_column_repair": True,
            "reuse_parent_value_read_and_output_permissions": True,
            "network_or_href_reads": False,
            "read_daymet_landsat_qa_or_target_values": False,
            "fit_predict_score_or_evaluate": False,
        },
        "audit": {"value_files_opened_or_statted_while_authorizing": 0},
        "next_safe_stage": "retry_canary_through_lineage_city_repair_runner",
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
            raise M3BlindLineageCityRepairError("Append-only authorization drifted.")
    else:
        atomic_json(expected, path)
    return authenticate_authorization(root)


def authenticate_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observed = _read_committed(root / AUTHORIZATION_PATH)
    if observed != build_authorization(root):
        raise M3BlindLineageCityRepairError("Lineage city authorization drifted.")
    return observed


@contextmanager
def _repair_insert(project_root: Path) -> Iterator[list[int]]:
    authenticate_authorization(project_root)
    original = pd.DataFrame.insert
    applied = [0]

    def repaired(
        frame: pd.DataFrame,
        loc: int,
        column: Any,
        value: Any,
        allow_duplicates: Any = False,
    ) -> None:
        if loc == 0 and column == "city_id" and "city_id" in frame.columns:
            authenticate_authorization(project_root)
            observed = frame["city_id"]
            if observed.isna().any() or not observed.astype(str).eq(str(value)).all():
                raise M3BlindLineageCityRepairError("Existing city_id values changed.")
            frame.drop(columns=["city_id"], inplace=True)
            applied[0] += 1
        original(frame, loc, column, value, allow_duplicates=allow_duplicates)

    pd.DataFrame.insert = repaired  # type: ignore[method-assign]
    try:
        yield applied
    finally:
        pd.DataFrame.insert = original  # type: ignore[method-assign]


def run(project_root: str | Path, *, canary: bool) -> dict[str, Any]:
    root = Path(project_root).resolve()
    authenticate_authorization(root)
    with _repair_insert(root) as applied:
        result = parent.run(root, canary=canary)
    if applied[0] < 1:
        raise M3BlindLineageCityRepairError("Expected lineage city repair was not applied.")
    result = dict(result)
    result["lineage_city_repairs_applied"] = applied[0]
    return result
