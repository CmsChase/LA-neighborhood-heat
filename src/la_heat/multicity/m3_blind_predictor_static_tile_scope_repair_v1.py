"""Repair a false two-terrain-tile assumption using the frozen city scope."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from la_heat.multicity import m3_blind_predictor_sentinel_lineage_city_repair_v1 as parent
from la_heat.multicity import m3_blind_predictor_sentinel_static_assembly_v1 as assembly
from la_heat.multicity import portable_predictor_components as components
from la_heat.provenance import atomic_json, canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-blind-predictor-static-tile-scope-repair-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/"
    "M3_BLIND_PREDICTOR_STATIC_TILE_SCOPE_REPAIR_V1_AUTHORIZATION.json"
)
EXPECTED_PARENT_COMMIT: Final = (
    "8c0d87761adf3c5cd2d588bcc39a44a1db5626035ec1300d90c5122fe2a4c751"
)
CODE_PATHS: Final = (
    "scripts/run_m3_blind_predictor_static_tile_scope_repair_v1.py",
    "src/la_heat/multicity/m3_blind_predictor_static_tile_scope_repair_v1.py",
)


class M3BlindStaticTileScopeRepairError(RuntimeError):
    """Raised when the frozen static tile scope cannot be enforced."""


def _read_committed(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if payload.get("commit_sha256") != canonical_sha256(body):
        raise M3BlindStaticTileScopeRepairError(f"Invalid commit: {path}")
    return payload


def _record(root: Path, relative: str | Path) -> dict[str, Any]:
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise M3BlindStaticTileScopeRepairError(f"Missing file: {relative}")
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    parent_auth = parent.authenticate_authorization(root)
    if parent_auth.get("commit_sha256") != EXPECTED_PARENT_COMMIT:
        raise M3BlindStaticTileScopeRepairError("Parent authorization changed.")
    files = [_record(root, path) for path in CODE_PATHS]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "m3_blind_predictor_static_tile_scope_repair_authorized",
        "parent_authorization": {
            **_record(root, parent.AUTHORIZATION_PATH),
            "commit_sha256": parent_auth["commit_sha256"],
        },
        "incident": {
            "city_id": "miami_fl",
            "error_type": "M3BlindAssemblyError",
            "false_assumption": "every city requires exactly two SRTM tiles",
            "repair": "use only the exact acquired terrain paths frozen by city scope",
        },
        "code_identity": {"files": files, "set_sha256": canonical_sha256(files)},
        "permissions": {
            "replace_only_static_source_tile_cardinality_check": True,
            "reuse_parent_value_read_and_output_permissions": True,
            "network_or_href_reads": False,
            "read_daymet_landsat_qa_or_target_values": False,
            "fit_predict_score_or_evaluate": False,
        },
        "audit": {"value_files_opened_or_statted_while_authorizing": 0},
        "next_safe_stage": "retry_canary_through_static_tile_scope_repair_runner",
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
            raise M3BlindStaticTileScopeRepairError("Append-only authorization drifted.")
    else:
        atomic_json(expected, path)
    return authenticate_authorization(root)


def authenticate_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observed = _read_committed(root / AUTHORIZATION_PATH)
    if observed != build_authorization(root):
        raise M3BlindStaticTileScopeRepairError("Static tile authorization drifted.")
    return observed


def run(project_root: str | Path, *, canary: bool) -> dict[str, Any]:
    root = Path(project_root).resolve()
    authenticate_authorization(root)
    original_configure = assembly._configure

    def configure(scoped_root: Path) -> dict[str, Any]:
        contexts = original_configure(scoped_root)

        def static_sources(_root: Path, city_id: str) -> components.StaticSourcePaths:
            authenticate_authorization(root)
            base = root / assembly.acquired.OUTPUT_ROOT / "static" / city_id
            land = base / "nlcd_2016_land_cover.tif"
            impervious = base / "nlcd_2016_impervious.tif"
            terrain = tuple(sorted((base / "terrain").glob("*.tif")))
            expected = tuple(
                sorted(
                    Path(task["path"])
                    for task in assembly.acquired._static_tasks(root)
                    if task.get("city_id") == city_id and task.get("kind") == "srtm"
                )
            )
            if not terrain or terrain != expected:
                raise M3BlindStaticTileScopeRepairError("Terrain scope changed.")
            records = [
                assembly._verify_acquired_file(root, path)
                for path in (land, impervious, *terrain)
            ]
            return components.StaticSourcePaths(land, impervious, terrain, tuple(records))

        components._static_source_paths = static_sources
        return contexts

    assembly._configure = configure
    try:
        return parent.run(root, canary=canary)
    finally:
        assembly._configure = original_configure
