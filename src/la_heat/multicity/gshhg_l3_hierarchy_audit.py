"""Run the preregistered, target-blind GSHHG L3 hierarchy audit.

The public entry point is deliberately fail closed.  Before opening the source
archive it authenticates planning schema v6, a clean synchronized ``main``
branch, and the exact tracked executor bytes.  The archive reader may stream
only the twelve full-resolution L1/L2/L3 shapefile members authorized by the
planning transition.  L4, Census, eligible-land, target, model, prediction,
and result artifacts are outside this module's access surface.

The audit has two irreversible phases:

1. authenticate source bytes and every preregistered structural gate;
2. only from a completed :class:`StructuralAuditBundle`, derive probes and
   execute the frozen numerical gates.

No geometry is exported.  The twelve authenticated members are staged only in
an isolated operating-system temporary directory so GDAL cannot inspect any
other archive member; that directory is removed before phase 1 returns.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import io
import json
import math
import os
import re
import stat
import subprocess
import tempfile
import tomllib
import unicodedata
import uuid
import zipfile
import zlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from pyproj import CRS, Transformer
from pyproj.exceptions import ProjError
from shapely.geometry import LineString, Point

from la_heat.multicity.gshhg_geometry_pilot import (
    WGS84,
    GshhgGeometryPilotError,
    _audit_source_layer,
    _chunk_coordinate_run,
    _conservative_envelope,
    _coordinate_runs_without_dateline_seams,
    _normalized_wkb_sha256,
    _read_exact_configs,
    _thread_and_query_chunk_audit,
    audit_l1_dateline_segments,
    geodesic_reference_distances,
    nearest_projected_bruteforce,
    nearest_projected_strtree,
    repair_predeclared_l1_geometry,
    require_projected_geodesic_parity,
)
from la_heat.multicity.gshhg_l3_hierarchy_preregistration import (
    DEFAULT_CONFIG,
    EXPECTED_CONFIG_SHA256,
    EXPECTED_SELECTED_L2_IDS,
    _read_config,
)
from la_heat.multicity.plan_audit import (
    _git_preflight,
    audit_multicity_plan,
)
from la_heat.provenance import canonical_sha256, code_runtime_fingerprint, sha256_file

SCHEMA_VERSION: Final = 1
V1_ALGORITHM_VERSION: Final = "gshhg-l3-hierarchy-audit-v1"
ALGORITHM_VERSION: Final = "gshhg-l3-hierarchy-audit-v2"
COMPLETE_STATE: Final = "gshhg_l3_hierarchy_audit_v2_complete_source_not_frozen"
V1_FAILURE_STATE: Final = "gshhg_l3_hierarchy_audit_v1_failed"
FAILURE_STATE: Final = "gshhg_l3_hierarchy_audit_v2_failed"

DEFAULT_MANIFEST: Final = Path(
    "manifests/multicity/reviews/portable_water_distance/GSHHG_L3_HIERARCHY_AUDIT.json"
)
DEFAULT_V1_FAILURE_MANIFEST: Final = Path(
    "manifests/multicity/reviews/portable_water_distance/GSHHG_L3_HIERARCHY_AUDIT_V1_FAILURE.json"
)
DEFAULT_FAILURE_MANIFEST: Final = Path(
    "manifests/multicity/reviews/portable_water_distance/GSHHG_L3_HIERARCHY_AUDIT_V2_FAILURE.json"
)
DEFAULT_DIAGNOSTIC_TABLE: Final = Path(
    "data/interim/multicity/water_distance/gshhg_l3_hierarchy_audit/diagnostic_distances.csv"
)
AMENDMENT_PATH: Final = (
    "configs/multicity/gshhg_l3_hierarchy_audit_amendment_v2.toml"
)
PLAN_PATH: Final = "manifests/multicity/PLAN_READINESS.json"
PREREGISTRATION_PATH: Final = (
    "manifests/multicity/reviews/portable_water_distance/"
    "GSHHG_L3_HIERARCHY_AUDIT_PREREGISTRATION.json"
)
PILOT_PATH: Final = "manifests/multicity/reviews/portable_water_distance/GSHHG_GEOMETRY_PILOT.json"
FREEZE_DECISION_PATH: Final = (
    "manifests/multicity/reviews/portable_water_distance/WATER_DISTANCE_FREEZE_DECISION.json"
)
EXPECTED_PREREGISTRATION_FILE_SHA256: Final = (
    "ecb21bfa31f98dfe275f113ee13909fd30276e049ee0d2a05fca2b2a2bd4b47f"
)
EXPECTED_PREREGISTRATION_COMMIT_SHA256: Final = (
    "7be642a7fd099d026c828e018d699f1c6a885de0d180d50ce7eda00e17e694a7"
)
EXPECTED_PLAN_FILE_SHA256: Final = (
    "9a8f8b93ccfa89bf43354cb09d6d92fee1b436eb5edbd227b75d794dd49cac6c"
)
EXPECTED_PLAN_COMMIT_SHA256: Final = (
    "1789d828f212e0cd65f87c9427eb4a7fbd1697cc7170ebb98a80806659afbc86"
)
EXPECTED_AMENDMENT_FILE_SHA256: Final = (
    "c60c2d699e94bca832a78b4959db9a5333b2aa3ae37bfdd72d9c0eb6f37ff127"
)
AMENDMENT_PUBLICATION_GIT_COMMIT: Final = (
    "e07ef369ea3310ec67956b06436f793f01c89942"
)
EXPECTED_AMENDMENT_BLOB_SHA1: Final = "e19275b922498a6b72f28341f5bcaf2647e58c7e"
EXPECTED_V1_FAILURE_FILE_SHA256: Final = (
    "b5eb32e3de1702250e36a7eb81b2ea0c78551930a7f92abe5278d21c05a0ea9e"
)
EXPECTED_V1_FAILURE_COMMIT_SHA256: Final = (
    "e5b8e1e242276bcb530990ee070739f84e48177c431e556cfebb4819c92ea067"
)
V1_FAILURE_PUBLICATION_GIT_COMMIT: Final = (
    "fbf20ed7a601af8e9f77ad768f1267b8a6503a0d"
)
V1_RUN_HEAD: Final = "ab51a9506d77b7ac0efcdfb97e494c665cd80e5b"
CORRECTED_SOURCE_ID: Final = "180515"
V1_EXPECTED_SOURCE_HASH: Final = (
    "858c762462d6573e3f2ce25356ba1e193c1ca47a1f1ecc327710a49ff1fe014c"
)
V2_CORRECTED_SOURCE_HASH: Final = (
    "858c762462d6573e3f2ce25356ba1e193c1ca47a1f1ecc327710a49ff1fe014a"
)

LAYER_MEMBER_QUARTETS: Final = {
    level: tuple(
        f"GSHHS_shp/f/GSHHS_f_L{level}.{suffix}" for suffix in ("dbf", "prj", "shp", "shx")
    )
    for level in (1, 2, 3)
}
AUTHORIZED_MEMBERS: Final = tuple(
    member for level in (1, 2, 3) for member in LAYER_MEMBER_QUARTETS[level]
)
EXPECTED_COLUMNS: Final = (
    "id",
    "level",
    "source",
    "parent_id",
    "sibling_id",
    "area",
    "geometry",
)
EXPECTED_DTYPES: Final = {
    "id": "str",
    "level": "int32",
    "source": "str",
    "parent_id": "int32",
    "sibling_id": "int32",
    "area": "float64",
    "geometry": "geometry",
}
SOURCE_ID_PATTERN: Final = re.compile(r"^(0|[1-9][0-9]*)(?:-([EW]))?$")
SUFFIX_ORDER: Final = {None: 0, "E": 1, "W": 2}
ALLOWED_SOURCE_VALUES: Final = {"WDBII", "WVS"}
INVARIANCE_TOLERANCE_M: Final = 0.000001

CODE_PATHS: Final = (
    "configs/multicity/gshhg_l3_hierarchy_audit_preregistration_v1.toml",
    AMENDMENT_PATH,
    "scripts/audit_multicity_gshhg_l3_hierarchy.py",
    "src/la_heat/multicity/gshhg_l3_hierarchy_audit.py",
    "src/la_heat/multicity/gshhg_l3_hierarchy_preregistration.py",
    "src/la_heat/multicity/gshhg_geometry_pilot.py",
    "src/la_heat/multicity/plan_audit.py",
    "src/la_heat/multicity/config.py",
    "src/la_heat/provenance.py",
)

ProgressCallback = Callable[[dict[str, Any]], None]
_STRUCTURAL_BUNDLE_TOKEN: Final = object()


class GshhgL3HierarchyAuditError(ValueError):
    """Raised when the authorized L3 audit cannot finish exactly as frozen."""


class StructuralAuditError(GshhgL3HierarchyAuditError):
    """A preregistered phase-1 source-structure gate failed."""

    def __init__(
        self,
        gate: str,
        *,
        expected: object,
        observed: object,
        detail: str | None = None,
    ) -> None:
        self.gate = gate
        self.expected = expected
        self.observed = observed
        self.detail = detail
        message = f"Structural gate {gate!r} failed."
        if detail:
            message = f"{message} {detail}"
        super().__init__(message)


class NumericalAuditError(GshhgL3HierarchyAuditError):
    """A preregistered phase-2 numerical gate failed."""

    def __init__(
        self,
        gate: str,
        *,
        expected: object,
        observed: object,
        detail: str | None = None,
    ) -> None:
        self.gate = gate
        self.expected = expected
        self.observed = observed
        self.detail = detail
        message = f"Numerical gate {gate!r} failed."
        if detail:
            message = f"{message} {detail}"
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class GitGate:
    """Authenticated repository state that must remain unchanged during a run."""

    head: str
    branch: str
    origin_main: str
    tracked_blob_sha1: dict[str, str]


@dataclass(frozen=True, slots=True)
class V2Amendment:
    """Authenticated one-leaf correction applied to the immutable V1 contract."""

    path: Path
    file_sha256: str
    contract: dict[str, Any]
    effective_config: dict[str, Any]
    v1_failure: dict[str, Any]
    v1_failure_file_sha256: str


@dataclass(frozen=True, slots=True)
class StructuralAuditBundle:
    """The only value accepted by phase 2.

    Construction occurs after every phase-1 gate passes and after the isolated
    member-staging directory has been deleted.
    """

    l1_repaired: gpd.GeoDataFrame
    selected_l2: gpd.GeoDataFrame
    selected_l3: gpd.GeoDataFrame
    archive_audit: dict[str, Any]
    layer_audit: dict[str, Any]
    hierarchy_audit: dict[str, Any]
    _authorization_token: object


@dataclass(frozen=True, slots=True)
class DistanceRun:
    """One canonical point-to-line distance plus its accepted candidates."""

    record: dict[str, Any]
    candidates: gpd.GeoDataFrame


def _emit(
    callback: ProgressCallback | None,
    event: str,
    **details: object,
) -> None:
    if callback is not None:
        callback({"event": event, **details})


def _canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")


def _read_json_object(path: Path, *, label: str) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GshhgL3HierarchyAuditError(f"Cannot read {label}: {path}") from exc
    after = sha256_file(path)
    if before != after:
        raise RuntimeError(f"{label} changed while being read: {path}")
    if not isinstance(payload, dict):
        raise GshhgL3HierarchyAuditError(f"{label} must be a JSON object.")
    recorded = payload.get("commit_sha256")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or canonical_sha256(body) != recorded:
        raise GshhgL3HierarchyAuditError(f"{label} has an invalid internal commit.")
    return payload, before


def _resolve_project_path(project_root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    return candidate.resolve() if candidate.is_absolute() else (project_root / candidate).resolve()


def _recorded_path(project_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _publish_new_bytes(content: bytes, destination: Path) -> None:
    """Atomically publish new bytes without ever replacing an existing artifact."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.partial"
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError as exc:
            raise GshhgL3HierarchyAuditError(
                f"Append-only output already exists: {destination}"
            ) from exc
        except OSError:
            # Some Windows filesystems disable hard links.  O_EXCL preserves
            # no-clobber semantics even though a crash could leave an incomplete
            # destination; check-only will reject such bytes.
            try:
                with destination.open("xb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
            except FileExistsError as exc:
                raise GshhgL3HierarchyAuditError(
                    f"Append-only output already exists: {destination}"
                ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def canonical_source_id_sort_key(value: object) -> tuple[int, int]:
    """Return the preregistered numeric-plus-suffix ordering key."""

    if not isinstance(value, str):
        raise StructuralAuditError(
            "canonical_source_id",
            expected="string matching the frozen source-ID pattern",
            observed=type(value).__name__,
        )
    match = SOURCE_ID_PATTERN.fullmatch(value)
    if match is None:
        raise StructuralAuditError(
            "canonical_source_id",
            expected=SOURCE_ID_PATTERN.pattern,
            observed=value,
        )
    return int(match.group(1)), SUFFIX_ORDER[match.group(2)]


def _normalized_geometry_sha256(geometry: object) -> str:
    return hashlib.sha256(shapely.to_wkb(shapely.normalize(geometry))).hexdigest()


def _semantic_layer_sha256(frame: gpd.GeoDataFrame) -> str:
    records: list[dict[str, object]] = []
    ordered_indices = sorted(
        range(len(frame)),
        key=lambda index: canonical_source_id_sort_key(frame.iloc[index]["id"]),
    )
    for index in ordered_indices:
        row = frame.iloc[index]
        records.append(
            {
                "id": str(row["id"]),
                "level": int(row["level"]),
                "source": str(row["source"]),
                "parent_id": int(row["parent_id"]),
                "sibling_id": int(row["sibling_id"]),
                "area": float(row["area"]),
                "normalized_wkb_sha256": _normalized_geometry_sha256(row.geometry),
            }
        )
    return canonical_sha256(
        {
            "crs": CRS.from_user_input(frame.crs).to_string(),
            "columns": list(EXPECTED_COLUMNS[:-1]),
            "records": records,
        }
    )


def _selected_exterior_semantic_sha256(frame: gpd.GeoDataFrame) -> str:
    records: list[dict[str, object]] = []
    ordered_indices = sorted(
        range(len(frame)),
        key=lambda index: canonical_source_id_sort_key(frame.iloc[index]["id"]),
    )
    for index in ordered_indices:
        row = frame.iloc[index]
        exterior = LineString(row.geometry.exterior.coords)
        records.append(
            {
                "id": str(row["id"]),
                "parent_id": int(row["parent_id"]),
                "exterior_normalized_wkb_sha256": _normalized_geometry_sha256(exterior),
            }
        )
    return canonical_sha256(
        {
            "crs": CRS.from_user_input(frame.crs).to_string(),
            "exterior_only": True,
            "records": records,
        }
    )


def _git_blob_records(
    project_root: Path,
    *,
    required_paths: tuple[str, ...],
) -> GitGate:
    """Run the shared v6 Git gate and record each authenticated HEAD blob."""

    head = _git_preflight(project_root, required_paths=required_paths)

    def run(*arguments: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(project_root), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise GshhgL3HierarchyAuditError(
                f"Git evidence command failed: {' '.join(arguments)}: {completed.stderr.strip()}"
            )
        return completed.stdout.strip()

    branch = run("branch", "--show-current")
    origin_main = run("rev-parse", "origin/main")
    records: dict[str, str] = {}
    for relative in required_paths:
        tree_record = run("ls-tree", "HEAD", "--", relative)
        parts = tree_record.split(maxsplit=3)
        if len(parts) != 4 or parts[0] not in {"100644", "100755"} or parts[1] != "blob":
            raise GshhgL3HierarchyAuditError(
                f"Executor dependency is not a regular HEAD blob: {relative}"
            )
        records[relative] = parts[2]
    return GitGate(
        head=head,
        branch=branch,
        origin_main=origin_main,
        tracked_blob_sha1=records,
    )


def _strict_equal(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or set(actual) != set(expected):
            return False
        return all(_strict_equal(actual[key], expected[key]) for key in expected)
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        return all(
            _strict_equal(left, right)
            for left, right in zip(actual, expected, strict=True)
        )
    return bool(actual == expected)


def _require_exact_object(
    value: object,
    *,
    expected: Mapping[str, object],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or not _strict_equal(value, dict(expected)):
        raise GshhgL3HierarchyAuditError(f"The exact {label} changed.")
    return value


def _git_readonly(project_root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(project_root), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise GshhgL3HierarchyAuditError(
            f"Git amendment evidence command failed: {' '.join(arguments)}: "
            f"{completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def _require_git_ancestor(
    project_root: Path,
    ancestor: str,
    descendant: str,
    *,
    label: str,
) -> None:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise GshhgL3HierarchyAuditError(
            f"The required {label} Git ancestry is missing."
        )


def _authenticate_v1_failure_history(
    project_root: Path,
    failure: Mapping[str, Any],
) -> None:
    repository = failure.get("repository")
    _require_exact_mapping_fields(
        repository,
        expected={
            "branch": "main",
            "head": V1_RUN_HEAD,
            "origin_main": V1_RUN_HEAD,
            "head_equals_origin_main": True,
        },
        label="V1 failure historical repository",
    )
    if not isinstance(repository, dict):
        raise GshhgL3HierarchyAuditError(
            "The V1 failure historical repository evidence is missing."
        )
    recorded_blobs = repository.get("tracked_blob_sha1")
    if not isinstance(recorded_blobs, dict) or not recorded_blobs:
        raise GshhgL3HierarchyAuditError(
            "The V1 failure lacks historical executor blob evidence."
        )
    mismatches = {
        path: {
            "recorded": blob,
            "historical": _git_readonly(project_root, "rev-parse", f"{V1_RUN_HEAD}:{path}"),
        }
        for path, blob in recorded_blobs.items()
        if not isinstance(path, str)
        or not isinstance(blob, str)
        or _git_readonly(project_root, "rev-parse", f"{V1_RUN_HEAD}:{path}") != blob
    }
    if mismatches:
        raise GshhgL3HierarchyAuditError(
            f"The V1 failure historical executor blobs changed: {mismatches}"
        )
    failure_blob = _git_readonly(
        project_root,
        "rev-parse",
        f"{V1_FAILURE_PUBLICATION_GIT_COMMIT}:{DEFAULT_V1_FAILURE_MANIFEST.as_posix()}",
    )
    if failure_blob != "aca5a2b7231bd1d0ffb660ce0554034c3dd014ba":
        raise GshhgL3HierarchyAuditError(
            "The preserved V1 failure publication blob changed."
        )
    _require_git_ancestor(
        project_root,
        V1_RUN_HEAD,
        V1_FAILURE_PUBLICATION_GIT_COMMIT,
        label="V1 run-to-failure-publication",
    )
    _require_git_ancestor(
        project_root,
        V1_FAILURE_PUBLICATION_GIT_COMMIT,
        AMENDMENT_PUBLICATION_GIT_COMMIT,
        label="V1-failure-to-V2-amendment",
    )


def _authenticate_v2_amendment(
    project_root: Path,
    *,
    base_config_path: Path,
    base_config: Mapping[str, Any],
    preregistration: Mapping[str, Any],
    pilot: Mapping[str, Any],
) -> V2Amendment:
    """Authenticate the one-leaf V2 correction without opening source data."""

    amendment_path = (project_root / AMENDMENT_PATH).resolve()
    if sha256_file(amendment_path) != EXPECTED_AMENDMENT_FILE_SHA256:
        raise GshhgL3HierarchyAuditError("The exact V2 structural amendment changed.")
    try:
        with amendment_path.open("rb") as handle:
            contract = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise GshhgL3HierarchyAuditError(
            "Cannot parse the exact V2 structural amendment."
        ) from exc

    amendment = _require_exact_object(
        contract.get("amendment"),
        expected={
            "schema_version": 2,
            "algorithm_version": "gshhg-l3-hierarchy-audit-structural-amendment-v2",
            "amendment_id": (
                "target_blind_gshhg_l3_hierarchy_audit_structural_amendment_v2"
            ),
            "amendment_date": "2026-07-30",
            "state": (
                "gshhg_l3_hierarchy_audit_v2_structural_amendment_committed_unopened"
            ),
            "scope": (
                "correct exactly one transcribed source-structure hash after preserving "
                "the authenticated V1 failure and before any probe or distance"
            ),
            "correction_count": 1,
            "base_preregistration_config": (
                "configs/multicity/gshhg_l3_hierarchy_audit_preregistration_v1.toml"
            ),
            "base_preregistration_config_sha256": EXPECTED_CONFIG_SHA256,
            "base_preregistration_manifest": PREREGISTRATION_PATH,
            "base_preregistration_manifest_sha256": (
                EXPECTED_PREREGISTRATION_FILE_SHA256
            ),
            "base_preregistration_commit_sha256": (
                EXPECTED_PREREGISTRATION_COMMIT_SHA256
            ),
            "v1_failure_manifest": DEFAULT_V1_FAILURE_MANIFEST.as_posix(),
            "v1_failure_manifest_sha256": EXPECTED_V1_FAILURE_FILE_SHA256,
            "v1_failure_commit_sha256": EXPECTED_V1_FAILURE_COMMIT_SHA256,
            "v1_failure_git_commit": V1_FAILURE_PUBLICATION_GIT_COMMIT,
            "v1_failure_tracked_blob_sha1": (
                "aca5a2b7231bd1d0ffb660ce0554034c3dd014ba"
            ),
            "v1_run_head": V1_RUN_HEAD,
            "v1_required_state": V1_FAILURE_STATE,
            "v1_required_phase": "phase_1_structure",
            "v1_required_gate": "selected_l2_normalized_wkb_sha256",
            "all_other_structure_gates_unchanged": True,
            "all_probe_definitions_unchanged": True,
            "all_numerical_algorithms_and_thresholds_unchanged": True,
            "all_access_locks_unchanged": True,
        },
        label="V2 amendment identity and lineage",
    )
    correction = _require_exact_object(
        contract.get("correction"),
        expected={
            "field_path": (
                "unchanged_v2_contract.selected_l2_normalized_wkb_sha256.180515"
            ),
            "json_pointer": (
                "/unchanged_v2_contract/selected_l2_normalized_wkb_sha256/180515"
            ),
            "source_id": CORRECTED_SOURCE_ID,
            "preregistered_value": V1_EXPECTED_SOURCE_HASH,
            "corrected_value": V2_CORRECTED_SOURCE_HASH,
            "v1_observed_value": V2_CORRECTED_SOURCE_HASH,
            "correction_reason": (
                "the base preregistration transcribed the final hexadecimal character "
                "as c although the previously authenticated V2 pilot and the preserved "
                "V1 failure both record a"
            ),
            "acceptance_rule": (
                "replace only the exact old value with the exact corrected value; no "
                "tolerance, fallback, reselection, or additional correction is allowed"
            ),
            "pilot_manifest": PILOT_PATH,
            "pilot_manifest_sha256": (
                "71d68e35a67d82d5e8d7746cc9732d9cd1b8d880ed126e1c2af46cc72615bad1"
            ),
            "pilot_commit_sha256": (
                "e14cbd4763489fbacdec3ac45348226e2ae677073aa592aabf9bc0e3d8256735"
            ),
            "pilot_record_pointer": (
                "/source_layers/great_lakes_identity/source_polygons/"
                "source_id=180515/normalized_wkb_sha256"
            ),
        },
        label="V2 exact source-structure correction",
    )
    _require_exact_object(
        contract.get("unchanged_contract"),
        expected={
            "source_archive_or_version_may_change": False,
            "selected_l2_source_ids_may_change": False,
            "direct_parent_all_descendants_rule_may_change": False,
            "l4_or_exterior_only_rule_may_change": False,
            "existing_points_may_change": False,
            "numerical_thresholds_may_change": False,
            "access_locks_may_change": False,
            "tolerance_may_be_relaxed": False,
            "fallback_or_reselection_allowed": False,
            "v1_failure_may_be_deleted_or_rewritten": False,
        },
        label="V2 unchanged-contract locks",
    )
    _require_exact_object(
        contract.get("locks"),
        expected={
            "source_lock_created": False,
            "algorithm_lock_created": False,
            "feature_names_frozen": False,
            "predictor_build_authorized": False,
            "protocol_lock_created": False,
            "external_targets_unlocked": False,
            "external_target_values_read": False,
            "external_prediction_commit_exists": False,
        },
        label="V2 amendment locks",
    )
    _require_exact_object(
        contract.get("amendment_access_record"),
        expected={
            "amendment_program_archive_opened": False,
            "amendment_program_geometry_opened": False,
            "public_source_geometry_was_read_in_preserved_v1_run": True,
            "source_structure_values_were_read_in_preserved_v1_run": True,
            "v1_probe_derived": False,
            "v1_distance_values_computed": False,
            "network_requests": 0,
            "gshhg_l4_member_opened": False,
            "census_layer_opened": False,
            "eligible_land_grid_opened": False,
            "distance_feature_surface_computed": False,
            "tract_aggregation_performed": False,
            "predictor_values_computed": False,
            "predictor_construction_performed": False,
            "model_fit_performed": False,
            "model_predictions_computed": False,
            "landsat_thermal_values_read": False,
            "landsat_target_qa_values_read": False,
            "external_target_files_opened": False,
            "final_evaluation_outputs_opened": False,
        },
        label="V2 amendment access record",
    )
    _require_exact_object(
        contract.get("outputs"),
        expected={
            "success_manifest": DEFAULT_MANIFEST.as_posix(),
            "v2_failure_manifest": DEFAULT_FAILURE_MANIFEST.as_posix(),
            "diagnostic_table": DEFAULT_DIAGNOSTIC_TABLE.as_posix(),
        },
        label="V2 amendment outputs",
    )
    if set(contract) != {
        "amendment",
        "correction",
        "unchanged_contract",
        "locks",
        "amendment_access_record",
        "outputs",
    }:
        raise GshhgL3HierarchyAuditError("The V2 amendment top-level schema changed.")

    if sha256_file(base_config_path) != amendment["base_preregistration_config_sha256"]:
        raise GshhgL3HierarchyAuditError("The V1 config bound by the amendment changed.")
    if (
        preregistration.get("commit_sha256")
        != amendment["base_preregistration_commit_sha256"]
    ):
        raise GshhgL3HierarchyAuditError(
            "The V1 preregistration bound by the amendment changed."
        )
    for section in (
        "unchanged_v2_contract",
        "hierarchy_contract",
        "probe_rule",
        "locks",
        "access_contract",
    ):
        if not _strict_equal(base_config.get(section), preregistration.get(section)):
            raise GshhgL3HierarchyAuditError(
                f"The parsed V1 config diverges from its committed {section} evidence."
            )
    base_numerical = base_config.get("numerical_audit")
    preregistered_numerical = preregistration.get("numerical_audit")
    if not isinstance(base_numerical, dict) or not isinstance(
        preregistered_numerical,
        dict,
    ):
        raise GshhgL3HierarchyAuditError(
            "The V1 numerical contract evidence is missing."
        )
    changed_numerical = {
        key: {
            "config": value,
            "preregistration": preregistered_numerical.get(key),
        }
        for key, value in base_numerical.items()
        if key not in preregistered_numerical
        or not _strict_equal(value, preregistered_numerical[key])
    }
    if changed_numerical:
        raise GshhgL3HierarchyAuditError(
            f"The parsed V1 numerical contract diverges from its committed evidence: "
            f"{changed_numerical}"
        )
    if "diagnostic_points" in preregistered_numerical and not _strict_equal(
        base_config.get("diagnostic_points"),
        preregistered_numerical["diagnostic_points"],
    ):
        raise GshhgL3HierarchyAuditError(
            "The parsed V1 diagnostic points diverge from committed evidence."
        )
    old_hash = (
        base_config.get("unchanged_v2_contract", {})
        .get("selected_l2_normalized_wkb_sha256", {})
        .get(CORRECTED_SOURCE_ID)
    )
    if old_hash != V1_EXPECTED_SOURCE_HASH:
        raise GshhgL3HierarchyAuditError(
            "The amendment's exact old source-structure hash is absent."
        )
    hash_differences = [
        (index, old, new)
        for index, (old, new) in enumerate(
            zip(V1_EXPECTED_SOURCE_HASH, V2_CORRECTED_SOURCE_HASH, strict=True)
        )
        if old != new
    ]
    if hash_differences != [(63, "c", "a")]:
        raise GshhgL3HierarchyAuditError(
            "The V2 amendment is not the exact one-character c-to-a correction."
        )

    v1_failure_path = (project_root / DEFAULT_V1_FAILURE_MANIFEST).resolve()
    v1_failure, v1_failure_sha = _read_json_object(
        v1_failure_path,
        label="preserved GSHHG L3 V1 failure",
    )
    if (
        v1_failure_sha != EXPECTED_V1_FAILURE_FILE_SHA256
        or v1_failure.get("commit_sha256") != EXPECTED_V1_FAILURE_COMMIT_SHA256
    ):
        raise GshhgL3HierarchyAuditError("The preserved V1 failure bytes changed.")
    _require_exact_mapping_fields(
        v1_failure,
        expected={
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": f"{V1_ALGORITHM_VERSION}-failure-record",
            "state": V1_FAILURE_STATE,
            "phase": "phase_1_structure",
            "gate": "selected_l2_normalized_wkb_sha256",
            "expected": {
                "source_id": CORRECTED_SOURCE_ID,
                "sha256": V1_EXPECTED_SOURCE_HASH,
            },
            "observed": {"sha256": V2_CORRECTED_SOURCE_HASH},
        },
        label="preserved V1 failure identity",
    )
    _authenticate_terminal_locks_and_access(v1_failure, label="preserved V1 failure")
    _require_exact_mapping_fields(
        v1_failure.get("access_contract"),
        expected={
            "network_requests": 0,
            "gshhg_archive_opened": True,
            "authorized_l1_l2_l3_members_may_have_been_opened": True,
            "authorized_member_allowlist": list(AUTHORIZED_MEMBERS),
            "probe_derived": False,
            "distance_values_computed": False,
        },
        label="preserved V1 failure access evidence",
    )
    _authenticate_v1_failure_history(project_root, v1_failure)

    pilot_records = (
        pilot.get("source_layers", {})
        .get("great_lakes_identity", {})
        .get("source_polygons")
    )
    if not isinstance(pilot_records, list):
        raise GshhgL3HierarchyAuditError(
            "The authenticated pilot lacks Great Lakes source identities."
        )
    matching_pilot_records = [
        record
        for record in pilot_records
        if isinstance(record, dict) and record.get("source_id") == CORRECTED_SOURCE_ID
    ]
    if (
        len(matching_pilot_records) != 1
        or matching_pilot_records[0].get("normalized_wkb_sha256")
        != V2_CORRECTED_SOURCE_HASH
        or sha256_file(project_root / PILOT_PATH) != correction["pilot_manifest_sha256"]
        or pilot.get("commit_sha256") != correction["pilot_commit_sha256"]
    ):
        raise GshhgL3HierarchyAuditError(
            "The exact correction does not reproduce the pre-existing pilot evidence."
        )

    pilot_v2_path = _resolve_project_path(
        project_root,
        str(base_config["unchanged_v2_contract"]["amendment_config_path"]),
    )
    try:
        with pilot_v2_path.open("rb") as handle:
            pilot_v2 = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise GshhgL3HierarchyAuditError(
            "Cannot authenticate the pre-existing V2 pilot contract."
        ) from exc
    pilot_v2_records = pilot_v2.get("great_lakes_connected_water_contract", {}).get(
        "source_polygons"
    )
    matching_pilot_v2 = [
        record
        for record in pilot_v2_records
        if isinstance(record, dict) and record.get("source_id") == CORRECTED_SOURCE_ID
    ] if isinstance(pilot_v2_records, list) else []
    if (
        sha256_file(pilot_v2_path)
        != str(base_config["unchanged_v2_contract"]["amendment_config_sha256"])
        or len(matching_pilot_v2) != 1
        or matching_pilot_v2[0].get("expected_normalized_wkb_sha256")
        != V2_CORRECTED_SOURCE_HASH
    ):
        raise GshhgL3HierarchyAuditError(
            "The exact correction does not reproduce the frozen V2 pilot config."
        )

    amendment_blob = _git_readonly(
        project_root,
        "rev-parse",
        f"{AMENDMENT_PUBLICATION_GIT_COMMIT}:{AMENDMENT_PATH}",
    )
    if amendment_blob != EXPECTED_AMENDMENT_BLOB_SHA1:
        raise GshhgL3HierarchyAuditError(
            "The separately committed V2 amendment blob changed."
        )
    _require_git_ancestor(
        project_root,
        V1_FAILURE_PUBLICATION_GIT_COMMIT,
        AMENDMENT_PUBLICATION_GIT_COMMIT,
        label="V1-failure-to-amendment",
    )

    effective_config = copy.deepcopy(dict(base_config))
    effective_hashes = effective_config["unchanged_v2_contract"][
        "selected_l2_normalized_wkb_sha256"
    ]
    effective_hashes[CORRECTED_SOURCE_ID] = V2_CORRECTED_SOURCE_HASH
    changed_paths: list[tuple[str, ...]] = []

    def collect_changes(
        left: object,
        right: object,
        path: tuple[str, ...] = (),
    ) -> None:
        if isinstance(left, dict) and isinstance(right, dict) and set(left) == set(right):
            for key in sorted(left):
                collect_changes(left[key], right[key], (*path, str(key)))
            return
        if not _strict_equal(left, right):
            changed_paths.append(path)

    collect_changes(dict(base_config), effective_config)
    if changed_paths != [
        (
            "unchanged_v2_contract",
            "selected_l2_normalized_wkb_sha256",
            CORRECTED_SOURCE_ID,
        )
    ]:
        raise GshhgL3HierarchyAuditError(
            f"The V2 effective contract changed more than one leaf: {changed_paths}"
        )
    return V2Amendment(
        path=amendment_path,
        file_sha256=EXPECTED_AMENDMENT_FILE_SHA256,
        contract=contract,
        effective_config=effective_config,
        v1_failure=v1_failure,
        v1_failure_file_sha256=v1_failure_sha,
    )


def _required_git_paths(project_root: Path) -> tuple[str, ...]:
    """Return every tracked byte surface needed by the executor."""

    from la_heat.multicity.config import load_multicity_plan

    plan = load_multicity_plan(project_root / "configs/multicity/experiment.toml")
    return tuple(
        dict.fromkeys(
            (
                *(path.relative_to(project_root).as_posix() for path in plan.source_files),
                *CODE_PATHS,
                PLAN_PATH,
                PREREGISTRATION_PATH,
                PILOT_PATH,
                FREEZE_DECISION_PATH,
                DEFAULT_V1_FAILURE_MANIFEST.as_posix(),
            )
        )
    )


def _authenticate_pre_archive_inputs(
    config_path: str | Path,
    *,
    callback: ProgressCallback | None,
) -> tuple[
    Path,
    Path,
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    V2Amendment,
    GitGate,
    tuple[str, ...],
]:
    """Authenticate only tracked bytes; this function has no data reader."""

    project_root, resolved_config, base_config = _read_config(config_path)
    if sha256_file(resolved_config) != EXPECTED_CONFIG_SHA256:
        raise GshhgL3HierarchyAuditError("The exact L3 preregistration config changed.")
    _emit(callback, "preflight.plan_v6.start")
    plan = audit_multicity_plan(
        project_root / "configs/multicity/experiment.toml",
        output_path=project_root / PLAN_PATH,
        write=False,
    )
    if (
        plan.get("schema_version") != 6
        or plan.get("commit_sha256") != EXPECTED_PLAN_COMMIT_SHA256
        or sha256_file(project_root / PLAN_PATH) != EXPECTED_PLAN_FILE_SHA256
        or plan.get("next_safe_stage") != "target_blind_gshhg_l3_hierarchy_geometry_audit"
        or plan.get("authorized_now", {}).get("target_blind_gshhg_l3_hierarchy_geometry_read")
        is not True
    ):
        raise GshhgL3HierarchyAuditError(
            "Planning v6 did not reproduce the exact narrow L3 authorization."
        )
    _emit(callback, "preflight.plan_v6.complete")

    preregistration, preregistration_sha = _read_json_object(
        project_root / PREREGISTRATION_PATH,
        label="L3 preregistration",
    )
    if (
        preregistration_sha != EXPECTED_PREREGISTRATION_FILE_SHA256
        or preregistration.get("commit_sha256") != EXPECTED_PREREGISTRATION_COMMIT_SHA256
    ):
        raise GshhgL3HierarchyAuditError("The canonical L3 preregistration changed.")
    pilot, _ = _read_json_object(
        project_root / PILOT_PATH,
        label="GSHHG V2 pilot",
    )
    _, amendment, _, base = _read_exact_configs(
        project_root
        / str(base_config["unchanged_v2_contract"]["amendment_config_path"])
    )
    v2_amendment = _authenticate_v2_amendment(
        project_root,
        base_config_path=resolved_config,
        base_config=base_config,
        preregistration=preregistration,
        pilot=pilot,
    )

    required_paths = _required_git_paths(project_root)
    _emit(callback, "preflight.git_gate.start")
    git_gate = _git_blob_records(project_root, required_paths=required_paths)
    _emit(
        callback,
        "preflight.git_gate.complete",
        head=git_gate.head,
        tracked_blob_count=len(git_gate.tracked_blob_sha1),
    )
    _require_git_ancestor(
        project_root,
        AMENDMENT_PUBLICATION_GIT_COMMIT,
        git_gate.head,
        label="V2-amendment-to-run",
    )
    if (
        git_gate.tracked_blob_sha1.get(AMENDMENT_PATH)
        != EXPECTED_AMENDMENT_BLOB_SHA1
        or git_gate.tracked_blob_sha1.get(DEFAULT_V1_FAILURE_MANIFEST.as_posix())
        != "aca5a2b7231bd1d0ffb660ce0554034c3dd014ba"
    ):
        raise GshhgL3HierarchyAuditError(
            "The current clean main branch does not retain the exact amendment lineage."
        )
    return (
        project_root,
        resolved_config,
        v2_amendment.effective_config,
        preregistration,
        pilot,
        {"amendment": amendment, "base": base},
        v2_amendment,
        git_gate,
        required_paths,
    )


def _same_git_gate(
    project_root: Path,
    *,
    required_paths: tuple[str, ...],
    expected: GitGate,
) -> GitGate:
    observed = _git_blob_records(project_root, required_paths=required_paths)
    if observed != expected:
        raise GshhgL3HierarchyAuditError(
            "Repository state changed after the authenticated executor preflight."
        )
    return observed


def _validate_member_name(name: str, *, is_directory: bool) -> str:
    if not name or "\x00" in name or "\\" in name:
        raise StructuralAuditError(
            "zip_member_path_safety",
            expected="non-empty canonical POSIX member name",
            observed=name,
        )
    if name.startswith(("/", "//")) or re.match(r"^[A-Za-z]:", name) or ":" in name:
        raise StructuralAuditError(
            "zip_member_path_safety",
            expected="relative canonical POSIX member name",
            observed=name,
        )
    body = name[:-1] if is_directory and name.endswith("/") else name
    if is_directory and not name.endswith("/"):
        raise StructuralAuditError(
            "zip_member_path_safety",
            expected="directory with trailing slash",
            observed=name,
        )
    if not is_directory and name.endswith("/"):
        raise StructuralAuditError(
            "zip_member_path_safety",
            expected="file without trailing slash",
            observed=name,
        )
    parts = body.split("/")
    parsed = PurePosixPath(body)
    if (
        not body
        or any(part in {"", ".", ".."} for part in parts)
        or parsed.is_absolute()
        or parsed.as_posix() != body
    ):
        raise StructuralAuditError(
            "zip_member_path_safety",
            expected="canonical relative member name",
            observed=name,
        )
    return body


def _hash_archive(
    archive_path: Path,
    *,
    callback: ProgressCallback | None,
) -> tuple[str, str, int]:
    sha256 = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)  # noqa: S324
    observed_bytes = archive_path.stat().st_size
    completed_bytes = 0
    next_report = 16 * 1024 * 1024
    with archive_path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            sha256.update(chunk)
            md5.update(chunk)
            completed_bytes += len(chunk)
            if completed_bytes >= next_report:
                _emit(
                    callback,
                    "phase1.archive_hash.progress",
                    completed_bytes=completed_bytes,
                    total_bytes=observed_bytes,
                )
                next_report += 16 * 1024 * 1024
    return sha256.hexdigest(), md5.hexdigest(), observed_bytes


def _central_directory_audit(
    archive: zipfile.ZipFile,
    *,
    expected_member_count: int,
    expected_uncompressed_bytes: int,
    expected_inventory_sha256: str,
) -> tuple[dict[str, zipfile.ZipInfo], dict[str, Any]]:
    """Audit central-directory metadata without opening a member."""

    members = archive.infolist()
    if len(members) != expected_member_count:
        raise StructuralAuditError(
            "archive_member_count",
            expected=expected_member_count,
            observed=len(members),
        )
    seen: set[str] = set()
    collision_keys: set[str] = set()
    file_members: dict[str, zipfile.ZipInfo] = {}
    inventory: list[dict[str, object]] = []
    total_uncompressed = 0
    compression_methods: set[int] = set()
    for member in members:
        canonical = _validate_member_name(
            member.filename,
            is_directory=member.is_dir(),
        )
        if member.filename in seen:
            raise StructuralAuditError(
                "archive_member_names_unique",
                expected="unique member names",
                observed=member.filename,
            )
        seen.add(member.filename)
        collision = unicodedata.normalize("NFKC", canonical).casefold()
        if collision in collision_keys:
            raise StructuralAuditError(
                "archive_member_casefold_names_unique",
                expected="no Unicode/case-folding collision",
                observed=canonical,
            )
        collision_keys.add(collision)
        unix_mode = member.external_attr >> 16
        file_type = stat.S_IFMT(unix_mode)
        if stat.S_ISLNK(unix_mode) or file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
            raise StructuralAuditError(
                "archive_member_file_type",
                expected="regular file or directory",
                observed={"name": member.filename, "mode": unix_mode},
            )
        if member.flag_bits & 0x1:
            raise StructuralAuditError(
                "archive_member_encryption",
                expected=False,
                observed=member.filename,
            )
        if member.file_size < 0 or member.compress_size < 0:
            raise StructuralAuditError(
                "archive_member_size",
                expected="non-negative sizes",
                observed=member.filename,
            )
        if member.file_size > 1_000_000_000:
            raise StructuralAuditError(
                "archive_member_size_limit",
                expected="<= 1,000,000,000 uncompressed bytes",
                observed={"name": member.filename, "bytes": member.file_size},
            )
        if member.file_size / max(member.compress_size, 1) > 250.0:
            raise StructuralAuditError(
                "archive_member_compression_ratio",
                expected="<= 250",
                observed=member.filename,
            )
        total_uncompressed += member.file_size
        compression_methods.add(member.compress_type)
        if not member.is_dir():
            file_members[canonical] = member
        inventory.append(
            {
                "name": member.filename,
                "bytes": member.file_size,
                "compressed_bytes": member.compress_size,
                "crc32": f"{member.CRC:08x}",
                "method": member.compress_type,
                "flags": member.flag_bits,
                "external_attr": member.external_attr,
            }
        )
    encoded = json.dumps(
        inventory,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    inventory_sha = hashlib.sha256(encoded).hexdigest()
    if total_uncompressed != expected_uncompressed_bytes:
        raise StructuralAuditError(
            "archive_total_uncompressed_bytes",
            expected=expected_uncompressed_bytes,
            observed=total_uncompressed,
        )
    if inventory_sha != expected_inventory_sha256:
        raise StructuralAuditError(
            "archive_member_inventory_sha256",
            expected=expected_inventory_sha256,
            observed=inventory_sha,
        )
    missing = sorted(set(AUTHORIZED_MEMBERS) - set(file_members))
    if missing:
        raise StructuralAuditError(
            "authorized_member_presence_and_case",
            expected=list(AUTHORIZED_MEMBERS),
            observed={"missing": missing},
        )
    return file_members, {
        "member_count": len(members),
        "file_member_count": len(file_members),
        "total_uncompressed_bytes": total_uncompressed,
        "member_inventory_sha256": inventory_sha,
        "compression_methods": sorted(compression_methods),
        "central_directory_only_before_authorized_member_streams": True,
    }


def _stream_authorized_members(
    archive: zipfile.ZipFile,
    file_members: Mapping[str, zipfile.ZipInfo],
    *,
    staging_root: Path,
    inherited_hashes: Mapping[str, str],
    callback: ProgressCallback | None,
    access_evidence: dict[str, Any] | None = None,
) -> tuple[dict[int, Path], dict[str, dict[str, object]], list[str]]:
    """Stream exactly twelve members to three isolated layer directories."""

    if set(AUTHORIZED_MEMBERS) - set(file_members):
        raise StructuralAuditError(
            "authorized_member_presence_and_case",
            expected=list(AUTHORIZED_MEMBERS),
            observed=sorted(file_members),
        )
    layer_paths: dict[int, Path] = {}
    records: dict[str, dict[str, object]] = {}
    open_log: list[str] = []
    for level in (1, 2, 3):
        layer_directory = staging_root / f"L{level}"
        layer_directory.mkdir(parents=True, exist_ok=False)
        for member_name in LAYER_MEMBER_QUARTETS[level]:
            if member_name not in AUTHORIZED_MEMBERS:
                raise AssertionError("An unauthorized ZIP member reached the stream loop.")
            info = file_members[member_name]
            destination = layer_directory / Path(member_name).name
            digest = hashlib.sha256()
            crc32 = 0
            observed_bytes = 0
            try:
                with archive.open(info, "r") as source, destination.open("xb") as target:
                    open_log.append(member_name)
                    if access_evidence is not None:
                        access_evidence["member_open_log"] = list(open_log)
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        target.write(chunk)
                        digest.update(chunk)
                        crc32 = zlib.crc32(chunk, crc32)
                        observed_bytes += len(chunk)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise StructuralAuditError(
                    "authorized_member_stream_and_crc",
                    expected="member streams fully with matching CRC",
                    observed=member_name,
                    detail=str(exc),
                ) from exc
            sha = digest.hexdigest()
            observed_crc = f"{crc32 & 0xFFFFFFFF:08x}"
            expected_crc = f"{info.CRC:08x}"
            if observed_bytes != info.file_size or observed_crc != expected_crc:
                raise StructuralAuditError(
                    "authorized_member_stream_and_crc",
                    expected={"bytes": info.file_size, "crc32": expected_crc},
                    observed={"bytes": observed_bytes, "crc32": observed_crc},
                )
            inherited = inherited_hashes.get(member_name)
            if level in (1, 2) and sha != inherited:
                raise StructuralAuditError(
                    "inherited_l1_l2_member_sha256",
                    expected=inherited,
                    observed={"member": member_name, "sha256": sha},
                )
            records[member_name] = {
                "bytes": observed_bytes,
                "compressed_bytes": info.compress_size,
                "sha256": sha,
                "crc32": observed_crc,
                "compression_method": info.compress_type,
                "opened_once_by_allowlisted_python_stream": True,
            }
            if access_evidence is not None:
                access_evidence.setdefault("authorized_member_records", {})[member_name] = records[
                    member_name
                ]
            _emit(
                callback,
                "phase1.member.complete",
                member=member_name,
                member_index=len(open_log),
                member_total=len(AUTHORIZED_MEMBERS),
            )
        layer_paths[level] = layer_directory / f"GSHHS_f_L{level}.shp"
    if open_log != list(AUTHORIZED_MEMBERS):
        raise StructuralAuditError(
            "authorized_member_open_log",
            expected=list(AUTHORIZED_MEMBERS),
            observed=open_log,
        )
    return layer_paths, records, open_log


def _read_isolated_layers(
    archive_path: Path,
    *,
    source_config: Mapping[str, Any],
    pilot: Mapping[str, Any],
    callback: ProgressCallback | None,
    access_evidence: dict[str, Any] | None = None,
) -> tuple[
    dict[int, gpd.GeoDataFrame],
    dict[str, Any],
]:
    """Authenticate the archive and parse only isolated authorized quartets."""

    if not archive_path.is_file():
        raise FileNotFoundError(archive_path)
    _emit(callback, "phase1.archive_hash.start", total_bytes=archive_path.stat().st_size)
    sha256, md5, observed_bytes = _hash_archive(archive_path, callback=callback)
    if access_evidence is not None:
        access_evidence.update(
            {
                "archive_opened": True,
                "archive_bytes": observed_bytes,
                "archive_sha256": sha256,
                "archive_md5": md5,
                "member_open_log": [],
            }
        )
    if observed_bytes != int(source_config["expected_archive_bytes"]):
        raise StructuralAuditError(
            "archive_bytes",
            expected=int(source_config["expected_archive_bytes"]),
            observed=observed_bytes,
        )
    if sha256 != str(source_config["expected_archive_sha256"]):
        raise StructuralAuditError(
            "archive_sha256",
            expected=str(source_config["expected_archive_sha256"]),
            observed=sha256,
        )
    if md5 != str(source_config["published_archive_md5"]):
        raise StructuralAuditError(
            "archive_published_md5",
            expected=str(source_config["published_archive_md5"]),
            observed=md5,
        )
    _emit(callback, "phase1.archive_hash.complete", sha256=sha256)

    inherited_hashes = {
        str(name): str(value)
        for name, value in pilot["source_archive"]["required_member_sha256"].items()
        if str(name) in AUTHORIZED_MEMBERS
    }
    frames: dict[int, gpd.GeoDataFrame] = {}
    archive_record: dict[str, Any]
    with tempfile.TemporaryDirectory(prefix="gshhg-l3-audit-") as temporary:
        temporary_root = Path(temporary)
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                file_members, central = _central_directory_audit(
                    archive,
                    expected_member_count=int(
                        source_config["expected_archive_member_count_from_v2"]
                    ),
                    expected_uncompressed_bytes=int(
                        source_config["expected_archive_uncompressed_bytes_from_v2"]
                    ),
                    expected_inventory_sha256=str(
                        source_config["expected_member_inventory_sha256_from_v2"]
                    ),
                )
                if access_evidence is not None:
                    access_evidence["central_directory_audit"] = central
                layer_paths, member_records, open_log = _stream_authorized_members(
                    archive,
                    file_members,
                    staging_root=temporary_root,
                    inherited_hashes=inherited_hashes,
                    callback=callback,
                    access_evidence=access_evidence,
                )
                if access_evidence is not None:
                    access_evidence["authorized_member_records"] = member_records
            for level in (1, 2, 3):
                _emit(callback, "phase1.layer_read.start", level=level)
                try:
                    frames[level] = gpd.read_file(
                        layer_paths[level],
                        engine="pyogrio",
                    )
                except Exception as exc:
                    raise StructuralAuditError(
                        f"l{level}_isolated_shapefile_parse",
                        expected="readable authenticated shapefile quartet",
                        observed=type(exc).__name__,
                        detail=str(exc),
                    ) from exc
                _emit(
                    callback,
                    "phase1.layer_read.complete",
                    level=level,
                    rows=len(frames[level]),
                )
                if access_evidence is not None:
                    access_evidence.setdefault("parsed_layer_rows", {})[str(level)] = len(
                        frames[level]
                    )
        finally:
            # TemporaryDirectory performs the deletion.  Explicitly clearing
            # paths avoids accidentally retaining any usable staging handle.
            layer_paths = {}
    archive_record = {
        "source_id": source_config["source_id"],
        "dataset": source_config["dataset"],
        "version": source_config["version"],
        "path": str(source_config["archive_path"]),
        "bytes": observed_bytes,
        "sha256": sha256,
        "published_md5": str(source_config["published_archive_md5"]),
        "observed_md5": md5,
        **central,
        "authorized_member_count": len(member_records),
        "authorized_members": member_records,
        "member_open_log": open_log,
        "all_authorized_member_crc_passed": True,
        "zipfile_testzip_called": False,
        "unauthorized_member_open_count": 0,
        "archive_extracted_to_project": False,
        "isolated_os_temporary_staging_deleted": True,
        "geometry_exported_or_redistributed": False,
    }
    return frames, archive_record


def _require_exact_l3_schema(frame: gpd.GeoDataFrame) -> None:
    if tuple(frame.columns) != EXPECTED_COLUMNS:
        raise StructuralAuditError(
            "l3_exact_columns",
            expected=list(EXPECTED_COLUMNS),
            observed=list(frame.columns),
        )
    observed_dtypes = {column: str(dtype) for column, dtype in frame.dtypes.items()}
    if observed_dtypes != EXPECTED_DTYPES:
        raise StructuralAuditError(
            "l3_exact_dtypes",
            expected=EXPECTED_DTYPES,
            observed=observed_dtypes,
        )
    if frame.crs is None or not CRS.from_user_input(frame.crs).equals(WGS84):
        raise StructuralAuditError(
            "l3_crs",
            expected="EPSG:4326",
            observed=None if frame.crs is None else CRS.from_user_input(frame.crs).to_string(),
        )


def _audit_selected_topology(
    selected: gpd.GeoDataFrame,
    selected_l2: gpd.GeoDataFrame,
) -> dict[str, Any]:
    parent_geometries = {int(row["id"]): row.geometry for _, row in selected_l2.iterrows()}
    containment_records: list[dict[str, object]] = []
    for _, row in selected.iterrows():
        child_id = str(row["id"])
        parent_id = int(row["parent_id"])
        parent = parent_geometries.get(parent_id)
        if parent is None:
            raise StructuralAuditError(
                "selected_child_declared_parent_present",
                expected=sorted(parent_geometries),
                observed={"child_source_id": child_id, "parent_id": parent_id},
            )
        strictly_within = bool(row.geometry.within(parent))
        boundary_disjoint = bool(row.geometry.boundary.disjoint(parent.exterior))
        if not strictly_within:
            raise StructuralAuditError(
                "selected_child_strictly_within_parent",
                expected=True,
                observed={"child_source_id": child_id, "parent_id": parent_id},
            )
        if not boundary_disjoint:
            raise StructuralAuditError(
                "selected_child_boundary_disjoint_from_parent_exterior",
                expected=True,
                observed={"child_source_id": child_id, "parent_id": parent_id},
            )
        containment_records.append(
            {
                "child_source_id": child_id,
                "parent_id": parent_id,
                "strictly_within": strictly_within,
                "boundary_disjoint_from_parent_exterior": boundary_disjoint,
            }
        )

    total_pair_count = 0
    candidate_pair_count = 0
    overlap_pair_count = 0
    for parent_id, siblings in selected.groupby("parent_id", sort=True):
        siblings = siblings.reset_index(drop=True)
        if len(siblings) < 2:
            continue
        total_pair_count += len(siblings) * (len(siblings) - 1) // 2
        spatial_index = siblings.sindex
        for left_index, left in siblings.iterrows():
            candidates = spatial_index.query(left.geometry, predicate="intersects")
            for right_index_value in candidates:
                right_index = int(right_index_value)
                if right_index <= left_index:
                    continue
                candidate_pair_count += 1
                right = siblings.iloc[right_index]
                intersection = left.geometry.intersection(right.geometry)
                if not intersection.is_empty and float(intersection.area) > 0.0:
                    overlap_pair_count += 1
                    raise StructuralAuditError(
                        "selected_sibling_interiors_not_overlapping",
                        expected="zero positive-area sibling intersections",
                        observed={
                            "parent_id": int(parent_id),
                            "left_source_id": str(left["id"]),
                            "right_source_id": str(right["id"]),
                            "intersection_area_square_degrees": float(intersection.area),
                        },
                    )

    maximum_jump = 0.0
    segment_count = 0
    for _, row in selected.iterrows():
        coordinates = np.asarray(row.geometry.exterior.coords, dtype=np.float64)
        jumps = np.abs(np.diff(coordinates[:, 0]))
        if jumps.size:
            row_maximum = float(np.max(jumps))
            maximum_jump = max(maximum_jump, row_maximum)
            segment_count += int(jumps.size)
            if np.any(jumps >= 180.0):
                raise StructuralAuditError(
                    "selected_exterior_longitude_jumps",
                    expected="< 180 degrees",
                    observed={
                        "child_source_id": str(row["id"]),
                        "maximum_jump_degrees": row_maximum,
                    },
                )

    return {
        "containment_records": containment_records,
        "selected_child_count": len(selected),
        "sibling_total_pair_count": total_pair_count,
        "sibling_spatial_candidate_pair_count": candidate_pair_count,
        "sibling_disjoint_pair_count_pruned_by_spatial_index": (
            total_pair_count - candidate_pair_count
        ),
        "positive_area_sibling_overlap_pair_count": overlap_pair_count,
        "selected_exterior_segment_count": segment_count,
        "maximum_selected_exterior_longitude_jump_degrees": maximum_jump,
        "longitude_jump_threshold_degrees": 180.0,
        "all_children_strictly_within_declared_parent": True,
        "all_child_boundaries_disjoint_from_parent_exterior": True,
        "all_selected_sibling_interiors_nonoverlapping": True,
        "all_selected_exterior_longitude_jumps_passed": True,
    }


def audit_l3_structure(
    frame: gpd.GeoDataFrame,
    selected_l2: gpd.GeoDataFrame,
    *,
    selected_parent_ids: Sequence[int] = EXPECTED_SELECTED_L2_IDS,
    evidence: dict[str, Any] | None = None,
) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    """Audit every frozen L3 structural rule without deriving a probe."""

    if evidence is not None:
        evidence.update(
            {
                "row_count": len(frame),
                "columns": list(frame.columns),
                "dtypes": {column: str(dtype) for column, dtype in frame.dtypes.items()},
                "crs": (None if frame.crs is None else CRS.from_user_input(frame.crs).to_string()),
                "bounds": ([] if frame.empty else [float(value) for value in frame.total_bounds]),
                "initial_observed_surface_recorded_before_gate_checks": True,
            }
        )
    _require_exact_l3_schema(frame)
    if frame.empty:
        raise StructuralAuditError(
            "l3_nonempty_layer",
            expected="at least one row",
            observed=0,
        )
    if not frame["level"].eq(3).all():
        observed = {
            str(value): int(count)
            for value, count in frame["level"].value_counts(dropna=False).items()
        }
        raise StructuralAuditError(
            "all_l3_rows_level_3",
            expected={3: len(frame)},
            observed=observed,
        )
    identifiers = frame["id"].tolist()
    for identifier in identifiers:
        canonical_source_id_sort_key(identifier)
    if frame["id"].duplicated().any():
        duplicates = sorted(
            frame.loc[frame["id"].duplicated(keep=False), "id"].astype(str).unique(),
            key=canonical_source_id_sort_key,
        )
        raise StructuralAuditError(
            "l3_source_ids_unique",
            expected="all unique",
            observed=duplicates,
        )
    observed_sources = set(frame["source"].astype(str))
    if not observed_sources.issubset(ALLOWED_SOURCE_VALUES):
        raise StructuralAuditError(
            "l3_source_values",
            expected=sorted(ALLOWED_SOURCE_VALUES),
            observed=sorted(observed_sources),
        )
    areas = frame["area"].to_numpy(dtype=np.float64)
    if not np.isfinite(areas).all() or np.any(areas <= 0.0):
        raise StructuralAuditError(
            "l3_reported_area_finite_positive",
            expected="all finite and > 0",
            observed={
                "nonfinite_count": int((~np.isfinite(areas)).sum()),
                "nonpositive_count": int((areas <= 0.0).sum()),
            },
        )
    if set(frame.geom_type) != {"Polygon"}:
        raise StructuralAuditError(
            "l3_geometry_type",
            expected={"Polygon": len(frame)},
            observed={
                str(value): int(count)
                for value, count in frame.geom_type.value_counts(dropna=False).items()
            },
        )
    if frame.geometry.isna().any() or frame.geometry.is_empty.any():
        raise StructuralAuditError(
            "l3_geometry_nonempty",
            expected=True,
            observed={
                "missing": int(frame.geometry.isna().sum()),
                "empty": int(frame.geometry.is_empty.sum()),
            },
        )
    invalid = ~frame.geometry.is_valid
    if invalid.any():
        raise StructuralAuditError(
            "l3_geometry_valid_without_repair",
            expected=0,
            observed=int(invalid.sum()),
        )
    coordinates = shapely.get_coordinates(frame.geometry.to_numpy())
    if (
        coordinates.size == 0
        or not np.isfinite(coordinates).all()
        or np.max(np.abs(coordinates[:, 0])) > 180.0
        or np.max(np.abs(coordinates[:, 1])) > 90.0
    ):
        raise StructuralAuditError(
            "l3_coordinate_domain",
            expected="finite longitude/latitude coordinates within world bounds",
            observed="empty, nonfinite, or out of bounds",
        )

    parent_ids = tuple(int(value) for value in selected_parent_ids)
    if parent_ids != tuple(EXPECTED_SELECTED_L2_IDS):
        raise StructuralAuditError(
            "selected_l2_parent_ids",
            expected=EXPECTED_SELECTED_L2_IDS,
            observed=list(parent_ids),
        )
    selected = frame.loc[frame["parent_id"].isin(parent_ids)].copy()
    selected = selected.iloc[
        sorted(
            range(len(selected)),
            key=lambda index: canonical_source_id_sort_key(selected.iloc[index]["id"]),
        )
    ].reset_index(drop=True)
    if selected.empty:
        raise StructuralAuditError(
            "selected_direct_l3_descendant_count",
            expected=">= 1",
            observed=0,
        )
    counts_by_parent = {
        str(parent_id): int(selected["parent_id"].eq(parent_id).sum()) for parent_id in parent_ids
    }
    interior_ring_count = int(sum(len(row.geometry.interiors) for _, row in selected.iterrows()))
    full_hash = _semantic_layer_sha256(frame)
    selected_hash = _semantic_layer_sha256(selected)
    exterior_hash = _selected_exterior_semantic_sha256(selected)
    pre_topology_audit: dict[str, Any] = {
        "row_count": len(frame),
        "columns": list(frame.columns),
        "dtypes": {column: str(dtype) for column, dtype in frame.dtypes.items()},
        "crs": CRS.from_user_input(frame.crs).to_string(),
        "bounds": [float(value) for value in frame.total_bounds],
        "geometry_types": {
            str(value): int(count) for value, count in frame.geom_type.value_counts().items()
        },
        "source_counts": {
            str(value): int(count) for value, count in frame["source"].value_counts().items()
        },
        "invalid_geometry_count": 0,
        "nonpositive_area_count": 0,
        "full_layer_attribute_geometry_semantic_sha256": full_hash,
        "canonical_source_id_order": ("ascending base numeric id, then suffix order none, E, W"),
        "selected_direct_descendants": {
            "selection_rule": (
                "every row whose integer parent_id is one of 180507, 180515, 180517"
            ),
            "selected_parent_ids": list(parent_ids),
            "row_count": len(selected),
            "counts_by_parent": counts_by_parent,
            "source_ids": selected["id"].astype(str).tolist(),
            "attribute_geometry_semantic_sha256": selected_hash,
            "exterior_linework_semantic_sha256": exterior_hash,
            "interior_ring_count_recorded_and_excluded": interior_ring_count,
            "geometry_repair_count": 0,
            "geometry_decomposition_count": 0,
            "selection_used_city_bbox_name_area_distance_support_target_model_or_result": False,
            "sibling_id_used_for_selection": False,
            "l4_member_opened": False,
        },
    }
    if evidence is not None:
        evidence.update(pre_topology_audit)
        evidence["topology_started"] = True
    topology = _audit_selected_topology(selected, selected_l2)
    audit = {
        **pre_topology_audit,
        "topology": topology,
        "all_structural_gates_passed": True,
    }
    if evidence is not None:
        evidence.update(audit)
    return selected, audit


def _authenticate_l1_l2_replay(
    l1: gpd.GeoDataFrame,
    l2: gpd.GeoDataFrame,
    *,
    pilot: Mapping[str, Any],
    amendment_and_base: Mapping[str, Any],
    config: Mapping[str, Any],
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, dict[str, Any]]:
    """Reproduce the exact V2 L1/L2 identities before admitting L3."""

    pilot_layers = pilot["source_layers"]
    expected_l1 = pilot_layers["l1_original"]
    expected_l2 = pilot_layers["l2_original"]
    l1_audit = _audit_source_layer(
        l1,
        label="GSHHG full-resolution L1",
        expected_level=1,
        expected_rows=int(expected_l1["row_count"]),
        allow_invalid_count=int(expected_l1["invalid_geometry_count"]),
    )
    l2_audit = _audit_source_layer(
        l2,
        label="GSHHG full-resolution L2",
        expected_level=2,
        expected_rows=int(expected_l2["row_count"]),
        allow_invalid_count=int(expected_l2["invalid_geometry_count"]),
    )
    if l1_audit != expected_l1:
        raise StructuralAuditError(
            "l1_v2_identity_replay",
            expected=expected_l1,
            observed=l1_audit,
        )
    if l2_audit != expected_l2:
        raise StructuralAuditError(
            "l2_v2_identity_replay",
            expected=expected_l2,
            observed=l2_audit,
        )

    amendment = amendment_and_base["amendment"]
    base = amendment_and_base["base"]
    repaired_l1, repair_audit = repair_predeclared_l1_geometry(
        l1,
        amendment["invalid_geometry_repair"],
    )
    if repair_audit != pilot_layers["l1_repair"]:
        raise StructuralAuditError(
            "l1_v2_predeclared_repair_replay",
            expected=pilot_layers["l1_repair"],
            observed=repair_audit,
        )
    dateline_audit = audit_l1_dateline_segments(
        repaired_l1,
        tolerance=float(base["geometry_contract"]["dateline_tolerance_degrees"]),
        reject_jump_degrees=float(
            base["geometry_contract"]["reject_remaining_segment_longitude_jump_degrees"]
        ),
    )
    expected_dateline = pilot_layers["linework"]["global_antimeridian_source_audit"]
    if dateline_audit != expected_dateline:
        raise StructuralAuditError(
            "l1_v2_antimeridian_replay",
            expected=expected_dateline,
            observed=dateline_audit,
        )

    selected_rows: list[pd.Series] = []
    selected_hashes = config["unchanged_v2_contract"]["selected_l2_normalized_wkb_sha256"]
    for source_id in EXPECTED_SELECTED_L2_IDS:
        matches = l2.loc[l2["id"].eq(str(source_id))]
        if len(matches) != 1:
            raise StructuralAuditError(
                "selected_l2_exact_identity",
                expected={"source_id": str(source_id), "row_count": 1},
                observed={"row_count": len(matches)},
            )
        row = matches.iloc[0]
        observed_hash = _normalized_wkb_sha256(row.geometry)
        expected_hash = str(selected_hashes[str(source_id)])
        if observed_hash != expected_hash:
            raise StructuralAuditError(
                "selected_l2_normalized_wkb_sha256",
                expected={"source_id": str(source_id), "sha256": expected_hash},
                observed={"sha256": observed_hash},
            )
        selected_rows.append(row)
    selected_l2 = gpd.GeoDataFrame(
        selected_rows,
        geometry="geometry",
        crs=l2.crs,
    ).reset_index(drop=True)
    selected_record = {
        "source_ids": [str(value) for value in EXPECTED_SELECTED_L2_IDS],
        "normalized_wkb_sha256": {
            str(row["id"]): _normalized_wkb_sha256(row.geometry)
            for _, row in selected_l2.iterrows()
        },
        "row_count": len(selected_l2),
    }
    return (
        repaired_l1,
        selected_l2,
        {
            "l1_original": l1_audit,
            "l2_original": l2_audit,
            "l1_repair": repair_audit,
            "l1_antimeridian": dateline_audit,
            "selected_l2": selected_record,
            "v2_identity_fully_replayed": True,
        },
    )


def run_structural_phase(
    archive_path: Path,
    *,
    config: Mapping[str, Any],
    pilot: Mapping[str, Any],
    amendment_and_base: Mapping[str, Any],
    callback: ProgressCallback | None = None,
    phase_evidence: dict[str, Any] | None = None,
) -> StructuralAuditBundle:
    """Run phase 1 and return the unforgeable phase-2 input bundle."""

    frames, archive_audit = _read_isolated_layers(
        archive_path,
        source_config=config["source"],
        pilot=pilot,
        callback=callback,
        access_evidence=phase_evidence,
    )
    _emit(callback, "phase1.structure.start")
    try:
        repaired_l1, selected_l2, inherited_audit = _authenticate_l1_l2_replay(
            frames[1],
            frames[2],
            pilot=pilot,
            amendment_and_base=amendment_and_base,
            config=config,
        )
        l3_evidence: dict[str, Any] = {}
        if phase_evidence is not None:
            phase_evidence["l3_structure"] = l3_evidence
        selected_l3, hierarchy_audit = audit_l3_structure(
            frames[3],
            selected_l2,
            evidence=l3_evidence,
        )
        if phase_evidence is not None:
            phase_evidence.update(
                {
                    "inherited_l1_l2_audit": inherited_audit,
                    "l3_hierarchy_audit": hierarchy_audit,
                }
            )
    except StructuralAuditError:
        raise
    except GshhgGeometryPilotError as exc:
        raise StructuralAuditError(
            "inherited_v2_structure_replay",
            expected="exact V2 structural identity",
            observed=type(exc).__name__,
            detail=str(exc),
        ) from exc
    _emit(
        callback,
        "phase1.structure.complete",
        l3_rows=len(frames[3]),
        selected_l3_rows=len(selected_l3),
    )
    return StructuralAuditBundle(
        l1_repaired=repaired_l1,
        selected_l2=selected_l2,
        selected_l3=selected_l3,
        archive_audit=archive_audit,
        layer_audit={
            **inherited_audit,
            "l3": hierarchy_audit,
        },
        hierarchy_audit=hierarchy_audit,
        _authorization_token=_STRUCTURAL_BUNDLE_TOKEN,
    )


def _derive_probes(selected_l3: gpd.GeoDataFrame) -> list[dict[str, Any]]:
    """Derive one deterministic source-only probe for every represented parent."""

    probes: list[dict[str, Any]] = []
    for parent_id, children in selected_l3.groupby("parent_id", sort=True):
        ranked_indices = sorted(
            range(len(children)),
            key=lambda index: (
                -float(children.iloc[index]["area"]),
                canonical_source_id_sort_key(children.iloc[index]["id"]),
            ),
        )
        child = children.iloc[ranked_indices[0]]
        normalized_child = shapely.normalize(child.geometry)
        point = normalized_child.representative_point()
        strictly_inside = bool(normalized_child.contains(point))
        on_boundary = bool(normalized_child.boundary.intersects(point))
        if not strictly_inside or on_boundary:
            raise NumericalAuditError(
                "probe_strictly_inside_selected_child",
                expected={"contains": True, "on_boundary": False},
                observed={
                    "parent_id": int(parent_id),
                    "child_source_id": str(child["id"]),
                    "contains": strictly_inside,
                    "on_boundary": on_boundary,
                },
            )
        longitude = float(point.x)
        latitude = float(point.y)
        if (
            not math.isfinite(longitude)
            or not math.isfinite(latitude)
            or latitude < 0.0
            or latitude > 84.0
        ):
            raise NumericalAuditError(
                "probe_northern_utm_domain",
                expected="finite point in northern UTM domain 0 <= latitude <= 84",
                observed={"longitude": longitude, "latitude": latitude},
            )
        zone = math.floor((longitude + 180.0) / 6.0) + 1
        if zone < 1 or zone > 60:
            raise NumericalAuditError(
                "probe_utm_zone",
                expected="zone in 1..60 from frozen formula",
                observed={"longitude": longitude, "zone": zone},
            )
        projected_crs = f"EPSG:{32600 + zone}"
        point_sha = _normalized_geometry_sha256(point)
        probes.append(
            {
                "point_id": f"l3_probe_parent_{int(parent_id)}",
                "city_id": f"l3_probe_parent_{int(parent_id)}",
                "point_kind": "real_l3_source_geometry_probe",
                "label": (f"deterministic representative point for L3 child {str(child['id'])}"),
                "child_source_id": str(child["id"]),
                "parent_id": int(parent_id),
                "source_reported_area": float(child["area"]),
                "normalized_child_wkb_sha256": _normalized_geometry_sha256(normalized_child),
                "longitude": longitude,
                "latitude": latitude,
                "representative_point_longitude": longitude,
                "representative_point_latitude": latitude,
                "representative_point_sha256": point_sha,
                "projected_crs": projected_crs,
                "derived_projected_crs": projected_crs,
                "probe_child_selection_order": (
                    "reported area descending, then canonical source ID"
                ),
                "probe_reselected_after_distance": False,
            }
        )
    if not probes:
        raise NumericalAuditError(
            "minimum_real_probe_count",
            expected=">= 1",
            observed=0,
        )
    return probes


def _exterior_linework(
    frame: gpd.GeoDataFrame,
    *,
    source_level: int,
    shoreline_class: str,
    max_vertices: int,
) -> gpd.GeoDataFrame:
    """Build exterior-only chunks while preserving full official source IDs."""

    if max_vertices < 2:
        raise ValueError("Line chunks require at least two vertices.")
    records: list[dict[str, object]] = []
    ordered_indices = sorted(
        range(len(frame)),
        key=lambda index: canonical_source_id_sort_key(frame.iloc[index]["id"]),
    )
    for index in ordered_indices:
        row = frame.iloc[index]
        source_id = str(row["id"])
        geometry = row.geometry
        if geometry.geom_type != "Polygon":
            raise NumericalAuditError(
                "candidate_exterior_polygon_type",
                expected="Polygon",
                observed={"source_id": source_id, "type": geometry.geom_type},
            )
        runs = _coordinate_runs_without_dateline_seams(
            geometry,
            tolerance=1e-9,
        )
        chunk_count = 0
        for run_index, run in enumerate(runs):
            for chunk_index, chunk in enumerate(
                _chunk_coordinate_run(run, max_vertices=max_vertices)
            ):
                records.append(
                    {
                        "source_level": source_level,
                        "source_id": source_id,
                        "component_id": source_id,
                        "polygon_index": 0,
                        "run_index": run_index,
                        "chunk_index": chunk_index,
                        "shoreline_class": shoreline_class,
                        "geometry": chunk,
                    }
                )
                chunk_count += 1
        if chunk_count == 0:
            raise NumericalAuditError(
                "candidate_exterior_linework_nonempty",
                expected="at least one physical exterior line chunk",
                observed=source_id,
            )
    result = gpd.GeoDataFrame(records, geometry="geometry", crs=WGS84)
    if result.empty:
        raise NumericalAuditError(
            "candidate_exterior_linework_nonempty",
            expected="at least one line",
            observed=0,
        )
    return result


def _regional_l1(
    repaired_l1: gpd.GeoDataFrame,
    points: Sequence[Mapping[str, Any]],
    *,
    maximum_radius_km: float,
) -> gpd.GeoDataFrame:
    indices: set[int] = set()
    for point in points:
        envelope = _conservative_envelope(
            float(point["longitude"]),
            float(point["latitude"]),
            maximum_radius_km,
        )
        indices.update(
            int(value)
            for value in repaired_l1.sindex.query(
                envelope,
                predicate="intersects",
            )
        )
    if not indices:
        raise NumericalAuditError(
            "regional_l1_prefilter",
            expected="at least one L1 polygon",
            observed=0,
        )
    return repaired_l1.iloc[sorted(indices)].copy()


def _linework_variants(
    bundle: StructuralAuditBundle,
    points: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any],
    *,
    callback: ProgressCallback | None,
) -> tuple[
    dict[int, gpd.GeoDataFrame],
    dict[int, gpd.GeoDataFrame],
    dict[str, Any],
]:
    maximum_radius = float(max(settings["search_radii_km"]))
    regional_l1 = _regional_l1(
        bundle.l1_repaired,
        points,
        maximum_radius_km=maximum_radius,
    )
    baseline: dict[int, gpd.GeoDataFrame] = {}
    inclusive: dict[int, gpd.GeoDataFrame] = {}
    counts: dict[str, dict[str, int]] = {}
    for chunk_value in settings["line_chunk_vertex_counts"]:
        chunk_size = int(chunk_value)
        _emit(
            callback,
            "phase2.linework.start",
            line_chunk_vertex_count=chunk_size,
        )
        l1_lines = _exterior_linework(
            regional_l1,
            source_level=1,
            shoreline_class="global_ocean_l1",
            max_vertices=chunk_size,
        )
        l2_lines = _exterior_linework(
            bundle.selected_l2,
            source_level=2,
            shoreline_class="selected_great_lakes_l2",
            max_vertices=chunk_size,
        )
        l3_lines = _exterior_linework(
            bundle.selected_l3,
            source_level=3,
            shoreline_class="all_direct_selected_l3_island_exteriors",
            max_vertices=chunk_size,
        )
        baseline[chunk_size] = gpd.GeoDataFrame(
            pd.concat([l1_lines, l2_lines], ignore_index=True),
            geometry="geometry",
            crs=WGS84,
        )
        inclusive[chunk_size] = gpd.GeoDataFrame(
            pd.concat([l1_lines, l2_lines, l3_lines], ignore_index=True),
            geometry="geometry",
            crs=WGS84,
        )
        counts[str(chunk_size)] = {
            "l1_line_chunks": len(l1_lines),
            "l2_line_chunks": len(l2_lines),
            "l3_line_chunks": len(l3_lines),
            "baseline_line_chunks": len(baseline[chunk_size]),
            "inclusive_line_chunks": len(inclusive[chunk_size]),
        }
        _emit(
            callback,
            "phase2.linework.complete",
            line_chunk_vertex_count=chunk_size,
            inclusive_line_count=len(inclusive[chunk_size]),
        )
    return (
        baseline,
        inclusive,
        {
            "global_l1_polygon_count": len(bundle.l1_repaired),
            "regional_l1_polygon_count": len(regional_l1),
            "selected_l2_polygon_count": len(bundle.selected_l2),
            "selected_l3_polygon_count": len(bundle.selected_l3),
            "maximum_search_radius_km": maximum_radius,
            "native_crs_prefilter_used": True,
            "global_layer_projected_at_once": False,
            "interior_rings_included": False,
            "variant_counts": counts,
        },
    )


def _candidate_lines(
    linework: gpd.GeoDataFrame,
    *,
    longitude: float,
    latitude: float,
    radius_km: float,
) -> gpd.GeoDataFrame:
    envelope = _conservative_envelope(longitude, latitude, radius_km)
    indices = np.unique(linework.sindex.query(envelope, predicate="intersects"))
    if indices.size == 0:
        return linework.iloc[0:0].copy()
    return linework.iloc[indices].reset_index(drop=True)


def _nearest_logical_source_evidence(
    point: gpd.GeoSeries,
    candidates: gpd.GeoDataFrame,
    *,
    projected_crs: str,
    expected_distance_m: float,
    tie_tolerance_m: float,
    require_unique: bool,
) -> dict[str, Any]:
    """Aggregate chunk distances by logical source before applying the tie gate."""

    required = {"source_level", "source_id", "geometry"}
    if not required.issubset(candidates.columns):
        raise NumericalAuditError(
            "nearest_source_evidence_schema",
            expected=sorted(required),
            observed=list(candidates.columns),
        )
    projected_point = point.to_crs(projected_crs).iloc[0]
    projected = candidates.to_crs(projected_crs)
    distances = np.asarray(
        shapely.distance(projected_point, projected.geometry.to_numpy()),
        dtype=np.float64,
    )
    if distances.size == 0 or not np.isfinite(distances).all():
        raise NumericalAuditError(
            "nearest_source_evidence_distance",
            expected="non-empty finite distances",
            observed={"count": distances.size},
        )
    grouped: dict[tuple[int, str], tuple[float, int]] = {}
    for index, distance in enumerate(distances):
        key = (
            int(candidates.iloc[index]["source_level"]),
            str(candidates.iloc[index]["source_id"]),
        )
        current = grouped.get(key)
        value = float(distance)
        if current is None or value < current[0]:
            grouped[key] = (value, index)
    global_minimum = min(value[0] for value in grouped.values())
    if abs(global_minimum - expected_distance_m) > tie_tolerance_m:
        raise NumericalAuditError(
            "nearest_source_evidence_matches_canonical_distance",
            expected=expected_distance_m,
            observed=global_minimum,
        )
    tied = sorted(
        (
            key,
            value,
        )
        for key, value in grouped.items()
        if abs(value[0] - global_minimum) <= tie_tolerance_m
    )
    if require_unique and len(tied) != 1:
        raise NumericalAuditError(
            "unique_nearest_logical_source",
            expected=1,
            observed={
                "tie_count": len(tied),
                "sources": [
                    {"source_level": key[0], "source_id": key[1], "distance_m": value[0]}
                    for key, value in tied
                ],
            },
        )
    (source_level, source_id), (distance, selected_index) = tied[0]
    selected_line = projected.geometry.iloc[selected_index]
    connector = shapely.shortest_line(projected_point, selected_line)
    if connector.is_empty or len(connector.coords) < 2:
        raise NumericalAuditError(
            "nearest_source_coordinate",
            expected="finite shortest-line endpoint",
            observed="empty connector",
        )
    nearest_x, nearest_y = connector.coords[-1][:2]
    transformer = Transformer.from_crs(
        CRS.from_user_input(projected_crs),
        WGS84,
        always_xy=True,
    )
    nearest_longitude, nearest_latitude = transformer.transform(nearest_x, nearest_y)
    return {
        "nearest_source_level": source_level,
        "nearest_source_id": source_id,
        "nearest_longitude": float(nearest_longitude),
        "nearest_latitude": float(nearest_latitude),
        "nearest_tie_count": len(tied),
        "unique_nearest_required": require_unique,
        "nearest_logical_source_distance_m": distance,
        "tie_tolerance_m": tie_tolerance_m,
        "chunk_rows_aggregated_by_logical_source": True,
    }


def _distance_run(
    point: Mapping[str, Any],
    linework: gpd.GeoDataFrame,
    settings: Mapping[str, Any],
    *,
    source_contract: str,
    include_geodesic: bool,
    require_unique_source: bool,
    computation_evidence: dict[str, Any] | None = None,
) -> DistanceRun:
    longitude = float(point["longitude"])
    latitude = float(point["latitude"])
    projected_crs = str(point["projected_crs"])
    point_geometry = gpd.GeoSeries([Point(longitude, latitude)], crs=WGS84)
    tolerance = float(settings["invariance_absolute_tolerance_m"])
    radius_records: list[dict[str, object]] = []
    first_distance: float | None = None
    first_bruteforce: float | None = None
    first_radius: float | None = None
    first_candidates: gpd.GeoDataFrame | None = None
    maximum_radius_difference = 0.0
    for radius_value in settings["search_radii_km"]:
        radius_km = float(radius_value)
        candidates = _candidate_lines(
            linework,
            longitude=longitude,
            latitude=latitude,
            radius_km=radius_km,
        )
        if candidates.empty:
            radius_records.append(
                {
                    "radius_km": radius_km,
                    "candidate_count": 0,
                    "accepted": False,
                }
            )
            continue
        indexed = float(
            nearest_projected_strtree(
                point_geometry,
                candidates,
                projected_crs,
            )[0]
        )
        brute = float(
            nearest_projected_bruteforce(
                point_geometry,
                candidates,
                projected_crs,
            )[0]
        )
        if computation_evidence is not None:
            computation_evidence["distance_values_computed"] = True
            computation_evidence["first_computed_distance_point_id"] = str(
                point["point_id"]
            )
        parity = abs(indexed - brute)
        if parity > tolerance:
            raise NumericalAuditError(
                "strtree_bruteforce_parity",
                expected=f"<= {tolerance} m",
                observed={
                    "point_id": str(point["point_id"]),
                    "radius_km": radius_km,
                    "difference_m": parity,
                },
            )
        accepted = bool(
            math.isfinite(indexed) and math.isfinite(brute) and indexed < radius_km * 1000.0
        )
        difference_from_first: float | None = None
        if accepted and first_distance is None:
            first_distance = indexed
            first_bruteforce = brute
            first_radius = radius_km
            first_candidates = candidates
            difference_from_first = 0.0
        elif accepted:
            assert first_distance is not None
            difference_from_first = abs(indexed - first_distance)
            maximum_radius_difference = max(
                maximum_radius_difference,
                difference_from_first,
            )
            if difference_from_first > tolerance:
                raise NumericalAuditError(
                    "radius_expansion_invariance",
                    expected=f"<= {tolerance} m",
                    observed={
                        "point_id": str(point["point_id"]),
                        "first_radius_km": first_radius,
                        "radius_km": radius_km,
                        "difference_m": difference_from_first,
                    },
                )
        radius_records.append(
            {
                "radius_km": radius_km,
                "candidate_count": len(candidates),
                "strtree_distance_m": indexed,
                "bruteforce_distance_m": brute,
                "strtree_bruteforce_absolute_difference_m": parity,
                "accepted": accepted,
                "absolute_difference_from_first_accepted_m": difference_from_first,
            }
        )
    if (
        first_distance is None
        or first_bruteforce is None
        or first_radius is None
        or first_candidates is None
    ):
        raise NumericalAuditError(
            "search_radius_ladder",
            expected="at least one strictly accepted radius",
            observed={"point_id": str(point["point_id"])},
        )
    if not math.isfinite(first_distance) or first_distance <= 0.0:
        raise NumericalAuditError(
            "finite_positive_distance",
            expected="finite and > 0 m",
            observed={"point_id": str(point["point_id"]), "distance_m": first_distance},
        )
    reversed_distance = float(
        nearest_projected_strtree(
            point_geometry,
            first_candidates.iloc[::-1].reset_index(drop=True),
            projected_crs,
        )[0]
    )
    reverse_difference = abs(reversed_distance - first_distance)
    if reverse_difference > tolerance:
        raise NumericalAuditError(
            "source_forward_reverse_invariance",
            expected=f"<= {tolerance} m",
            observed={
                "point_id": str(point["point_id"]),
                "difference_m": reverse_difference,
            },
        )
    evidence = _nearest_logical_source_evidence(
        point_geometry,
        first_candidates,
        projected_crs=projected_crs,
        expected_distance_m=first_distance,
        tie_tolerance_m=tolerance,
        require_unique=require_unique_source,
    )
    geodesic_distance: float | None = None
    geodesic_audit: dict[str, float] | None = None
    if include_geodesic:
        geodesic_distance = float(
            geodesic_reference_distances(
                point_geometry,
                first_candidates,
                max_step_m=float(settings["geodesic_densification_max_step_m"]),
            )[0]
        )
        try:
            geodesic_audit = require_projected_geodesic_parity(
                [first_distance],
                [geodesic_distance],
                absolute_tolerance_m=float(settings["geodesic_absolute_tolerance_m"]),
                relative_tolerance=float(settings["geodesic_relative_tolerance"]),
            )
        except GshhgGeometryPilotError as exc:
            raise NumericalAuditError(
                "projected_geodesic_parity",
                expected=("difference <= max(100 m, 0.005 times geodesic distance)"),
                observed={
                    "point_id": str(point["point_id"]),
                    "projected_m": first_distance,
                    "geodesic_m": geodesic_distance,
                },
                detail=str(exc),
            ) from exc
    record = {
        "point_id": str(point["point_id"]),
        "point_kind": str(point["point_kind"]),
        "label": str(point["label"]),
        "longitude": longitude,
        "latitude": latitude,
        "projected_crs": projected_crs,
        "source_contract": source_contract,
        "distance_m": first_distance,
        "distance_km": first_distance / 1000.0,
        "accepted_radius_km": first_radius,
        "accepted_candidate_count": len(first_candidates),
        "bruteforce_distance_m": first_bruteforce,
        "strtree_bruteforce_absolute_difference_m": abs(first_distance - first_bruteforce),
        "source_order_reversed_distance_m": reversed_distance,
        "source_order_absolute_difference_m": reverse_difference,
        "maximum_radius_invariance_difference_m": maximum_radius_difference,
        "radius_audit": radius_records,
        "geodesic_distance_m": geodesic_distance,
        "projected_minus_geodesic_m": (
            None if geodesic_distance is None else first_distance - geodesic_distance
        ),
        "geodesic_audit": geodesic_audit,
        "nearest_source_evidence": evidence,
    }
    return DistanceRun(record=record, candidates=first_candidates)


def _fixed_points(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            **dict(point),
            "point_id": str(point["city_id"]),
            "point_kind": "fixed_target_blind_v2_replay_point",
        }
        for point in config["diagnostic_points"]
    ]


def _find_selected_child(
    selected_l3: gpd.GeoDataFrame,
    source_id: str,
) -> pd.Series:
    matches = selected_l3.loc[selected_l3["id"].eq(source_id)]
    if len(matches) != 1:
        raise NumericalAuditError(
            "probe_child_identity",
            expected={"source_id": source_id, "row_count": 1},
            observed={"row_count": len(matches)},
        )
    return matches.iloc[0]


def _probe_gate_record(
    probe: Mapping[str, Any],
    inclusive: DistanceRun,
    baseline: DistanceRun,
    *,
    selected_l3: gpd.GeoDataFrame,
    tolerance_m: float,
) -> dict[str, Any]:
    evidence = inclusive.record["nearest_source_evidence"]
    child_source_id = str(probe["child_source_id"])
    if (
        int(evidence["nearest_source_level"]) != 3
        or str(evidence["nearest_source_id"]) != child_source_id
        or int(evidence["nearest_tie_count"]) != 1
    ):
        raise NumericalAuditError(
            "probe_nearest_candidate_is_unique_own_child",
            expected={
                "nearest_source_level": 3,
                "nearest_source_id": child_source_id,
                "nearest_tie_count": 1,
            },
            observed=evidence,
        )
    child = _find_selected_child(selected_l3, child_source_id)
    point_geometry = gpd.GeoSeries(
        [Point(float(probe["longitude"]), float(probe["latitude"]))],
        crs=WGS84,
    )
    own_exterior = gpd.GeoSeries(
        [LineString(child.geometry.exterior.coords)],
        crs=WGS84,
    )
    direct_own_distance = float(
        nearest_projected_bruteforce(
            point_geometry,
            own_exterior,
            str(probe["projected_crs"]),
        )[0]
    )
    indexed = float(inclusive.record["distance_m"])
    direct_difference = abs(indexed - direct_own_distance)
    if direct_difference > tolerance_m:
        raise NumericalAuditError(
            "probe_indexed_equals_direct_own_exterior",
            expected=f"<= {tolerance_m} m",
            observed={
                "point_id": str(probe["point_id"]),
                "indexed_distance_m": indexed,
                "direct_own_exterior_distance_m": direct_own_distance,
                "difference_m": direct_difference,
            },
        )
    baseline_distance = float(baseline.record["distance_m"])
    improvement = baseline_distance - indexed
    if improvement <= tolerance_m:
        raise NumericalAuditError(
            "probe_l3_strict_improvement",
            expected=f"> {tolerance_m} m",
            observed={
                "point_id": str(probe["point_id"]),
                "l1_l2_only_distance_m": baseline_distance,
                "l3_inclusive_distance_m": indexed,
                "improvement_m": improvement,
            },
        )
    return {
        **dict(probe),
        "inclusive_distance_m": indexed,
        "l1_l2_only_distance_m": baseline_distance,
        "strict_improvement_m": improvement,
        "direct_own_exterior_distance_m": direct_own_distance,
        "indexed_direct_absolute_difference_m": direct_difference,
        "nearest_source_level": int(evidence["nearest_source_level"]),
        "nearest_source_id": str(evidence["nearest_source_id"]),
        "nearest_longitude": float(evidence["nearest_longitude"]),
        "nearest_latitude": float(evidence["nearest_latitude"]),
        "nearest_tie_count": int(evidence["nearest_tie_count"]),
        "shapely_geos_runtime": shapely.geos_version_string,
        "pyproj_runtime": importlib.metadata.version("pyproj"),
        "all_probe_gates_passed": True,
    }


def _diagnostic_table(
    fixed_records: Sequence[Mapping[str, Any]],
    probe_records: Sequence[Mapping[str, Any]],
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for record in fixed_records:
        evidence = record["nearest_source_evidence"]
        rows.append(
            {
                "point_id": record["point_id"],
                "point_kind": record["point_kind"],
                "parent_id": None,
                "child_source_id": None,
                "label": record["label"],
                "longitude": record["longitude"],
                "latitude": record["latitude"],
                "projected_crs": record["projected_crs"],
                "distance_m": record["distance_m"],
                "distance_km": record["distance_km"],
                "l1_l2_only_distance_m": record["l1_l2_only_distance_m"],
                "l3_improvement_m": (
                    float(record["l1_l2_only_distance_m"]) - float(record["distance_m"])
                ),
                "v2_expected_distance_m": record["v2_expected_distance_m"],
                "v2_absolute_error_m": record["v2_absolute_error_m"],
                "accepted_radius_km": record["accepted_radius_km"],
                "accepted_candidate_count": record["accepted_candidate_count"],
                "bruteforce_distance_m": record["bruteforce_distance_m"],
                "geodesic_distance_m": record["geodesic_distance_m"],
                "projected_minus_geodesic_m": record["projected_minus_geodesic_m"],
                "nearest_source_level": evidence["nearest_source_level"],
                "nearest_source_id": evidence["nearest_source_id"],
                "nearest_tie_count": evidence["nearest_tie_count"],
            }
        )
    for probe in probe_records:
        rows.append(
            {
                "point_id": probe["point_id"],
                "point_kind": probe["point_kind"],
                "parent_id": probe["parent_id"],
                "child_source_id": probe["child_source_id"],
                "label": probe["label"],
                "longitude": probe["longitude"],
                "latitude": probe["latitude"],
                "projected_crs": probe["projected_crs"],
                "distance_m": probe["inclusive_distance_m"],
                "distance_km": float(probe["inclusive_distance_m"]) / 1000.0,
                "l1_l2_only_distance_m": probe["l1_l2_only_distance_m"],
                "l3_improvement_m": probe["strict_improvement_m"],
                "v2_expected_distance_m": None,
                "v2_absolute_error_m": None,
                "accepted_radius_km": probe["accepted_radius_km"],
                "accepted_candidate_count": probe["accepted_candidate_count"],
                "bruteforce_distance_m": probe["bruteforce_distance_m"],
                "geodesic_distance_m": probe["geodesic_distance_m"],
                "projected_minus_geodesic_m": probe["projected_minus_geodesic_m"],
                "nearest_source_level": probe["nearest_source_level"],
                "nearest_source_id": probe["nearest_source_id"],
                "nearest_tie_count": probe["nearest_tie_count"],
            }
        )
    return pd.DataFrame(rows).sort_values("point_id", kind="stable").reset_index(drop=True)


def _diagnostic_table_bytes(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(
        index=False,
        lineterminator="\n",
        float_format="%.12g",
    ).encode("utf-8")


def run_numerical_phase(
    bundle: StructuralAuditBundle,
    *,
    config: Mapping[str, Any],
    callback: ProgressCallback | None = None,
    phase_evidence: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Run phase 2; a completed phase-1 bundle is mandatory."""

    if not isinstance(bundle, StructuralAuditBundle):
        raise TypeError("Phase 2 requires a completed StructuralAuditBundle.")
    if bundle._authorization_token is not _STRUCTURAL_BUNDLE_TOKEN:
        raise TypeError("Phase 2 requires a bundle authorized by the structural phase.")
    settings = config["numerical_audit"]
    required_settings = {
        "search_radii_km": [64, 128, 256, 512, 1024, 2048],
        "line_chunk_vertex_counts": [256, 1024, 4096],
        "canonical_line_chunk_vertex_count": 1024,
        "query_chunk_sizes": [1, 2, 4],
        "worker_counts": [1, 2, 4],
        "invariance_absolute_tolerance_m": 0.000001,
        "geodesic_densification_max_step_m": 50.0,
        "geodesic_absolute_tolerance_m": 100.0,
        "geodesic_relative_tolerance": 0.005,
        "existing_four_point_absolute_tolerance_m": 0.000001,
    }
    for field, expected in required_settings.items():
        if settings.get(field) != expected:
            raise GshhgL3HierarchyAuditError(
                f"Frozen numerical setting changed before phase 2: {field}"
            )

    _emit(callback, "phase2.probes.start")
    probes = _derive_probes(bundle.selected_l3)
    if phase_evidence is not None:
        phase_evidence["probe_derived"] = True
        phase_evidence["probe_count"] = len(probes)
    fixed_points = _fixed_points(config)
    all_points = [*fixed_points, *probes]
    _emit(callback, "phase2.probes.complete", probe_count=len(probes))
    baseline_variants, inclusive_variants, linework_audit = _linework_variants(
        bundle,
        all_points,
        settings,
        callback=callback,
    )
    canonical_chunk = int(settings["canonical_line_chunk_vertex_count"])
    if canonical_chunk not in baseline_variants or canonical_chunk not in inclusive_variants:
        raise NumericalAuditError(
            "canonical_line_chunk_variant",
            expected=canonical_chunk,
            observed=sorted(inclusive_variants),
        )
    canonical_baseline = baseline_variants[canonical_chunk]
    canonical_inclusive = inclusive_variants[canonical_chunk]

    canonical_runs: dict[str, DistanceRun] = {}
    baseline_runs: dict[str, DistanceRun] = {}
    for point_index, point in enumerate(all_points, start=1):
        point_id = str(point["point_id"])
        if phase_evidence is not None:
            phase_evidence["distance_computation_started"] = True
            phase_evidence["distance_stage_started_at_point_id"] = point_id
        _emit(
            callback,
            "phase2.distance.start",
            point_id=point_id,
            point_index=point_index,
            point_total=len(all_points),
        )
        canonical_runs[point_id] = _distance_run(
            point,
            canonical_inclusive,
            settings,
            source_contract="gshhg_l1_l2_plus_all_selected_direct_l3_exteriors",
            include_geodesic=True,
            require_unique_source=point["point_kind"] == "real_l3_source_geometry_probe",
            computation_evidence=phase_evidence,
        )
        baseline_runs[point_id] = _distance_run(
            point,
            canonical_baseline,
            settings,
            source_contract="gshhg_v2_l1_l2_only",
            include_geodesic=False,
            require_unique_source=False,
        )
        if phase_evidence is not None:
            phase_evidence.setdefault("completed_distance_point_ids", []).append(point_id)
        _emit(
            callback,
            "phase2.distance.complete",
            point_id=point_id,
        )

    baselines = {
        str(name): float(value) for name, value in settings["v2_gshhg_baseline_distance_m"].items()
    }
    fixed_records: list[dict[str, Any]] = []
    maximum_v2_error = 0.0
    replay_tolerance = float(settings["existing_four_point_absolute_tolerance_m"])
    for point in fixed_points:
        point_id = str(point["point_id"])
        inclusive_record = dict(canonical_runs[point_id].record)
        baseline_record = baseline_runs[point_id].record
        expected = baselines[point_id]
        inclusive_error = abs(float(inclusive_record["distance_m"]) - expected)
        baseline_error = abs(float(baseline_record["distance_m"]) - expected)
        maximum_v2_error = max(maximum_v2_error, inclusive_error, baseline_error)
        if inclusive_error > replay_tolerance or baseline_error > replay_tolerance:
            raise NumericalAuditError(
                "existing_four_point_v2_replay",
                expected={"distance_m": expected, "tolerance_m": replay_tolerance},
                observed={
                    "point_id": point_id,
                    "inclusive_distance_m": inclusive_record["distance_m"],
                    "inclusive_error_m": inclusive_error,
                    "l1_l2_only_distance_m": baseline_record["distance_m"],
                    "l1_l2_only_error_m": baseline_error,
                },
            )
        inclusive_record.update(
            {
                "l1_l2_only_distance_m": float(baseline_record["distance_m"]),
                "v2_expected_distance_m": expected,
                "v2_absolute_error_m": inclusive_error,
                "l1_l2_v2_absolute_error_m": baseline_error,
                "census_layer_opened": False,
            }
        )
        fixed_records.append(inclusive_record)

    probe_records: list[dict[str, Any]] = []
    for probe in probes:
        point_id = str(probe["point_id"])
        gate = _probe_gate_record(
            probe,
            canonical_runs[point_id],
            baseline_runs[point_id],
            selected_l3=bundle.selected_l3,
            tolerance_m=float(config["probe_rule"]["distance_equality_absolute_tolerance_m"]),
        )
        inclusive_record = canonical_runs[point_id].record
        gate.update(
            {
                "accepted_radius_km": inclusive_record["accepted_radius_km"],
                "accepted_candidate_count": inclusive_record["accepted_candidate_count"],
                "bruteforce_distance_m": inclusive_record["bruteforce_distance_m"],
                "geodesic_distance_m": inclusive_record["geodesic_distance_m"],
                "projected_minus_geodesic_m": inclusive_record["projected_minus_geodesic_m"],
            }
        )
        probe_records.append(gate)

    line_chunk_runs: list[dict[str, Any]] = []
    maximum_line_chunk_difference = 0.0
    for chunk_size, linework in sorted(inclusive_variants.items()):
        point_differences: dict[str, float] = {}
        if chunk_size == canonical_chunk:
            continue
        _emit(
            callback,
            "phase2.line_chunk_invariance.start",
            line_chunk_vertex_count=chunk_size,
        )
        for point in all_points:
            point_id = str(point["point_id"])
            observed = _distance_run(
                point,
                linework,
                settings,
                source_contract=("gshhg_l1_l2_plus_all_selected_direct_l3_exteriors"),
                include_geodesic=False,
                require_unique_source=point["point_kind"] == "real_l3_source_geometry_probe",
            )
            difference = abs(
                float(observed.record["distance_m"])
                - float(canonical_runs[point_id].record["distance_m"])
            )
            if difference > float(settings["invariance_absolute_tolerance_m"]):
                raise NumericalAuditError(
                    "line_chunk_invariance",
                    expected=(f"<= {float(settings['invariance_absolute_tolerance_m'])} m"),
                    observed={
                        "point_id": point_id,
                        "line_chunk_vertex_count": chunk_size,
                        "difference_m": difference,
                    },
                )
            point_differences[point_id] = difference
            maximum_line_chunk_difference = max(
                maximum_line_chunk_difference,
                difference,
            )
        line_chunk_runs.append(
            {
                "line_chunk_vertex_count": chunk_size,
                "absolute_differences_m": point_differences,
                "maximum_absolute_difference_m": max(
                    point_differences.values(),
                    default=0.0,
                ),
            }
        )
        _emit(
            callback,
            "phase2.line_chunk_invariance.complete",
            line_chunk_vertex_count=chunk_size,
        )

    thread_tasks = {
        (str(point["point_id"]), "gshhg_l1_l2_l3"): (
            point,
            canonical_runs[str(point["point_id"])].candidates,
            float(canonical_runs[str(point["point_id"])].record["distance_m"]),
        )
        for point in all_points
    }
    _emit(callback, "phase2.worker_query_chunk_invariance.start")
    try:
        worker_audit = _thread_and_query_chunk_audit(thread_tasks, settings)
    except GshhgGeometryPilotError as exc:
        raise NumericalAuditError(
            "worker_and_query_chunk_invariance",
            expected=(f"<= {float(settings['invariance_absolute_tolerance_m'])} m"),
            observed=type(exc).__name__,
            detail=str(exc),
        ) from exc
    _emit(callback, "phase2.worker_query_chunk_invariance.complete")

    table = _diagnostic_table(fixed_records, probe_records)
    numerical_audit = {
        "fixed_v2_replays": fixed_records,
        "real_l3_probes": probe_records,
        "linework": linework_audit,
        "gates": {
            "strtree_bruteforce_all_passed": True,
            "radius_expansion_all_passed": True,
            "source_forward_reverse_all_passed": True,
            "projected_geodesic_all_passed": True,
            "existing_four_point_l1_l2_and_l3_inclusive_replay_all_passed": True,
            "maximum_existing_four_point_absolute_error_m": maximum_v2_error,
            "line_chunk_invariance": {
                "canonical_line_chunk_vertex_count": canonical_chunk,
                "runs": line_chunk_runs,
                "maximum_absolute_difference_m": maximum_line_chunk_difference,
                "tolerance_m": float(settings["invariance_absolute_tolerance_m"]),
                "all_runs_invariant": True,
            },
            "worker_and_query_chunk_invariance": worker_audit,
        },
        "floating_dtype": "float64",
        "census_layer_opened": False,
        "probe_reselection_after_distance": False,
        "all_numerical_gates_passed": True,
    }
    if phase_evidence is not None:
        phase_evidence["phase_2_complete"] = True
        phase_evidence["diagnostic_row_count"] = len(table)
    return numerical_audit, table


def _publish_or_authenticate_bytes(content: bytes, destination: Path) -> None:
    if destination.exists():
        if not destination.is_file() or destination.read_bytes() != content:
            raise GshhgL3HierarchyAuditError(
                f"Existing append-only artifact has different bytes: {destination}"
            )
        return
    _publish_new_bytes(content, destination)


def _terminal_paths(
    project_root: Path,
    config: Mapping[str, Any],
) -> tuple[Path, Path, Path, Path]:
    outputs = config["outputs"]
    success = _resolve_project_path(project_root, str(outputs["success_manifest"]))
    v1_failure = _resolve_project_path(
        project_root,
        str(outputs["v1_failure_manifest"]),
    )
    v2_failure = (project_root / DEFAULT_FAILURE_MANIFEST).resolve()
    table = _resolve_project_path(project_root, str(outputs["diagnostic_table"]))
    expected = (
        (project_root / DEFAULT_MANIFEST).resolve(),
        (project_root / DEFAULT_V1_FAILURE_MANIFEST).resolve(),
        (project_root / DEFAULT_FAILURE_MANIFEST).resolve(),
        (project_root / DEFAULT_DIAGNOSTIC_TABLE).resolve(),
    )
    if (success, v1_failure, v2_failure, table) != expected:
        raise GshhgL3HierarchyAuditError("The preregistered canonical output paths changed.")
    return success, v1_failure, v2_failure, table


def _amendment_terminal_evidence(
    amendment: V2Amendment,
) -> tuple[dict[str, Any], dict[str, Any]]:
    correction = amendment.contract["correction"]
    amendment_record = {
        "path": AMENDMENT_PATH,
        "file_sha256": amendment.file_sha256,
        "publication_git_commit": AMENDMENT_PUBLICATION_GIT_COMMIT,
        "tracked_blob_sha1": EXPECTED_AMENDMENT_BLOB_SHA1,
        "amendment_id": amendment.contract["amendment"]["amendment_id"],
        "exact_change_count": 1,
        "field_path": correction["field_path"],
        "corrected_source_id": CORRECTED_SOURCE_ID,
        "old_sha256": V1_EXPECTED_SOURCE_HASH,
        "new_sha256": V2_CORRECTED_SOURCE_HASH,
        "all_other_structure_probe_numerical_and_access_rules_unchanged": True,
        "effective_contract_semantic_sha256": canonical_sha256(
            amendment.effective_config
        ),
    }
    v1_record = {
        "path": DEFAULT_V1_FAILURE_MANIFEST.as_posix(),
        "file_sha256": amendment.v1_failure_file_sha256,
        "commit_sha256": amendment.v1_failure["commit_sha256"],
        "publication_git_commit": V1_FAILURE_PUBLICATION_GIT_COMMIT,
        "run_head": V1_RUN_HEAD,
        "state": amendment.v1_failure["state"],
        "phase": amendment.v1_failure["phase"],
        "gate": amendment.v1_failure["gate"],
        "probe_derived": False,
        "distance_values_computed": False,
    }
    return amendment_record, v1_record


def _failure_payload(
    error: StructuralAuditError | NumericalAuditError,
    *,
    phase: str,
    project_root: Path,
    config_path: Path,
    git_gate: GitGate,
    preregistration: Mapping[str, Any],
    v2_amendment: V2Amendment,
    phase_evidence: Mapping[str, Any],
    probe_derived: bool,
    distance_values_computed: bool,
) -> dict[str, Any]:
    amendment_record, v1_record = _amendment_terminal_evidence(v2_amendment)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": f"{ALGORITHM_VERSION}-failure-record",
        "state": FAILURE_STATE,
        "phase": phase,
        "gate": error.gate,
        "expected": error.expected,
        "observed": error.observed,
        "detail": error.detail,
        "config": {
            "path": config_path.relative_to(project_root).as_posix(),
            "sha256": sha256_file(config_path),
        },
        "preregistration": {
            "path": PREREGISTRATION_PATH,
            "file_sha256": sha256_file(project_root / PREREGISTRATION_PATH),
            "commit_sha256": preregistration["commit_sha256"],
        },
        "structural_amendment": amendment_record,
        "prior_v1_failure": v1_record,
        "repository": {
            "branch": git_gate.branch,
            "head": git_gate.head,
            "origin_main": git_gate.origin_main,
            "head_equals_origin_main": git_gate.head == git_gate.origin_main,
            "working_tree_clean_at_preflight_archive_open_and_publish": True,
            "tracked_blob_sha1": git_gate.tracked_blob_sha1,
        },
        "phase_evidence": dict(phase_evidence),
        "access_contract": {
            "network_requests": 0,
            "gshhg_archive_opened": True,
            "authorized_l1_l2_l3_members_may_have_been_opened": True,
            "authorized_member_allowlist": list(AUTHORIZED_MEMBERS),
            "gshhg_l4_member_opened": False,
            "census_layer_opened": False,
            "other_public_source_geometry_opened": False,
            "eligible_land_grid_opened": False,
            "probe_derived": probe_derived,
            "distance_values_computed": distance_values_computed,
            "distance_feature_surface_computed": False,
            "tract_aggregation_performed": False,
            "predictor_values_computed": False,
            "predictor_construction_performed": False,
            "model_fit_performed": False,
            "model_predictions_computed": False,
            "landsat_thermal_values_read": False,
            "landsat_target_qa_values_read": False,
            "external_target_files_opened": False,
            "final_evaluation_outputs_opened": False,
            "geometry_exported_or_redistributed": False,
        },
        "locks": {
            "source_lock_created": False,
            "algorithm_lock_created": False,
            "feature_names_frozen": False,
            "predictor_build_authorized": False,
            "protocol_lock_created": False,
            "external_targets_unlocked": False,
            "external_target_values_read": False,
            "external_prediction_commit_exists": False,
        },
        "amendment_policy": {
            "failure_record_is_append_only": True,
            "tolerance_probe_or_gate_may_be_relaxed_after_failure": False,
            "separate_committed_and_pushed_amendment_required": True,
        },
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def _success_payload(
    *,
    project_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    preregistration: Mapping[str, Any],
    v2_amendment: V2Amendment,
    git_gate: GitGate,
    bundle: StructuralAuditBundle,
    numerical_audit: Mapping[str, Any],
    table_path: Path,
    table_bytes: bytes,
    table: pd.DataFrame,
) -> dict[str, Any]:
    amendment_record, v1_record = _amendment_terminal_evidence(v2_amendment)
    code_sha, code_runtime = code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=CODE_PATHS,
        algorithm_version=ALGORITHM_VERSION,
    )
    code_runtime["packages"]["pyproj"] = importlib.metadata.version("pyproj")
    code_runtime["packages"]["pyogrio"] = importlib.metadata.version("pyogrio")
    code_runtime["relative_paths"] = list(CODE_PATHS)
    code_runtime["base_fingerprint_sha256"] = code_sha
    code_runtime["sha256"] = canonical_sha256(code_runtime)
    serialized_table = pd.read_csv(io.BytesIO(table_bytes))
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": COMPLETE_STATE,
        "scope": "target-blind source-only GSHHG L3 hierarchy audit",
        "config": {
            "path": config_path.relative_to(project_root).as_posix(),
            "sha256": sha256_file(config_path),
            "all_other_preregistered_structure_probe_and_numerical_gates_unchanged": True,
            "exact_documented_source_identity_correction_count": 1,
        },
        "planning_authorization": {
            "path": PLAN_PATH,
            "file_sha256": sha256_file(project_root / PLAN_PATH),
            "commit_sha256": EXPECTED_PLAN_COMMIT_SHA256,
            "authorized_stage": ("target_blind_gshhg_l3_hierarchy_geometry_audit"),
        },
        "preregistration": {
            "path": PREREGISTRATION_PATH,
            "file_sha256": sha256_file(project_root / PREREGISTRATION_PATH),
            "commit_sha256": preregistration["commit_sha256"],
            "preregistration_id": preregistration["preregistration_id"],
        },
        "structural_amendment": amendment_record,
        "prior_v1_failure": v1_record,
        "repository": {
            "branch": git_gate.branch,
            "head": git_gate.head,
            "origin_main": git_gate.origin_main,
            "head_equals_origin_main": git_gate.head == git_gate.origin_main,
            "working_tree_clean_at_preflight_archive_open_and_publish": True,
            "tracked_blob_sha1": git_gate.tracked_blob_sha1,
        },
        "source_archive": bundle.archive_audit,
        "source_layers": bundle.layer_audit,
        "hierarchy_audit": bundle.hierarchy_audit,
        "numerical_audit": dict(numerical_audit),
        "diagnostic_table": {
            "path": _recorded_path(project_root, table_path),
            "bytes": len(table_bytes),
            "sha256": hashlib.sha256(table_bytes).hexdigest(),
            "rows": len(table),
            "semantic_sha256": canonical_sha256(
                serialized_table[
                    [
                        "point_id",
                        "point_kind",
                        "distance_m",
                        "l1_l2_only_distance_m",
                    ]
                ].to_dict("records")
            ),
        },
        "locks": {
            "source_lock_created": False,
            "algorithm_lock_created": False,
            "feature_names_frozen": False,
            "predictor_build_authorized": False,
            "protocol_lock_created": False,
            "external_targets_unlocked": False,
            "external_target_values_read": False,
            "external_prediction_commit_exists": False,
        },
        "access_contract": {
            "audit_program_network_requests": 0,
            "gshhg_archive_opened": True,
            "authorized_gshhg_member_count_opened": len(AUTHORIZED_MEMBERS),
            "authorized_gshhg_members": list(AUTHORIZED_MEMBERS),
            "unauthorized_gshhg_members_opened": 0,
            "gshhg_l4_member_opened": False,
            "census_layer_opened": False,
            "other_public_source_geometry_opened": False,
            "eligible_land_grid_opened": False,
            "fixed_target_blind_source_geometry_distances_computed": True,
            "distance_feature_surface_computed": False,
            "tract_aggregation_performed": False,
            "predictor_values_computed": False,
            "predictor_construction_performed": False,
            "model_fit_performed": False,
            "model_predictions_computed": False,
            "landsat_thermal_values_read": False,
            "landsat_target_qa_values_read": False,
            "external_target_files_opened": False,
            "final_evaluation_outputs_opened": False,
            "geometry_exported_or_redistributed": False,
        },
        "decision": {
            "audit_passed": True,
            "source_frozen": False,
            "algorithm_frozen": False,
            "predictor_build_authorized": False,
            "next_safe_stage": (
                "separate_portable_water_distance_source_and_algorithm_freeze_decision"
            ),
        },
        "code_runtime": code_runtime,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def _require_false_mapping_fields(
    value: object,
    *,
    fields: Sequence[str],
    label: str,
) -> None:
    if not isinstance(value, dict):
        raise GshhgL3HierarchyAuditError(f"{label} must be a JSON object.")
    changed = {field: value.get(field) for field in fields if value.get(field) is not False}
    if changed:
        raise GshhgL3HierarchyAuditError(
            f"{label} contains an opened lock or forbidden access claim: {changed}"
        )


def _require_exact_mapping_fields(
    value: object,
    *,
    expected: Mapping[str, object],
    label: str,
) -> None:
    if not isinstance(value, dict):
        raise GshhgL3HierarchyAuditError(f"{label} must be a JSON object.")
    changed = {
        field: {"expected": expected_value, "observed": value.get(field)}
        for field, expected_value in expected.items()
        if type(value.get(field)) is not type(expected_value)
        or value.get(field) != expected_value
    }
    if changed:
        raise GshhgL3HierarchyAuditError(
            f"{label} exact evidence changed: {changed}"
        )


def _authenticate_terminal_repository(
    project_root: Path,
    *,
    terminal_path: Path,
    terminal: Mapping[str, Any],
) -> GitGate:
    terminal_relative = terminal_path.relative_to(project_root).as_posix()
    required_paths = tuple(dict.fromkeys((*_required_git_paths(project_root), terminal_relative)))
    git_gate = _git_blob_records(project_root, required_paths=required_paths)
    repository = terminal.get("repository")
    if not isinstance(repository, dict):
        raise GshhgL3HierarchyAuditError("The terminal lacks authenticated repository evidence.")
    _require_exact_mapping_fields(
        repository,
        expected={
            "branch": "main",
            "origin_main": repository.get("head"),
            "head_equals_origin_main": True,
        },
        label="terminal repository",
    )
    if "working_tree_clean_at_preflight_archive_open_and_publish" in repository:
        _require_exact_mapping_fields(
            repository,
            expected={
                "working_tree_clean_at_preflight_archive_open_and_publish": True,
            },
            label="terminal repository",
        )
    recorded_blobs = repository.get("tracked_blob_sha1")
    if not isinstance(recorded_blobs, dict):
        raise GshhgL3HierarchyAuditError("The terminal lacks its executor blob fingerprints.")
    mismatches = {
        path: {
            "recorded": recorded_blobs.get(path),
            "current": git_gate.tracked_blob_sha1.get(path),
        }
        for path in CODE_PATHS
        if recorded_blobs.get(path) != git_gate.tracked_blob_sha1.get(path)
    }
    if mismatches:
        raise GshhgL3HierarchyAuditError(
            f"Executor blobs changed after the terminal run: {mismatches}"
        )
    recorded_head = repository.get("head")
    if not isinstance(recorded_head, str) or re.fullmatch(r"[0-9a-f]{40}", recorded_head) is None:
        raise GshhgL3HierarchyAuditError("The terminal run HEAD is invalid.")
    ancestor = subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "merge-base",
            "--is-ancestor",
            recorded_head,
            git_gate.head,
        ],
        check=False,
        capture_output=True,
    ).returncode
    if ancestor != 0:
        raise GshhgL3HierarchyAuditError(
            "The terminal run commit is not an ancestor of current HEAD."
        )
    return git_gate


def _authenticate_terminal_locks_and_access(
    terminal: Mapping[str, Any],
    *,
    label: str,
) -> None:
    _require_false_mapping_fields(
        terminal.get("locks"),
        fields=(
            "source_lock_created",
            "algorithm_lock_created",
            "feature_names_frozen",
            "predictor_build_authorized",
            "protocol_lock_created",
            "external_targets_unlocked",
            "external_target_values_read",
            "external_prediction_commit_exists",
        ),
        label=f"{label} locks",
    )
    _require_false_mapping_fields(
        terminal.get("access_contract"),
        fields=(
            "gshhg_l4_member_opened",
            "census_layer_opened",
            "other_public_source_geometry_opened",
            "eligible_land_grid_opened",
            "distance_feature_surface_computed",
            "tract_aggregation_performed",
            "predictor_values_computed",
            "predictor_construction_performed",
            "model_fit_performed",
            "model_predictions_computed",
            "landsat_thermal_values_read",
            "landsat_target_qa_values_read",
            "external_target_files_opened",
            "final_evaluation_outputs_opened",
            "geometry_exported_or_redistributed",
        ),
        label=f"{label} access contract",
    )


def _expected_runtime_fingerprint(project_root: Path) -> dict[str, Any]:
    code_sha, expected = code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=CODE_PATHS,
        algorithm_version=ALGORITHM_VERSION,
    )
    expected["packages"]["pyproj"] = importlib.metadata.version("pyproj")
    expected["packages"]["pyogrio"] = importlib.metadata.version("pyogrio")
    expected["relative_paths"] = list(CODE_PATHS)
    expected["base_fingerprint_sha256"] = code_sha
    expected["sha256"] = canonical_sha256(expected)
    return expected


def _authenticate_v2_terminal_lineage(
    terminal: Mapping[str, Any],
    amendment: V2Amendment,
) -> None:
    expected_amendment, expected_v1 = _amendment_terminal_evidence(amendment)
    _require_exact_object(
        terminal.get("structural_amendment"),
        expected=expected_amendment,
        label="V2 terminal structural-amendment lineage",
    )
    _require_exact_object(
        terminal.get("prior_v1_failure"),
        expected=expected_v1,
        label="V2 terminal prior-failure lineage",
    )


def _authenticate_failure_phase_evidence(
    failure: Mapping[str, Any],
    *,
    phase: str,
) -> None:
    phase_evidence = failure.get("phase_evidence")
    access = failure.get("access_contract")
    if not isinstance(phase_evidence, dict) or not isinstance(access, dict):
        raise GshhgL3HierarchyAuditError(
            "The failure phase/access evidence must be JSON objects."
        )
    fields = ("probe_derived", "distance_values_computed")
    if phase == "phase_1_structure":
        if phase_evidence.get("phase_1_started") is not True:
            raise GshhgL3HierarchyAuditError(
                "The phase-1 failure evidence does not retain its start gate."
            )
        for field in fields:
            observed = phase_evidence.get(field, False)
            if type(observed) is not bool or observed is not False:
                raise GshhgL3HierarchyAuditError(
                    f"Phase-1 failure phase evidence opened {field}: {observed!r}"
                )
            if access.get(field) is not False:
                raise GshhgL3HierarchyAuditError(
                    f"Phase-1 failure access evidence opened {field}."
                )
        return
    if phase != "phase_2_numerical":
        raise GshhgL3HierarchyAuditError(
            f"The failure terminal phase is not preregistered: {phase!r}"
        )
    if phase_evidence.get("phase_1_complete") is not True:
        raise GshhgL3HierarchyAuditError(
            "The phase-2 failure lacks completed phase-1 evidence."
        )
    values: dict[str, bool] = {}
    for field in fields:
        phase_value = phase_evidence.get(field)
        access_value = access.get(field)
        if type(phase_value) is not bool or type(access_value) is not bool:
            raise GshhgL3HierarchyAuditError(
                f"Phase-2 failure {field} evidence must be a strict boolean."
            )
        if phase_value is not access_value:
            raise GshhgL3HierarchyAuditError(
                f"Phase-2 failure phase/access evidence disagrees for {field}."
            )
        values[field] = phase_value
    if values["distance_values_computed"] and not values["probe_derived"]:
        raise GshhgL3HierarchyAuditError(
            "Phase-2 failure cannot compute distances before deriving probes."
        )


def authenticate_l3_audit_terminal(
    config_path: str | Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Authenticate a committed terminal without reopening source data."""

    project_root, resolved_config, base_config = _read_config(config_path)
    if sha256_file(resolved_config) != EXPECTED_CONFIG_SHA256:
        raise GshhgL3HierarchyAuditError("The exact audit config changed.")
    plan = audit_multicity_plan(
        project_root / "configs/multicity/experiment.toml",
        output_path=project_root / PLAN_PATH,
        write=False,
    )
    if (
        plan.get("commit_sha256") != EXPECTED_PLAN_COMMIT_SHA256
        or sha256_file(project_root / PLAN_PATH) != EXPECTED_PLAN_FILE_SHA256
    ):
        raise GshhgL3HierarchyAuditError("The exact v6 planning authorization changed.")
    preregistration, preregistration_sha = _read_json_object(
        project_root / PREREGISTRATION_PATH,
        label="L3 preregistration",
    )
    if (
        preregistration_sha != EXPECTED_PREREGISTRATION_FILE_SHA256
        or preregistration.get("commit_sha256") != EXPECTED_PREREGISTRATION_COMMIT_SHA256
    ):
        raise GshhgL3HierarchyAuditError("The exact preregistration changed.")
    pilot, _ = _read_json_object(
        project_root / PILOT_PATH,
        label="GSHHG V2 pilot",
    )
    v2_amendment = _authenticate_v2_amendment(
        project_root,
        base_config_path=resolved_config,
        base_config=base_config,
        preregistration=preregistration,
        pilot=pilot,
    )
    config = v2_amendment.effective_config

    success_path, v1_failure_path, failure_path, table_path = _terminal_paths(
        project_root,
        config,
    )
    if not v1_failure_path.is_file():
        raise GshhgL3HierarchyAuditError(
            "The preserved V1 failure is required for every V2 terminal."
        )
    if success_path.exists() and failure_path.exists():
        raise GshhgL3HierarchyAuditError(
            "V2 success and V2 failure terminals cannot both exist."
        )
    if failure_path.exists():
        failure, _ = _read_json_object(
            failure_path,
            label="L3 audit V2 failure manifest",
        )
        _require_exact_mapping_fields(
            failure,
            expected={
                "schema_version": SCHEMA_VERSION,
                "algorithm_version": f"{ALGORITHM_VERSION}-failure-record",
                "state": FAILURE_STATE,
            },
            label="failure terminal identity",
        )
        if table_path.exists():
            raise GshhgL3HierarchyAuditError(
                "A failure terminal cannot coexist with a diagnostic table."
            )
        _authenticate_terminal_repository(
            project_root,
            terminal_path=failure_path,
            terminal=failure,
        )
        _require_exact_mapping_fields(
            failure.get("config"),
            expected={
                "path": resolved_config.relative_to(project_root).as_posix(),
                "sha256": EXPECTED_CONFIG_SHA256,
            },
            label="failure terminal input identity: config",
        )
        _require_exact_mapping_fields(
            failure.get("preregistration"),
            expected={
                "path": PREREGISTRATION_PATH,
                "file_sha256": EXPECTED_PREREGISTRATION_FILE_SHA256,
                "commit_sha256": EXPECTED_PREREGISTRATION_COMMIT_SHA256,
            },
            label="failure terminal input identity: preregistration",
        )
        _authenticate_v2_terminal_lineage(failure, v2_amendment)
        _authenticate_terminal_locks_and_access(failure, label="failure")
        phase = failure.get("phase")
        if phase == "phase_1_structure":
            _require_exact_mapping_fields(
                failure.get("access_contract"),
                expected={
                    "network_requests": 0,
                    "gshhg_archive_opened": True,
                    "authorized_l1_l2_l3_members_may_have_been_opened": True,
                    "authorized_member_allowlist": list(AUTHORIZED_MEMBERS),
                    "probe_derived": False,
                    "distance_values_computed": False,
                },
                label="phase-1 failure access evidence",
            )
        elif phase == "phase_2_numerical":
            _require_exact_mapping_fields(
                failure.get("access_contract"),
                expected={
                    "network_requests": 0,
                    "gshhg_archive_opened": True,
                    "authorized_l1_l2_l3_members_may_have_been_opened": True,
                    "authorized_member_allowlist": list(AUTHORIZED_MEMBERS),
                },
                label="phase-2 failure access evidence",
            )
        else:
            raise GshhgL3HierarchyAuditError(
                f"The failure terminal phase is not preregistered: {phase!r}"
            )
        _authenticate_failure_phase_evidence(failure, phase=str(phase))
        return failure
    if not success_path.exists():
        if table_path.exists():
            raise GshhgL3HierarchyAuditError(
                "The preserved V1 failure cannot coexist with an orphan diagnostic table."
            )
        return v2_amendment.v1_failure

    success, _ = _read_json_object(
        success_path,
        label="L3 audit success manifest",
    )
    _require_exact_mapping_fields(
        success,
        expected={
            "schema_version": SCHEMA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "state": COMPLETE_STATE,
        },
        label="success terminal identity",
    )
    _authenticate_terminal_repository(
        project_root,
        terminal_path=success_path,
        terminal=success,
    )
    _require_exact_mapping_fields(
        success.get("config"),
        expected={
            "path": resolved_config.relative_to(project_root).as_posix(),
            "sha256": EXPECTED_CONFIG_SHA256,
            "all_other_preregistered_structure_probe_and_numerical_gates_unchanged": True,
            "exact_documented_source_identity_correction_count": 1,
        },
        label="success terminal input identity: config",
    )
    _require_exact_mapping_fields(
        success.get("planning_authorization"),
        expected={
            "path": PLAN_PATH,
            "file_sha256": EXPECTED_PLAN_FILE_SHA256,
            "commit_sha256": EXPECTED_PLAN_COMMIT_SHA256,
            "authorized_stage": "target_blind_gshhg_l3_hierarchy_geometry_audit",
        },
        label="success terminal input identity: planning authorization",
    )
    _require_exact_mapping_fields(
        success.get("preregistration"),
        expected={
            "path": PREREGISTRATION_PATH,
            "file_sha256": EXPECTED_PREREGISTRATION_FILE_SHA256,
            "commit_sha256": EXPECTED_PREREGISTRATION_COMMIT_SHA256,
            "preregistration_id": preregistration["preregistration_id"],
        },
        label="success terminal input identity: preregistration",
    )
    _authenticate_v2_terminal_lineage(success, v2_amendment)
    if (
        success.get("hierarchy_audit", {}).get("all_structural_gates_passed") is not True
        or success.get("numerical_audit", {}).get("all_numerical_gates_passed") is not True
        or success.get("decision", {}).get("audit_passed") is not True
    ):
        raise GshhgL3HierarchyAuditError(
            "The success terminal does not retain all completed gates."
        )
    _authenticate_terminal_locks_and_access(success, label="success")
    _require_exact_mapping_fields(
        success.get("access_contract"),
        expected={
            "audit_program_network_requests": 0,
            "gshhg_archive_opened": True,
            "authorized_gshhg_member_count_opened": len(AUTHORIZED_MEMBERS),
            "authorized_gshhg_members": list(AUTHORIZED_MEMBERS),
            "unauthorized_gshhg_members_opened": 0,
            "fixed_target_blind_source_geometry_distances_computed": True,
        },
        label="success access evidence",
    )
    _require_exact_mapping_fields(
        success.get("decision"),
        expected={
            "audit_passed": True,
            "source_frozen": False,
            "algorithm_frozen": False,
            "predictor_build_authorized": False,
            "next_safe_stage": (
                "separate_portable_water_distance_source_and_algorithm_freeze_decision"
            ),
        },
        label="success decision",
    )
    archive = success.get("source_archive")
    if (
        not isinstance(archive, dict)
        or archive.get("sha256") != str(config["source"]["expected_archive_sha256"])
        or archive.get("bytes") != int(config["source"]["expected_archive_bytes"])
        or archive.get("authorized_member_count") != len(AUTHORIZED_MEMBERS)
        or archive.get("member_open_log") != list(AUTHORIZED_MEMBERS)
        or archive.get("unauthorized_member_open_count") != 0
        or archive.get("zipfile_testzip_called") is not False
    ):
        raise GshhgL3HierarchyAuditError("The success terminal source-access evidence changed.")

    table_record = success.get("diagnostic_table")
    if not isinstance(table_record, dict) or not table_path.is_file():
        raise GshhgL3HierarchyAuditError("The canonical diagnostic table is missing.")
    _require_exact_mapping_fields(
        table_record,
        expected={"path": _recorded_path(project_root, table_path)},
        label="diagnostic table",
    )
    if table_path.stat().st_size != table_record.get("bytes") or sha256_file(
        table_path
    ) != table_record.get("sha256"):
        raise GshhgL3HierarchyAuditError("The canonical diagnostic table bytes changed.")
    try:
        table = pd.read_csv(table_path)
    except (OSError, pd.errors.ParserError) as exc:
        raise GshhgL3HierarchyAuditError(
            "Cannot parse the authenticated diagnostic table."
        ) from exc
    if len(table) != table_record.get("rows") or canonical_sha256(
        table[
            [
                "point_id",
                "point_kind",
                "distance_m",
                "l1_l2_only_distance_m",
            ]
        ].to_dict("records")
    ) != table_record.get("semantic_sha256"):
        raise GshhgL3HierarchyAuditError("The diagnostic table semantics changed.")

    code_runtime = success.get("code_runtime")
    if not isinstance(code_runtime, dict):
        raise GshhgL3HierarchyAuditError(
            "The success terminal code-runtime fingerprint is missing."
        )
    code_body = {key: value for key, value in code_runtime.items() if key != "sha256"}
    if code_runtime.get("sha256") != canonical_sha256(code_body):
        raise GshhgL3HierarchyAuditError(
            "The success terminal code-runtime fingerprint is invalid."
        )
    if code_runtime != _expected_runtime_fingerprint(project_root):
        raise GshhgL3HierarchyAuditError(
            "The current executor or code-runtime fingerprint differs from the terminal."
        )
    return success


def audit_gshhg_l3_hierarchy(
    config_path: str | Path = DEFAULT_CONFIG,
    *,
    write: bool = True,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run the canonical audit or authenticate its existing terminal."""

    if not write:
        return authenticate_l3_audit_terminal(config_path)
    (
        project_root,
        resolved_config,
        config,
        preregistration,
        pilot,
        amendment_and_base,
        v2_amendment,
        git_gate,
        required_paths,
    ) = _authenticate_pre_archive_inputs(config_path, callback=progress)
    success_path, v1_failure_path, failure_path, table_path = _terminal_paths(
        project_root,
        config,
    )
    if not v1_failure_path.is_file():
        raise GshhgL3HierarchyAuditError(
            "The preserved V1 failure disappeared after amendment preflight."
        )
    if success_path.exists() or failure_path.exists():
        raise GshhgL3HierarchyAuditError(
            "An append-only V2 terminal already exists; use --check-only."
        )
    _emit(progress, "preflight.archive_gate.start")
    _same_git_gate(
        project_root,
        required_paths=required_paths,
        expected=git_gate,
    )
    _emit(progress, "preflight.archive_gate.complete")

    archive_path = _resolve_project_path(
        project_root,
        str(config["source"]["archive_path"]),
    )
    phase_evidence: dict[str, Any] = {
        "archive_path": _recorded_path(project_root, archive_path),
        "phase_1_started": True,
    }
    try:
        try:
            bundle = run_structural_phase(
                archive_path,
                config=config,
                pilot=pilot,
                amendment_and_base=amendment_and_base,
                callback=progress,
                phase_evidence=phase_evidence,
            )
        except StructuralAuditError:
            raise
        except (
            GshhgGeometryPilotError,
            ProjError,
            shapely.errors.GEOSException,
            ValueError,
        ) as raw_error:
            raise StructuralAuditError(
                "structural_dependency_execution",
                expected="all frozen structural dependencies complete successfully",
                observed=type(raw_error).__name__,
                detail=str(raw_error),
            ) from raw_error
    except StructuralAuditError as exc:
        failure = _failure_payload(
            exc,
            phase="phase_1_structure",
            project_root=project_root,
            config_path=resolved_config,
            git_gate=git_gate,
            preregistration=preregistration,
            v2_amendment=v2_amendment,
            phase_evidence=phase_evidence,
            probe_derived=False,
            distance_values_computed=False,
        )
        _same_git_gate(
            project_root,
            required_paths=required_paths,
            expected=git_gate,
        )
        if table_path.exists():
            raise GshhgL3HierarchyAuditError(
                "Refusing to publish a structural failure beside an orphan diagnostic table."
            ) from exc
        _publish_new_bytes(_canonical_json_bytes(failure), failure_path)
        _emit(progress, "terminal.failure", phase="phase_1_structure", gate=exc.gate)
        return failure

    phase_evidence.update(
        {
            "archive": bundle.archive_audit,
            "hierarchy": bundle.hierarchy_audit,
            "phase_1_complete": True,
            "probe_derived": False,
            "distance_values_computed": False,
        }
    )
    try:
        try:
            numerical_audit, table = run_numerical_phase(
                bundle,
                config=config,
                callback=progress,
                phase_evidence=phase_evidence,
            )
        except NumericalAuditError:
            raise
        except (
            GshhgGeometryPilotError,
            ProjError,
            shapely.errors.GEOSException,
            ValueError,
        ) as raw_error:
            raise NumericalAuditError(
                "numerical_dependency_execution",
                expected="all frozen numerical dependencies complete successfully",
                observed=type(raw_error).__name__,
                detail=str(raw_error),
            ) from raw_error
    except NumericalAuditError as exc:
        failure = _failure_payload(
            exc,
            phase="phase_2_numerical",
            project_root=project_root,
            config_path=resolved_config,
            git_gate=git_gate,
            preregistration=preregistration,
            v2_amendment=v2_amendment,
            phase_evidence=phase_evidence,
            probe_derived=bool(phase_evidence.get("probe_derived", False)),
            distance_values_computed=bool(phase_evidence.get("distance_values_computed", False)),
        )
        _same_git_gate(
            project_root,
            required_paths=required_paths,
            expected=git_gate,
        )
        if table_path.exists():
            raise GshhgL3HierarchyAuditError(
                "Refusing to publish a numerical failure beside an orphan diagnostic table."
            ) from exc
        _publish_new_bytes(_canonical_json_bytes(failure), failure_path)
        _emit(progress, "terminal.failure", phase="phase_2_numerical", gate=exc.gate)
        return failure

    table_bytes = _diagnostic_table_bytes(table)
    success = _success_payload(
        project_root=project_root,
        config_path=resolved_config,
        config=config,
        preregistration=preregistration,
        v2_amendment=v2_amendment,
        git_gate=git_gate,
        bundle=bundle,
        numerical_audit=numerical_audit,
        table_path=table_path,
        table_bytes=table_bytes,
        table=table,
    )
    _emit(progress, "preflight.publish_gate.start")
    _same_git_gate(
        project_root,
        required_paths=required_paths,
        expected=git_gate,
    )
    _emit(progress, "preflight.publish_gate.complete")
    _publish_or_authenticate_bytes(table_bytes, table_path)
    _publish_new_bytes(_canonical_json_bytes(success), success_path)
    _emit(
        progress,
        "terminal.success",
        state=success["state"],
        diagnostic_rows=len(table),
    )
    return success
