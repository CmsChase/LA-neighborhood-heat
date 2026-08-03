"""Append-only, target-blind source evidence for the portable predictor contract.

This stage fills only the four gaps recorded by the V1 predictor-contract
decision.  It may acquire public boundary/source metadata and the static NLCD
and SRTM predictor sources.  It cannot build predictors, fit a model, inspect
thermal/QA targets, or read the completed Los Angeles evaluation.

The older Phoenix geography and source-footprint manifests are immutable.
Phoenix receives a new supplement; Houston and Chicago receive first-time
geography and source-footprint manifests plus the same supplement.  A global
terminal is written last, after every city checkpoint reauthenticates.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import tomllib
import uuid
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final, Protocol
from urllib.parse import urlparse

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio import Affine

from la_heat.multicity import geography as _geography
from la_heat.multicity import source_footprints as _footprints
from la_heat.multicity.config import CitySpec, MulticityPlan, load_multicity_plan
from la_heat.multicity.workspace import MulticityWorkspace
from la_heat.provenance import (
    atomic_json,
    atomic_parquet,
    canonical_frame_sha256,
    canonical_sha256,
    code_runtime_fingerprint,
    geometry_semantic_sha256,
    parquet_file_record,
    sha256_file,
)

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "portable-predictor-source-evidence-v1"
STAGE_ID: Final = "portable_predictor_missing_source_evidence_staging"
COMPLETE_STATE: Final = "complete_target_blind_portable_predictor_source_evidence"
CONFIG_PATH: Final = "configs/multicity/portable_predictor_source_evidence_v1.toml"
TERMINAL_PATH: Final = (
    "manifests/multicity/reviews/portable_predictor_contract/"
    "PORTABLE_PREDICTOR_SOURCE_EVIDENCE_V1.json"
)
OUTPUT_PATH: Final = TERMINAL_PATH
CONFIG_SHA256: Final = "d352ba7ad86c0a231e631ada1f95fd72a3a2e47b9e4185dc7687ad462b736f27"

CODE_PATHS: Final = (
    CONFIG_PATH,
    "configs/multicity/experiment.toml",
    "configs/multicity/cities/phoenix_az.toml",
    "configs/multicity/cities/houston_tx.toml",
    "configs/multicity/cities/chicago_il.toml",
    "scripts/stage_multicity_portable_predictor_source_evidence_v1.py",
    "src/la_heat/daymet_grid.py",
    "src/la_heat/multicity/config.py",
    "src/la_heat/multicity/geography.py",
    "src/la_heat/multicity/portable_predictor_source_evidence_v1.py",
    "src/la_heat/multicity/source_footprints.py",
    "src/la_heat/multicity/workspace.py",
    "src/la_heat/provenance.py",
    "src/la_heat/static_sources.py",
)

TRACKED_OUTPUT_PATHS: Final = (
    "manifests/multicity/cities/houston_tx/geography/GEOGRAPHY.json",
    "manifests/multicity/cities/chicago_il/geography/GEOGRAPHY.json",
    "manifests/multicity/cities/houston_tx/source_footprints/SOURCE_FOOTPRINTS.json",
    "manifests/multicity/cities/chicago_il/source_footprints/SOURCE_FOOTPRINTS.json",
    "manifests/multicity/cities/phoenix_az/source_evidence/"
    "PORTABLE_PREDICTOR_SOURCE_EVIDENCE_V1.json",
    "manifests/multicity/cities/houston_tx/source_evidence/"
    "PORTABLE_PREDICTOR_SOURCE_EVIDENCE_V1.json",
    "manifests/multicity/cities/chicago_il/source_evidence/"
    "PORTABLE_PREDICTOR_SOURCE_EVIDENCE_V1.json",
    TERMINAL_PATH,
)

EXPECTED_AUTHORIZED_NOW: Final = {
    "boundary_and_public_metadata_staging": False,
    "target_blind_source_geometry_review": False,
    "target_blind_gshhg_l3_hierarchy_preregistration": False,
    "target_blind_gshhg_l3_hierarchy_geometry_read": False,
    "portable_predictor_source_freeze": False,
    "portable_predictor_source_and_calibration_contract_freeze": False,
    "portable_predictor_missing_source_evidence_staging": True,
    "predictor_construction": False,
    "model_fitting": False,
    "external_target_or_qa_value_access": False,
    "one_time_external_evaluation": False,
    "operational_forecast_claim": False,
}

_REQUIRED_FALSE_LOCKS: Final = (
    "protocol_locked",
    "external_targets_unlocked",
    "external_target_values_read",
    "external_prediction_commit_exists",
    "portable_water_distance_feature_names_frozen",
    "predictor_build_authorized",
    "protocol_lock_created",
)
_LAND_COVER_CODES: Final = frozenset(
    {0, 11, 12, 21, 22, 23, 24, 31, 41, 42, 43, 51, 52, 71, 72, 73, 74, 81, 82, 90, 95}
)
_FORBIDDEN_PATH_PARTS: Final = frozenset(
    {"final_test_2025", "final_evaluation", "model_lock"}
)
_SELECTED_HEADERS: Final = frozenset(
    {"content-type", "content-length", "etag", "last-modified"}
)


class PortablePredictorSourceEvidenceV1Error(ValueError):
    """Raised when the narrow source-evidence contract cannot be proven."""


class _ResponseLike(Protocol):
    headers: Mapping[str, str]

    def raise_for_status(self) -> None: ...

    def iter_content(self, *, chunk_size: int) -> object: ...

    def close(self) -> None: ...


class _HttpClientLike(Protocol):
    def get(self, url: str, **kwargs: object) -> _ResponseLike: ...

    def post(self, url: str, **kwargs: object) -> object: ...

    def head(self, url: str, **kwargs: object) -> object: ...


@dataclass(frozen=True, slots=True)
class SourceEvidenceConfig:
    """Validated exact stage configuration."""

    path: Path
    project_root: Path
    raw: dict[str, Any]

    @property
    def semantic_sha256(self) -> str:
        return canonical_sha256(self.raw)

    def project_path(self, value: str) -> Path:
        path = (self.project_root / value).resolve()
        if not path.is_relative_to(self.project_root):
            raise PortablePredictorSourceEvidenceV1Error(
                f"Configured path escapes the project root: {value}"
            )
        if {part.lower() for part in path.parts} & _FORBIDDEN_PATH_PARTS:
            raise PortablePredictorSourceEvidenceV1Error(
                f"Configured path overlaps a prohibited result area: {value}"
            )
        return path


def expected_authorized_now() -> dict[str, bool]:
    """Return the exact canonical source-evidence permission map."""

    return deepcopy(EXPECTED_AUTHORIZED_NOW)


def expected_plan_authorization_scope() -> dict[str, Any]:
    """Return the detailed, hashable stage scope that canonical planning must bind."""

    return {
        "stage_id": STAGE_ID,
        "experiment_id": "la_to_three_city_zero_shot_v1",
        "configuration": {"path": CONFIG_PATH, "sha256": CONFIG_SHA256},
        "code_paths": list(CODE_PATHS),
        "cities": [
            {
                "id": "phoenix_az",
                "census_place_geoid": "0455000",
                "geography_action": "authenticate_existing",
                "source_footprint_action": "authenticate_existing",
                "static_source_action": "create_append_only_supplement",
            },
            {
                "id": "houston_tx",
                "census_place_geoid": "4835000",
                "geography_action": "create_first_target_blind_snapshot",
                "source_footprint_action": "create_first_metadata_snapshot",
                "static_source_action": "create_append_only_supplement",
            },
            {
                "id": "chicago_il",
                "census_place_geoid": "1714000",
                "geography_action": "create_first_target_blind_snapshot",
                "source_footprint_action": "create_first_metadata_snapshot",
                "static_source_action": "create_append_only_supplement",
            },
        ],
        "network": {
            "GET": [
                "tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
                "tigerWMS_Census2020/MapServer/{6,26}[,/query]",
                "services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/"
                "USA_Census_2020_Redistricting_{Tracts,Incorporated_Places}/"
                "FeatureServer/0[,/query]",
                "www.arcgis.com/sharing/rest/content/items/"
                "{e3a7d2d3e5834b7eb6b1c2943141ced6,13ea1fb24ca14842bb265e6ec6ac1d46}",
                "cmr.earthdata.nasa.gov/search/granules.json",
                "www.mrlc.gov/geoserver/ows",
                "opentopography.s3.sdsc.edu/raster/SRTM_GL1/"
                "SRTM_GL1_srtm/[NS][0-9]{2}[EW][0-9]{3}.tif",
            ],
            "POST": ["planetarycomputer.microsoft.com/api/stac/v1/search"],
            "HEAD": [
                "opentopography.s3.sdsc.edu/raster/SRTM_GL1/"
                "SRTM_GL1_srtm/[NS][0-9]{2}[EW][0-9]{3}.tif"
            ],
            "cross_origin_redirects_allowed": False,
            "maximum_network_requests": 1000,
            "maximum_single_download_bytes": 268_435_456,
            "maximum_total_download_bytes": 1_073_741_824,
            "maximum_stac_pages_per_query": 100,
        },
        "source_products": {
            "nlcd": {
                "release": "NLCD 2016 original release",
                "coverage_ids": [
                    "mrlc_download__NLCD_2016_Land_Cover_L48",
                    "mrlc_download__NLCD_2016_Impervious_L48",
                ],
                "native_grid": "EPSG:5070 30 m",
            },
            "terrain": {
                "release": "NASA SRTM GL1 v3 via OpenTopography",
                "opentopo_id": "OTSRTM.082015.4326.1",
                "schema": "EPSG:4326 int16 3601x3601 Point EGM96 metre",
            },
            "metadata": ["Census 2020", "Landsat C2 L2", "Sentinel-2 L2A", "Daymet V4R1"],
        },
        "candidate_downstream_rules": {
            "status": "recorded_for_v2_review_not_executed_or_frozen",
            "land_cover_resampling": "nearest",
            "impervious_resampling": "bilinear",
            "impervious_scale_divisor": 100.0,
            "impervious_scientific_nodata": 127,
            "elevation_resampling": "bilinear",
            "slope_algorithm": "Horn 3x3",
            "slope_source_halo_cells": 1,
            "minimum_valid_coverage_fraction": 0.98,
            "eligible_land_denominator_invariant_across_dates": True,
        },
        "reads": {
            "authenticated_phoenix_geography_and_source_footprint": True,
            "public_houston_and_chicago_place_and_tract_geometry": True,
            "public_source_metadata": True,
            "nlcd_and_srtm_static_source_payloads": True,
        },
        "tracked_output_paths": list(TRACKED_OUTPUT_PATHS),
        "write_contract": {
            "append_only": True,
            "terminal_written_last": True,
            "terminal_requires_all_city_checkpoints": True,
            "check_only_network_requests": 0,
            "tracked_output_set_must_be_exact": True,
        },
        "prohibitions": {
            "target_or_qa_values": True,
            "thermal_values": True,
            "predictor_construction": True,
            "model_fit_or_prediction": True,
            "final_evaluation_outputs": True,
            "protocol_or_feature_name_freeze": True,
            "operational_forecast_claim": True,
        },
    }


def _require_exact_keys(
    payload: Mapping[str, Any], expected: set[str], *, label: str
) -> None:
    if set(payload) != expected:
        raise PortablePredictorSourceEvidenceV1Error(
            f"{label} keys changed; missing={sorted(expected - set(payload))}, "
            f"unexpected={sorted(set(payload) - expected)}."
        )


def _read_config(path: str | Path) -> SourceEvidenceConfig:
    config_path = Path(path).resolve()
    if not config_path.is_file():
        raise FileNotFoundError(config_path)
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    _require_exact_keys(
        raw,
        {
            "stage",
            "dates",
            "landsat",
            "sentinel",
            "daymet",
            "nlcd",
            "terrain",
            "outputs",
            "limits",
            "candidate_downstream_rules",
            "access_contract",
        },
        label="source-evidence config",
    )
    project_root = config_path.parents[2]
    stage = raw["stage"]
    expected_stage = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "stage_id": STAGE_ID,
        "experiment_id": "la_to_three_city_zero_shot_v1",
        "experiment_config": "configs/multicity/experiment.toml",
        "plan_path": "manifests/multicity/PLAN_READINESS.json",
        "authorized_city_ids": ["phoenix_az", "houston_tx", "chicago_il"],
        "new_geography_city_ids": ["houston_tx", "chicago_il"],
        "new_source_footprint_city_ids": ["houston_tx", "chicago_il"],
        "confirmation_year": 2025,
        "analysis_crs": "EPSG:5070",
    }
    if stage != expected_stage:
        raise PortablePredictorSourceEvidenceV1Error(
            "The exact source-evidence stage identity or city scope changed."
        )
    if raw["outputs"] != {
        "terminal": TERMINAL_PATH,
        "city_evidence_manifest_name": "PORTABLE_PREDICTOR_SOURCE_EVIDENCE_V1.json",
        "raw_stage_directory": "portable_predictor_source_evidence_v1",
    }:
        raise PortablePredictorSourceEvidenceV1Error("Output paths changed.")
    if raw["limits"] != {
        "maximum_network_requests": 1000,
        "maximum_single_download_bytes": 268_435_456,
        "maximum_total_download_bytes": 1_073_741_824,
        "maximum_stac_pages_per_query": 100,
    }:
        raise PortablePredictorSourceEvidenceV1Error("Network safety limits changed.")
    if raw["candidate_downstream_rules"] != {
        "status": "recorded_for_v2_review_not_executed_or_frozen",
        "land_cover_resampling": "nearest",
        "impervious_resampling": "bilinear",
        "impervious_scale_divisor": 100.0,
        "impervious_scientific_nodata": 127,
        "elevation_resampling": "bilinear",
        "slope_algorithm": "Horn 3x3",
        "slope_source_halo_cells": 1,
        "minimum_valid_coverage_fraction": 0.98,
        "eligible_land_denominator_invariant_across_dates": True,
    }:
        raise PortablePredictorSourceEvidenceV1Error(
            "Candidate downstream rule evidence changed."
        )
    expected_access = {
        "external_target_or_qa_values_read": False,
        "landsat_thermal_values_read": False,
        "landsat_target_qa_values_read": False,
        "external_lst_values_read": False,
        "predictor_construction_performed": False,
        "model_fit_performed": False,
        "model_predictions_computed": False,
        "final_evaluation_outputs_opened": False,
        "only_public_source_metadata_and_static_predictor_sources_opened": True,
    }
    if raw["access_contract"] != expected_access:
        raise PortablePredictorSourceEvidenceV1Error("Access contract changed.")
    config = SourceEvidenceConfig(config_path, project_root, raw)
    if sha256_file(config_path) != CONFIG_SHA256:
        raise PortablePredictorSourceEvidenceV1Error(
            "Source-evidence config bytes differ from the preregistered SHA-256."
        )
    for key in (stage["experiment_config"], stage["plan_path"], raw["outputs"]["terminal"]):
        config.project_path(key)
    return config


def _json_with_commit(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = path.read_bytes()
    after = path.read_bytes()
    if before != after:
        raise PortablePredictorSourceEvidenceV1Error(f"{label} changed while read.")
    try:
        payload = json.loads(before.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PortablePredictorSourceEvidenceV1Error(f"Cannot parse {label}.") from exc
    if not isinstance(payload, dict):
        raise PortablePredictorSourceEvidenceV1Error(f"{label} must be a JSON object.")
    recorded = payload.get("commit_sha256")
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or canonical_sha256(body) != recorded:
        raise PortablePredictorSourceEvidenceV1Error(f"{label} commit is invalid.")
    return payload, before


def _authenticate_plan(
    config: SourceEvidenceConfig,
    *,
    publication_authenticator: Any | None = None,
) -> dict[str, Any]:
    path = config.project_path(config.raw["stage"]["plan_path"])
    payload, raw = _json_with_commit(path, label="canonical planning v10")
    if publication_authenticator is None:
        # Lazy import avoids the intentional planning -> scope-provider cycle.
        from la_heat.multicity.plan_source_evidence_hotfix_transition_v10 import (
            authorize_multicity_source_evidence_hotfix_resume,
        )

        publication_authenticator = authorize_multicity_source_evidence_hotfix_resume
    authenticated = publication_authenticator(
        project_root=config.project_root,
        output_path=path,
        write=False,
    )
    if authenticated != payload:
        raise PortablePredictorSourceEvidenceV1Error(
            "Publication-aware v10 authentication disagrees with canonical planning bytes."
        )
    return _plan_authorization_record(config, payload, raw)


def _plan_authorization_record(
    config: SourceEvidenceConfig,
    payload: Mapping[str, Any],
    raw: bytes,
) -> dict[str, Any]:
    """Validate the exact scientific boundary and return its byte identity."""

    path = config.project_path(config.raw["stage"]["plan_path"])
    if (
        type(payload.get("schema_version")) is not int
        or payload["schema_version"] != 10
        or payload.get("state") != "planning_ready"
        or payload.get("experiment_id") != config.raw["stage"]["experiment_id"]
        or payload.get("authorized_now") != expected_authorized_now()
        or payload.get("portable_predictor_source_evidence_stage_authorization_scope")
        != expected_plan_authorization_scope()
    ):
        raise PortablePredictorSourceEvidenceV1Error(
            "Canonical planning does not authorize this exact evidence stage."
        )
    locks = payload.get("locks")
    if not isinstance(locks, dict) or any(
        locks.get(key) is not False for key in _REQUIRED_FALSE_LOCKS
    ):
        raise PortablePredictorSourceEvidenceV1Error(
            "Predictor/model/target/protocol locks are not intact."
        )
    if locks.get("portable_water_distance_source_locked") is not True:
        raise PortablePredictorSourceEvidenceV1Error("Water source lock was lost.")
    if locks.get("portable_water_distance_algorithm_locked") is not True:
        raise PortablePredictorSourceEvidenceV1Error("Water algorithm lock was lost.")
    return {
        "path": path.relative_to(config.project_root).as_posix(),
        "bytes": len(raw),
        "file_sha256": hashlib.sha256(raw).hexdigest(),
        "commit_sha256": payload["commit_sha256"],
        "schema_version": 10,
        "state": "planning_ready",
        "authorized_now": expected_authorized_now(),
        "portable_predictor_source_evidence_stage_authorization_scope": (
            expected_plan_authorization_scope()
        ),
    }


def _resume_git_preflight(
    config: SourceEvidenceConfig,
    *,
    allowed_untracked_paths: Sequence[str],
) -> str:
    """Allow only already-written append-only checkpoints during a resume."""

    allowed = set(allowed_untracked_paths)
    if not allowed or not allowed.issubset(set(TRACKED_OUTPUT_PATHS)):
        raise PortablePredictorSourceEvidenceV1Error(
            "Resume paths are empty or outside the preregistered tracked outputs."
        )
    for relative in allowed:
        path = config.project_path(relative)
        if path.is_symlink() or not path.is_file():
            raise PortablePredictorSourceEvidenceV1Error(
                f"Resume checkpoint is not one regular file: {relative}"
            )

    branch = _run_git(config.project_root, "branch", "--show-current")
    head = _run_git(config.project_root, "rev-parse", "HEAD")
    origin = _run_git(config.project_root, "rev-parse", "origin/main")
    status = _run_git(
        config.project_root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        binary=True,
    )
    assert isinstance(branch, str)
    assert isinstance(head, str)
    assert isinstance(origin, str)
    assert isinstance(status, bytes)
    head = head.strip()
    if branch.strip() != "main" or head != origin.strip():
        raise PortablePredictorSourceEvidenceV1Error(
            "Resume requires synchronized main."
        )

    fields = status.split(b"\0")
    if fields[-1:] != [b""]:
        raise PortablePredictorSourceEvidenceV1Error(
            "Git resume status is not valid NUL-delimited output."
        )
    observed: set[str] = set()
    for field in fields[:-1]:
        if not field.startswith(b"?? "):
            raise PortablePredictorSourceEvidenceV1Error(
                "Resume permits only untracked append-only checkpoints."
            )
        try:
            relative = field[3:].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PortablePredictorSourceEvidenceV1Error(
                "Git resume status contains a non-UTF-8 path."
            ) from exc
        if relative not in allowed or relative in observed:
            raise PortablePredictorSourceEvidenceV1Error(
                f"Unexpected dirty path during resume: {relative}"
            )
        observed.add(relative)
    if observed != allowed:
        raise PortablePredictorSourceEvidenceV1Error(
            "Resume checkpoints and Git untracked paths disagree."
        )
    return head


def _authenticate_plan_for_resume(
    config: SourceEvidenceConfig,
    *,
    allowed_untracked_paths: Sequence[str],
    historical_authenticator: Any | None = None,
    publication_locator: Any | None = None,
) -> dict[str, Any]:
    """Authenticate published v10 without rejecting exact partial checkpoints."""

    head = _resume_git_preflight(
        config,
        allowed_untracked_paths=allowed_untracked_paths,
    )
    path = config.project_path(config.raw["stage"]["plan_path"])
    payload, raw = _json_with_commit(path, label="canonical planning v10")
    if historical_authenticator is None or publication_locator is None:
        from la_heat.multicity.plan_source_evidence_hotfix_transition_v10 import (
            _locate_v10_publication_commit,
            authenticate_historical_v10_payload,
        )

        if historical_authenticator is None:
            historical_authenticator = authenticate_historical_v10_payload
        if publication_locator is None:
            publication_locator = _locate_v10_publication_commit
    publication = publication_locator(
        config.project_root,
        payload,
        current_head=head,
    )
    authenticated = historical_authenticator(
        config.project_root,
        payload,
        publication_commit=publication,
        current_head=head,
    )
    if authenticated != payload:
        raise PortablePredictorSourceEvidenceV1Error(
            "Historical v10 resume authentication disagrees with canonical planning bytes."
        )
    return _plan_authorization_record(config, payload, raw)


def _relative(config: SourceEvidenceConfig, path: Path) -> str:
    resolved = path.resolve()
    if not resolved.is_relative_to(config.project_root):
        raise PortablePredictorSourceEvidenceV1Error(f"Artifact escapes project root: {path}")
    if {part.lower() for part in resolved.parts} & _FORBIDDEN_PATH_PARTS:
        raise PortablePredictorSourceEvidenceV1Error(f"Artifact enters prohibited area: {path}")
    return resolved.relative_to(config.project_root).as_posix()


def _file_record(config: SourceEvidenceConfig, path: Path) -> dict[str, Any]:
    return {
        "path": _relative(config, path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _verify_file_record(config: SourceEvidenceConfig, record: Mapping[str, Any]) -> Path:
    if set(record) != {"path", "bytes", "sha256"}:
        raise PortablePredictorSourceEvidenceV1Error("File record schema changed.")
    path = config.project_path(str(record["path"]))
    if (
        not path.is_file()
        or type(record["bytes"]) is not int
        or path.stat().st_size != record["bytes"]
        or sha256_file(path) != record["sha256"]
    ):
        raise PortablePredictorSourceEvidenceV1Error(f"File record failed: {record['path']}")
    return path


def _atomic_bytes_no_clobber(content: bytes, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not destination.is_file() or destination.read_bytes() != content:
            raise PortablePredictorSourceEvidenceV1Error(
                f"Existing append-only artifact differs: {destination}"
            )
        return
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.partial")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _write_manifest_no_clobber(payload: dict[str, Any], destination: Path) -> None:
    if destination.exists():
        raise PortablePredictorSourceEvidenceV1Error(
            f"Append-only manifest already exists: {destination}"
        )
    payload["commit_sha256"] = canonical_sha256(payload)
    atomic_json(payload, destination)


def _city(plan: MulticityPlan, city_id: str) -> CitySpec:
    city = next((item for item in plan.cities if item.id == city_id), None)
    if (
        city is None
        or city.role != "external_confirmation"
        or city.target_values_status != "sealed"
    ):
        raise PortablePredictorSourceEvidenceV1Error(f"Invalid external city: {city_id}")
    return city


class _StrictClient:
    """Allow only preregistered method/host/path combinations; reject every redirect."""

    def __init__(
        self,
        session: Any,
        *,
        allowed: Mapping[str, set[tuple[str, str]]],
        maximum_requests: int,
    ) -> None:
        self._session = session
        self._allowed = {method: set(values) for method, values in allowed.items()}
        self._maximum_requests = maximum_requests
        self.request_count = 0

    def _request(self, method: str, url: str, **kwargs: object) -> Any:
        parsed = urlparse(url)
        identity = (str(parsed.hostname or "").lower(), parsed.path.rstrip("/"))
        if parsed.scheme != "https" or identity not in self._allowed.get(method, set()):
            raise PortablePredictorSourceEvidenceV1Error(
                f"Unpreregistered {method.upper()} URL: {parsed.scheme}://{parsed.netloc}{parsed.path}"
            )
        self.request_count += 1
        if self.request_count > self._maximum_requests:
            raise PortablePredictorSourceEvidenceV1Error("Network request limit exceeded.")
        # Redirects are rejected before a second request can occur.  This is
        # intentionally stricter than checking ``response.history`` afterward.
        kwargs["allow_redirects"] = False
        response = getattr(self._session, method)(url, **kwargs)
        status_code = getattr(response, "status_code", None)
        if type(status_code) is int and 300 <= status_code < 400:
            try:
                response.close()
            finally:
                raise PortablePredictorSourceEvidenceV1Error(
                    "HTTP redirects are prohibited in source evidence staging."
                )
        response_url = getattr(response, "url", None)
        if isinstance(response_url, str):
            response_parsed = urlparse(response_url)
            if (
                response_parsed.scheme,
                response_parsed.hostname,
                response_parsed.port,
                response_parsed.path.rstrip("/"),
            ) != (
                parsed.scheme,
                parsed.hostname,
                parsed.port,
                parsed.path.rstrip("/"),
            ):
                try:
                    response.close()
                finally:
                    raise PortablePredictorSourceEvidenceV1Error(
                        "HTTP response identity changed without authorization."
                    )
        return response

    def get(self, url: str, **kwargs: object) -> Any:
        return self._request("get", url, **kwargs)

    def post(self, url: str, **kwargs: object) -> Any:
        return self._request("post", url, **kwargs)

    def head(self, url: str, **kwargs: object) -> Any:
        return self._request("head", url, **kwargs)


def _allowed_network_contract(
    config: SourceEvidenceConfig, plan: MulticityPlan
) -> dict[str, set[tuple[str, str]]]:
    raw = config.raw

    def identity(url: str) -> tuple[str, str]:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.query or parsed.fragment:
            raise PortablePredictorSourceEvidenceV1Error(f"Unsafe configured URL: {url}")
        return parsed.hostname.lower(), parsed.path.rstrip("/")

    get_urls = {
        str(raw["daymet"]["cmr_granules_url"]),
        str(raw["nlcd"]["wcs_endpoint"]),
        f"https://www.arcgis.com/sharing/rest/content/items/"
        f"{plan.raw['sources']['census_place_pilot_mirror_item']}",
        f"https://www.arcgis.com/sharing/rest/content/items/"
        f"{plan.raw['sources']['census_tract_pilot_mirror_item']}",
    }
    for role in ("place", "tract"):
        for suffix in ("", "/query"):
            get_urls.add(str(plan.raw["sources"][f"census_{role}_layer"]) + suffix)
            get_urls.add(
                str(plan.raw["sources"][f"census_{role}_pilot_mirror_layer"])
                + suffix
            )
    post_urls = {
        str(raw["landsat"]["api"]).rstrip("/") + "/search",
        str(raw["sentinel"]["api"]).rstrip("/") + "/search",
    }
    terrain_base = str(raw["terrain"]["base_url"]).rstrip("/")
    terrain_ids = {
        (urlparse(terrain_base).hostname or "").lower(),
        urlparse(terrain_base).path.rstrip("/"),
    }
    if not all(isinstance(value, str) for value in terrain_ids):  # pragma: no cover
        raise PortablePredictorSourceEvidenceV1Error("Invalid terrain base URL.")
    # Tile paths are added only after exact geometry-derived IDs are known.
    return {
        "get": {identity(url) for url in get_urls},
        "post": {identity(url) for url in post_urls},
        "head": set(),
    }


def _authorize_tile_url(client: _StrictClient, url: str, *, methods: Sequence[str]) -> None:
    parsed = urlparse(url)
    identity = ((parsed.hostname or "").lower(), parsed.path.rstrip("/"))
    if parsed.scheme != "https" or parsed.query or parsed.fragment:
        raise PortablePredictorSourceEvidenceV1Error(f"Unsafe terrain tile URL: {url}")
    for method in methods:
        client._allowed.setdefault(method, set()).add(identity)


def _parquet_record(
    config: SourceEvidenceConfig,
    path: Path,
    frame: pd.DataFrame | gpd.GeoDataFrame,
    *,
    geometry: bool,
) -> dict[str, Any]:
    record = parquet_file_record(path, frame)
    record["path"] = _relative(config, path)
    if geometry:
        record["geometry_semantic_sha256"] = geometry_semantic_sha256(frame)
    else:
        record["frame_semantic_sha256"] = _non_geometry_frame_sha256(frame)
    return record


def _non_geometry_frame_sha256(
    frame: pd.DataFrame | gpd.GeoDataFrame,
) -> str:
    """Hash the sole non-geometry checkpoint with its frozen identity order."""

    sort_by = ["variable", "year", "concept_id"]
    if not set(sort_by).issubset(frame.columns) or "geometry" in frame.columns:
        raise PortablePredictorSourceEvidenceV1Error(
            "Non-geometry source-footprint table schema changed."
        )
    return canonical_frame_sha256(frame, sort_by=sort_by)


def _commit_parquet_no_clobber(
    path: Path,
    frame: pd.DataFrame | gpd.GeoDataFrame,
    *,
    geometry: bool,
) -> None:
    if path.exists():
        if not path.is_file():
            raise PortablePredictorSourceEvidenceV1Error(f"Parquet path is not a file: {path}")
        observed = gpd.read_parquet(path) if geometry else pd.read_parquet(path)
        expected_sha = (
            geometry_semantic_sha256(frame)
            if geometry
            else _non_geometry_frame_sha256(frame)
        )
        observed_sha = (
            geometry_semantic_sha256(observed)
            if geometry
            else _non_geometry_frame_sha256(observed)
        )
        if expected_sha != observed_sha:
            raise PortablePredictorSourceEvidenceV1Error(
                f"Existing append-only Parquet semantics differ: {path}"
            )
        return
    atomic_parquet(frame, path)


def _geography_manifest_path(workspace: MulticityWorkspace, city_id: str) -> Path:
    return workspace.city(city_id).manifests / "geography" / "GEOGRAPHY.json"


def _verify_new_geography(
    config: SourceEvidenceConfig,
    workspace: MulticityWorkspace,
    city_id: str,
) -> dict[str, Any]:
    path = _geography_manifest_path(workspace, city_id)
    payload, _ = _json_with_commit(path, label=f"{city_id} geography")
    if (
        payload.get("schema_version") != 1
        or payload.get("algorithm_version") != f"{ALGORITHM_VERSION}-geography"
        or payload.get("state") != "complete_target_blind_public_geography"
        or payload.get("city", {}).get("id") != city_id
    ):
        raise PortablePredictorSourceEvidenceV1Error(f"Invalid {city_id} geography state.")
    if payload.get("access_contract") != {
        "public_boundary_and_tract_geometry_read": True,
        "tract_selection_uses_positive_intersection_and_area_fraction": True,
        "demographic_values_read": False,
        "target_or_qa_values_read": False,
        "predictor_values_computed": False,
        "model_or_result_values_read": False,
    }:
        raise PortablePredictorSourceEvidenceV1Error(
            f"{city_id} geography access contract changed."
        )
    for record in payload["raw_files"].values():
        _verify_file_record(config, record)
    for name, record in payload["output_tables"].items():
        table_path = _verify_file_record(
            config,
            {key: record[key] for key in ("path", "bytes", "sha256")},
        )
        frame = gpd.read_parquet(table_path)
        if len(frame) != record["rows"] or geometry_semantic_sha256(frame) != record[
            "geometry_semantic_sha256"
        ]:
            raise PortablePredictorSourceEvidenceV1Error(
                f"{city_id} geography table changed: {name}"
            )
    return payload


def _stage_new_geography(
    config: SourceEvidenceConfig,
    plan: MulticityPlan,
    workspace: MulticityWorkspace,
    city: CitySpec,
    client: _StrictClient,
) -> dict[str, Any]:
    manifest_path = _geography_manifest_path(workspace, city.id)
    if manifest_path.is_file():
        return _verify_new_geography(config, workspace, city.id)
    unavailable_origins: set[str] = set()
    place = _geography._acquire_with_fallback(
        _geography._layer_candidates(plan, role="place"),
        unavailable_origins=unavailable_origins,
        downloader=lambda candidate: _geography._download_place(client, candidate, city),
    )
    boundary = _geography.standardize_place(place.frame, city)
    bbox = tuple(float(value) for value in boundary.total_bounds)
    tracts = _geography._acquire_with_fallback(
        _geography._layer_candidates(plan, role="tract"),
        unavailable_origins=unavailable_origins,
        downloader=lambda candidate: _geography._download_tracts(
            client,
            candidate,
            city,
            bbox,
        ),
    )
    standardized = _geography.standardize_tracts(tracts.frame, city)
    candidates, primary = _geography.select_city_tracts(
        boundary,
        standardized,
        city_id=city.id,
        analysis_crs=str(config.raw["stage"]["analysis_crs"]),
        minimum_place_area_fraction=float(plan.raw["target"]["minimum_place_area_fraction"]),
        exclude_special_use_tracts=bool(plan.raw["target"]["exclude_special_use_tracts"]),
    )
    if not (candidates["place_overlap_area_m2"] > 0).any() or primary.empty:
        raise PortablePredictorSourceEvidenceV1Error(
            f"{city.id} tract selection did not perform positive city intersections."
        )
    city_workspace = workspace.city(city.id)
    raw_root = city_workspace.raw / "geography"
    raw_records: dict[str, dict[str, Any]] = {}
    for role, acquisition in (("place", place), ("tract", tracts)):
        for name, content in acquisition.raw_files.items():
            destination = raw_root / role / name
            _atomic_bytes_no_clobber(content, destination)
            raw_records[f"{role}/{name}"] = _file_record(config, destination)
    processed = city_workspace.processed / "geography"
    frames = {
        "city_boundary": boundary,
        "tract_candidates": candidates,
        "primary_tracts": primary,
    }
    paths = {name: processed / f"{name}.parquet" for name in frames}
    for name, frame in frames.items():
        _commit_parquet_no_clobber(paths[name], frame, geometry=True)
    tables = {
        name: _parquet_record(config, paths[name], gpd.read_parquet(paths[name]), geometry=True)
        for name in frames
    }
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": f"{ALGORITHM_VERSION}-geography",
        "state": "complete_target_blind_public_geography",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "experiment_id": plan.experiment_id,
        "city": {
            "id": city.id,
            "name": city.name,
            "state_fips": city.state_fips,
            "census_place_geoid": city.census_place_geoid,
            "role": city.role,
            "target_values_status": city.target_values_status,
        },
        "sources": {
            "place": _geography._source_manifest(place),
            "tract": _geography._source_manifest(tracts),
        },
        "selection_contract": {
            "analysis_crs": config.raw["stage"]["analysis_crs"],
            "minimum_place_area_fraction": plan.raw["target"][
                "minimum_place_area_fraction"
            ],
            "exclude_special_use_tracts": plan.raw["target"][
                "exclude_special_use_tracts"
            ],
            "candidate_query_uses_place_bbox_then_exact_polygon_intersection": True,
            "bbox_only_tract_selection": False,
        },
        "geography": {
            "bbox_wgs84": [round(value, 10) for value in bbox],
            "tract_candidates_in_bbox": len(candidates),
            "tract_candidates_with_positive_overlap": int(
                (candidates["place_overlap_area_m2"] > 0).sum()
            ),
            "primary_tract_count": len(primary),
        },
        "access_contract": {
            "public_boundary_and_tract_geometry_read": True,
            "tract_selection_uses_positive_intersection_and_area_fraction": True,
            "demographic_values_read": False,
            "target_or_qa_values_read": False,
            "predictor_values_computed": False,
            "model_or_result_values_read": False,
        },
        "raw_files": raw_records,
        "output_tables": tables,
    }
    _write_manifest_no_clobber(payload, manifest_path)
    return _verify_new_geography(config, workspace, city.id)


def _load_geography(
    config: SourceEvidenceConfig,
    plan: MulticityPlan,
    workspace: MulticityWorkspace,
    city_id: str,
) -> dict[str, Any]:
    if city_id == "phoenix_az":
        payload = _geography.verify_city_geography(plan.path, city_id)
        if payload.get("city", {}).get("target_values_status") != "sealed":
            raise PortablePredictorSourceEvidenceV1Error("Phoenix target lock changed.")
        return payload
    return _verify_new_geography(config, workspace, city_id)


def _source_footprint_manifest_path(
    workspace: MulticityWorkspace, city_id: str
) -> Path:
    return (
        workspace.city(city_id).manifests
        / "source_footprints"
        / "SOURCE_FOOTPRINTS.json"
    )


def _verify_new_source_footprint(
    config: SourceEvidenceConfig,
    workspace: MulticityWorkspace,
    city_id: str,
) -> dict[str, Any]:
    path = _source_footprint_manifest_path(workspace, city_id)
    payload, _ = _json_with_commit(path, label=f"{city_id} source footprint")
    if (
        payload.get("schema_version") != 1
        or payload.get("algorithm_version") != f"{ALGORITHM_VERSION}-source-footprints"
        or payload.get("state") != _footprints.COMPLETE_STATE
        or payload.get("city", {}).get("id") != city_id
    ):
        raise PortablePredictorSourceEvidenceV1Error(
            f"Invalid {city_id} source-footprint state."
        )
    if payload.get("access_contract") != _footprints.ACCESS_CONTRACT:
        raise PortablePredictorSourceEvidenceV1Error(
            f"{city_id} source-footprint access contract changed."
        )
    for record in payload["raw_files"].values():
        _verify_file_record(config, record)
    frames: dict[str, pd.DataFrame | gpd.GeoDataFrame] = {}
    for name, record in payload["output_tables"].items():
        table_path = _verify_file_record(
            config,
            {key: record[key] for key in ("path", "bytes", "sha256")},
        )
        geometry = name in _footprints.OUTPUT_GEOMETRY_TABLES
        frame = gpd.read_parquet(table_path) if geometry else pd.read_parquet(table_path)
        semantic_key = (
            "geometry_semantic_sha256" if geometry else "frame_semantic_sha256"
        )
        observed_sha = (
            geometry_semantic_sha256(frame)
            if geometry
            else _non_geometry_frame_sha256(frame)
        )
        if len(frame) != record["rows"] or observed_sha != record[semantic_key]:
            raise PortablePredictorSourceEvidenceV1Error(
                f"{city_id} source-footprint table changed: {name}"
            )
        frames[name] = frame
    replayed = _footprints._family_summaries(
        landsat_items=frames["landsat_items"],
        sentinel_items=frames["sentinel_items"],
        optical_units=frames["optical_units"],
        daymet_granules=frames["daymet_granules"],
        daymet_cells=frames["daymet_cells"],
        daymet_window=payload["source_families"]["daymet_cells"]["window"],
        terrain_tiles=frames["terrain_tiles"],
    )
    if replayed != payload["source_families"]:
        raise PortablePredictorSourceEvidenceV1Error(
            f"{city_id} source-family summary does not replay."
        )
    return payload


def _stage_new_source_footprint(
    config: SourceEvidenceConfig,
    plan: MulticityPlan,
    workspace: MulticityWorkspace,
    city: CitySpec,
    client: _StrictClient,
) -> dict[str, Any]:
    manifest_path = _source_footprint_manifest_path(workspace, city.id)
    if manifest_path.is_file():
        return _verify_new_source_footprint(config, workspace, city.id)
    geography = _load_geography(config, plan, workspace, city.id)
    boundary_record = geography["output_tables"]["city_boundary"]
    boundary_path = config.project_path(str(boundary_record["path"]))
    city_boundary = gpd.read_parquet(boundary_path)
    bbox = tuple(float(value) for value in geography["geography"]["bbox_wgs84"])
    analysis_crs = str(config.raw["stage"]["analysis_crs"])
    dates = config.raw["dates"]

    landsat = config.raw["landsat"]
    landsat_start = date.fromisoformat(str(dates["landsat_local_start"]))
    landsat_end = date.fromisoformat(str(dates["landsat_local_end"]))
    landsat_features, landsat_pages, landsat_query = (
        _footprints.fetch_public_stac_metadata(
            client,
            api=str(landsat["api"]),
            collection=str(landsat["collection"]),
            bbox_wgs84=bbox,
            datetime_interval=_footprints.local_date_interval_to_utc(
                landsat_start, landsat_end, city.timezone
            ),
            fields=_footprints.LANDSAT_FIELDS,
            properties=_footprints.LANDSAT_PROPERTIES,
            page_limit=int(landsat["page_limit"]),
            query={
                "platform": {"in": list(landsat["platforms"])},
                "landsat:collection_category": {
                    "eq": landsat["collection_category"]
                },
                "landsat:correction": {"eq": landsat["correction"]},
            },
        )
    )
    landsat_items = _footprints.build_optical_item_table(
        landsat_features,
        source="landsat_wrs",
        collection=str(landsat["collection"]),
        expected_properties=_footprints.LANDSAT_PROPERTIES,
        allowed_platforms=tuple(landsat["platforms"]),
        local_start_date=landsat_start,
        local_end_date=landsat_end,
        timezone=city.timezone,
        city_boundary=city_boundary,
        analysis_crs=analysis_crs,
    )

    sentinel = config.raw["sentinel"]
    sentinel_start = date.fromisoformat(str(dates["sentinel_local_start"]))
    sentinel_end = date.fromisoformat(str(dates["sentinel_local_end"]))
    sentinel_features, sentinel_pages, sentinel_query = (
        _footprints.fetch_public_stac_metadata(
            client,
            api=str(sentinel["api"]),
            collection=str(sentinel["collection"]),
            bbox_wgs84=bbox,
            datetime_interval=_footprints.local_date_interval_to_utc(
                sentinel_start, sentinel_end, city.timezone
            ),
            fields=_footprints.SENTINEL_FIELDS,
            properties=_footprints.SENTINEL_PROPERTIES,
            page_limit=int(sentinel["page_limit"]),
        )
    )
    sentinel_items = _footprints.build_optical_item_table(
        sentinel_features,
        source="sentinel_mgrs",
        collection=str(sentinel["collection"]),
        expected_properties=_footprints.SENTINEL_PROPERTIES,
        allowed_platforms=tuple(sentinel["platforms"]),
        local_start_date=sentinel_start,
        local_end_date=sentinel_end,
        timezone=city.timezone,
        city_boundary=city_boundary,
        analysis_crs=analysis_crs,
    )
    optical_units = _footprints.build_optical_unit_table(
        (landsat_items, sentinel_items),
        city_boundary=city_boundary,
        analysis_crs=analysis_crs,
    )

    daymet = config.raw["daymet"]
    daymet_granules, daymet_raw, daymet_query = (
        _footprints.fetch_daymet_granule_metadata(
            client,
            endpoint=str(daymet["cmr_granules_url"]),
            collection_concept_id=str(daymet["collection_concept_id"]),
            year=int(daymet["year"]),
            variables=tuple(daymet["variables"]),
            bbox_wgs84=bbox,
        )
    )
    daymet_window = _footprints.derive_daymet_index_window(
        bbox,
        halo_cells=int(daymet["window_halo_cells"]),
    )
    daymet_cells = _footprints.build_daymet_cell_table(
        daymet_window,
        city_boundary=city_boundary,
    )

    terrain = config.raw["terrain"]
    terrain_tiles = _footprints.derive_srtm_tiles(
        city_boundary,
        analysis_crs=analysis_crs,
        halo_m=float(terrain["slope_halo_m"]),
        base_url=str(terrain["base_url"]),
        filename_suffix=str(terrain["filename_suffix"]),
    )
    for url in terrain_tiles["url"].astype(str):
        _authorize_tile_url(client, url, methods=("head", "get"))
    terrain_tiles, terrain_probes = _footprints.probe_terrain_heads(
        client,
        terrain_tiles,
    )

    city_workspace = workspace.city(city.id)
    raw_root = city_workspace.raw / "source_footprints"
    raw_records: dict[str, dict[str, Any]] = {}
    for source, pages in (("landsat", landsat_pages), ("sentinel", sentinel_pages)):
        if len(pages) > int(config.raw["limits"]["maximum_stac_pages_per_query"]):
            raise PortablePredictorSourceEvidenceV1Error("STAC page limit exceeded.")
        for number, page in enumerate(pages, start=1):
            destination = raw_root / source / f"stac_page_{number:03d}.json"
            content = json.dumps(
                page, ensure_ascii=False, sort_keys=True, indent=2
            ).encode("utf-8") + b"\n"
            _atomic_bytes_no_clobber(content, destination)
            raw_records[f"{source}/stac_page_{number:03d}.json"] = _file_record(
                config, destination
            )
    daymet_path = raw_root / "daymet" / "cmr_granules_2025.json"
    daymet_content = json.dumps(
        daymet_raw, ensure_ascii=False, sort_keys=True, indent=2
    ).encode("utf-8") + b"\n"
    _atomic_bytes_no_clobber(daymet_content, daymet_path)
    raw_records["daymet/cmr_granules_2025.json"] = _file_record(config, daymet_path)
    for tile_id, probe in sorted(terrain_probes.items()):
        path = raw_root / "terrain" / f"{tile_id}_head.json"
        content = json.dumps(
            probe, ensure_ascii=False, sort_keys=True, indent=2
        ).encode("utf-8") + b"\n"
        _atomic_bytes_no_clobber(content, path)
        raw_records[f"terrain/{tile_id}_head.json"] = _file_record(config, path)

    frames: dict[str, pd.DataFrame | gpd.GeoDataFrame] = {
        "landsat_items": landsat_items,
        "sentinel_items": sentinel_items,
        "optical_units": optical_units,
        "daymet_granules": daymet_granules,
        "daymet_cells": daymet_cells,
        "terrain_tiles": terrain_tiles,
    }
    processed = city_workspace.processed / "source_footprints"
    paths = {
        name: processed / _footprints.OUTPUT_FILENAMES[name] for name in frames
    }
    for name, frame in frames.items():
        _commit_parquet_no_clobber(
            paths[name],
            frame,
            geometry=name in _footprints.OUTPUT_GEOMETRY_TABLES,
        )
    committed = {
        name: (
            gpd.read_parquet(paths[name])
            if name in _footprints.OUTPUT_GEOMETRY_TABLES
            else pd.read_parquet(paths[name])
        )
        for name in frames
    }
    tables = {
        name: _parquet_record(
            config,
            paths[name],
            committed[name],
            geometry=name in _footprints.OUTPUT_GEOMETRY_TABLES,
        )
        for name in frames
    }
    families = _footprints._family_summaries(
        landsat_items=committed["landsat_items"],
        sentinel_items=committed["sentinel_items"],
        optical_units=committed["optical_units"],
        daymet_granules=committed["daymet_granules"],
        daymet_cells=committed["daymet_cells"],
        daymet_window=daymet_window,
        terrain_tiles=committed["terrain_tiles"],
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": f"{ALGORITHM_VERSION}-source-footprints",
        "state": _footprints.COMPLETE_STATE,
        "stage": _footprints.STAGE_NAME,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "experiment_id": plan.experiment_id,
        "city": {
            "id": city.id,
            "name": city.name,
            "role": city.role,
            "target_values_status": city.target_values_status,
            "timezone": city.timezone,
        },
        "source_lock_status": "evidence_snapshot_not_contract_lock",
        "geography_input": {
            "manifest": _file_record(
                config, _geography_manifest_path(workspace, city.id)
            ),
            "manifest_commit_sha256": geography["commit_sha256"],
            "city_boundary": dict(boundary_record),
            "bbox_wgs84": list(bbox),
        },
        "queries": {
            "landsat": landsat_query,
            "sentinel": sentinel_query,
            "daymet": daymet_query,
            "terrain": {
                "method": "HEAD",
                "object_count": len(terrain_tiles),
                "payload_bytes_read": 0,
            },
        },
        "source_families": families,
        "access_contract": dict(_footprints.ACCESS_CONTRACT),
        "raw_files": raw_records,
        "output_tables": tables,
    }
    _write_manifest_no_clobber(payload, manifest_path)
    return _verify_new_source_footprint(config, workspace, city.id)


def _load_source_footprint(
    config: SourceEvidenceConfig,
    plan: MulticityPlan,
    workspace: MulticityWorkspace,
    city_id: str,
) -> dict[str, Any]:
    if city_id == "phoenix_az":
        return _footprints.verify_city_source_footprints(plan.path, city_id)
    return _verify_new_source_footprint(config, workspace, city_id)


@dataclass(slots=True)
class _DownloadBudget:
    maximum_single_bytes: int
    maximum_total_bytes: int
    downloaded_bytes: int = 0

    def consume(self, amount: int) -> None:
        if amount < 0 or amount > self.maximum_single_bytes:
            raise PortablePredictorSourceEvidenceV1Error(
                "One download exceeded its preregistered byte limit."
            )
        self.downloaded_bytes += amount
        if self.downloaded_bytes > self.maximum_total_bytes:
            raise PortablePredictorSourceEvidenceV1Error(
                "The stage exceeded its preregistered total download limit."
            )


def _aligned_nlcd_bounds(
    city_boundary: gpd.GeoDataFrame,
    *,
    resolution: float,
    edge_offset: float,
    halo_pixels: int,
) -> tuple[float, float, float, float]:
    """Return the smallest haloed native-grid window containing the city."""

    if city_boundary.empty or city_boundary.crs is None:
        raise PortablePredictorSourceEvidenceV1Error("A georeferenced boundary is required.")
    if resolution != 30 or edge_offset != 15 or halo_pixels != 2:
        raise PortablePredictorSourceEvidenceV1Error("NLCD grid contract changed.")
    left, bottom, right, top = city_boundary.to_crs("EPSG:5070").total_bounds
    halo = resolution * halo_pixels

    def lower(value: float) -> float:
        return math.floor((value - halo - edge_offset) / resolution) * resolution + edge_offset

    def upper(value: float) -> float:
        return math.ceil((value + halo - edge_offset) / resolution) * resolution + edge_offset

    bounds = lower(float(left)), lower(float(bottom)), upper(float(right)), upper(float(top))
    if not all(math.isfinite(value) for value in bounds):
        raise PortablePredictorSourceEvidenceV1Error("NLCD bounds are not finite.")
    if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
        raise PortablePredictorSourceEvidenceV1Error("NLCD bounds are empty.")
    return bounds


def _nlcd_query(
    coverage_id: str, bounds: tuple[float, float, float, float]
) -> tuple[tuple[str, str], ...]:
    left, bottom, right, top = bounds
    return (
        ("service", "WCS"),
        ("version", "2.0.1"),
        ("request", "GetCoverage"),
        ("coverageId", coverage_id),
        ("format", "image/tiff"),
        ("subset", f"X({int(left)},{int(right)})"),
        ("subset", f"Y({int(bottom)},{int(top)})"),
    )


def _close(left: float, right: float, *, tolerance: float = 1e-9) -> bool:
    return math.isclose(left, right, rel_tol=0.0, abs_tol=tolerance)


def _inspect_nlcd(
    path: Path,
    *,
    product: str,
    bounds: tuple[float, float, float, float],
) -> dict[str, Any]:
    left, bottom, right, top = bounds
    expected_shape = (int(round((top - bottom) / 30)), int(round((right - left) / 30)))
    expected_transform = Affine(30, 0, left, 0, -30, top)
    try:
        with rasterio.open(path) as dataset:
            if (
                dataset.count != 1
                or dataset.shape != expected_shape
                or dataset.crs is None
                or dataset.crs.to_epsg() != 5070
                or dataset.dtypes != ("uint8",)
                or dataset.nodata != 0
                or tuple(float(value) for value in dataset.scales) != (1.0,)
                or tuple(float(value) for value in dataset.offsets) != (0.0,)
                or dataset.tags().get("AREA_OR_POINT") != "Area"
                or not all(
                    _close(float(actual), float(expected))
                    for actual, expected in zip(
                        tuple(dataset.transform)[:6],
                        tuple(expected_transform)[:6],
                        strict=True,
                    )
                )
            ):
                raise PortablePredictorSourceEvidenceV1Error(
                    f"NLCD {product} raster schema or grid changed."
                )
            values = dataset.read(1)
            raster_unit = dataset.units[0]
    except PortablePredictorSourceEvidenceV1Error:
        raise
    except Exception as exc:
        raise PortablePredictorSourceEvidenceV1Error(
            f"Cannot inspect NLCD {product} raster."
        ) from exc
    unique = {int(value) for value in np.unique(values)}
    if product == "land_cover":
        unexpected = sorted(unique - _LAND_COVER_CODES)
        scientific_nodata = 0
    elif product == "impervious":
        unexpected = sorted(value for value in unique if value > 100 and value != 127)
        scientific_nodata = 127
    else:
        raise PortablePredictorSourceEvidenceV1Error(f"Unknown NLCD product: {product}")
    if unexpected:
        raise PortablePredictorSourceEvidenceV1Error(
            f"NLCD {product} contains invalid codes: {unexpected}"
        )
    return {
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "shape": list(expected_shape),
        "crs": "EPSG:5070",
        "transform": [float(value) for value in tuple(expected_transform)[:6]],
        "dtype": "uint8",
        "scale": 1.0,
        "offset": 0.0,
        "raster_band_unit_metadata": raster_unit,
        "area_or_point": "Area",
        "wcs_tiff_nodata_metadata": 0,
        "scientific_nodata": scientific_nodata,
        "minimum_value": int(values.min()),
        "maximum_value": int(values.max()),
        "unique_value_count": len(unique),
        "value_domain_verified": True,
    }


def _srtm_expected_transform(tile_id: str) -> Affine:
    if (
        len(tile_id) != 7
        or tile_id[0] not in "NS"
        or tile_id[3] not in "EW"
        or not tile_id[1:3].isdigit()
        or not tile_id[4:7].isdigit()
    ):
        raise PortablePredictorSourceEvidenceV1Error(f"Invalid SRTM tile ID: {tile_id}")
    south = int(tile_id[1:3]) * (1 if tile_id[0] == "N" else -1)
    west = int(tile_id[4:7]) * (1 if tile_id[3] == "E" else -1)
    one_arc_second = 1 / 3600
    return Affine(
        one_arc_second,
        0,
        west - one_arc_second / 2,
        0,
        -one_arc_second,
        south + 1 + one_arc_second / 2,
    )


def _inspect_srtm(path: Path, *, tile_id: str) -> dict[str, Any]:
    expected_transform = _srtm_expected_transform(tile_id)
    try:
        with rasterio.open(path) as dataset:
            if (
                dataset.count != 1
                or dataset.shape != (3601, 3601)
                or dataset.crs is None
                or dataset.crs.to_epsg() != 4326
                or dataset.dtypes != ("int16",)
                or dataset.nodata != -32768
                or tuple(float(value) for value in dataset.scales) != (1.0,)
                or tuple(float(value) for value in dataset.offsets) != (0.0,)
                or dataset.tags().get("AREA_OR_POINT") != "Point"
                or not all(
                    _close(float(actual), float(expected), tolerance=1e-12)
                    for actual, expected in zip(
                        tuple(dataset.transform)[:6],
                        tuple(expected_transform)[:6],
                        strict=True,
                    )
                )
            ):
                raise PortablePredictorSourceEvidenceV1Error(
                    f"SRTM raster schema or native point grid changed: {tile_id}"
                )
            raster_unit = dataset.units[0]
    except PortablePredictorSourceEvidenceV1Error:
        raise
    except Exception as exc:
        raise PortablePredictorSourceEvidenceV1Error(
            f"Cannot inspect SRTM raster: {tile_id}"
        ) from exc
    return {
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "shape": [3601, 3601],
        "crs": "EPSG:4326",
        "transform": [float(value) for value in tuple(expected_transform)[:6]],
        "dtype": "int16",
        "nodata": -32768,
        "area_or_point": "Point",
        "scale": 1.0,
        "offset": 0.0,
        "raster_band_unit_metadata": raster_unit,
        "documented_unit": "metre",
        "documented_vertical_datum": "EGM96 orthometric height",
        "unit_and_vertical_datum_source": "pinned_dataset_documentation_not_tiff_claim",
    }


def _selected_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        str(key).lower(): str(value)
        for key, value in headers.items()
        if str(key).lower() in _SELECTED_HEADERS
    }


def _download_file(
    *,
    client: _StrictClient,
    url: str,
    destination: Path,
    params: Sequence[tuple[str, str]] | None,
    timeout_seconds: float,
    budget: _DownloadBudget,
) -> dict[str, Any]:
    if destination.exists():
        if not destination.is_file():
            raise PortablePredictorSourceEvidenceV1Error(
                f"Download destination is not a file: {destination}"
            )
        return {
            "cache_hit_before_city_commit": True,
            "response_headers": {},
            "network_bytes": 0,
        }
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.partial")
    response: _ResponseLike | None = None
    byte_count = 0
    try:
        kwargs: dict[str, object] = {
            "stream": True,
            "timeout": (30.0, timeout_seconds),
        }
        if params is not None:
            kwargs["params"] = params
        response = client.get(url, **kwargs)
        response.raise_for_status()
        headers = _selected_headers(response.headers)
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in {"image/tiff", "image/geotiff", "application/octet-stream"}:
            raise PortablePredictorSourceEvidenceV1Error(
                f"Raster download returned unexpected content type: {content_type!r}"
            )
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                advertised = int(content_length)
            except ValueError as exc:
                raise PortablePredictorSourceEvidenceV1Error(
                    "Raster response Content-Length is invalid."
                ) from exc
            if advertised <= 0 or advertised > budget.maximum_single_bytes:
                raise PortablePredictorSourceEvidenceV1Error(
                    "Raster response exceeds the single-download limit."
                )
        with temporary.open("xb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                if not isinstance(chunk, bytes):
                    raise PortablePredictorSourceEvidenceV1Error(
                        "Raster response yielded a non-bytes chunk."
                    )
                byte_count += len(chunk)
                if byte_count > budget.maximum_single_bytes:
                    raise PortablePredictorSourceEvidenceV1Error(
                        "Raster download exceeded the single-download limit."
                    )
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        if byte_count <= 0 or (
            content_length is not None and byte_count != int(content_length)
        ):
            raise PortablePredictorSourceEvidenceV1Error(
                "Raster response byte count is empty or disagrees with Content-Length."
            )
        budget.consume(byte_count)
        os.replace(temporary, destination)
        return {
            "cache_hit_before_city_commit": False,
            "response_headers": headers,
            "network_bytes": byte_count,
        }
    finally:
        temporary.unlink(missing_ok=True)
        if response is not None:
            response.close()


def _city_evidence_manifest_path(
    config: SourceEvidenceConfig, workspace: MulticityWorkspace, city_id: str
) -> Path:
    return (
        workspace.city(city_id).manifests
        / "source_evidence"
        / str(config.raw["outputs"]["city_evidence_manifest_name"])
    )


def _verify_city_source_evidence(
    config: SourceEvidenceConfig,
    workspace: MulticityWorkspace,
    city_id: str,
) -> dict[str, Any]:
    path = _city_evidence_manifest_path(config, workspace, city_id)
    payload, _ = _json_with_commit(path, label=f"{city_id} static source evidence")
    if (
        payload.get("schema_version") != 1
        or payload.get("algorithm_version") != ALGORITHM_VERSION
        or payload.get("state") != "complete_city_static_source_evidence"
        or payload.get("city_id") != city_id
        or set(payload.get("source_families", {}))
        != {"nlcd_land_cover_and_imperviousness", "terrain_windows"}
    ):
        raise PortablePredictorSourceEvidenceV1Error(
            f"Invalid {city_id} static source evidence."
        )
    for family in payload["source_families"].values():
        for source in family["sources"]:
            source_path = _verify_file_record(config, source["file"])
            if source["content_sha256"] != sha256_file(source_path):
                raise PortablePredictorSourceEvidenceV1Error(
                    f"{city_id} static source content hash changed."
                )
            if source["raster_schema_verified"] is not True:
                raise PortablePredictorSourceEvidenceV1Error(
                    f"{city_id} static raster schema is not verified."
                )
    if payload.get("access_contract") != config.raw["access_contract"]:
        raise PortablePredictorSourceEvidenceV1Error(
            f"{city_id} source-evidence access contract changed."
        )
    return payload


def _stage_city_static_source_evidence(
    config: SourceEvidenceConfig,
    plan: MulticityPlan,
    workspace: MulticityWorkspace,
    city: CitySpec,
    client: _StrictClient,
    *,
    timeout_seconds: float,
    budget: _DownloadBudget,
) -> dict[str, Any]:
    manifest_path = _city_evidence_manifest_path(config, workspace, city.id)
    if manifest_path.is_file():
        return _verify_city_source_evidence(config, workspace, city.id)
    geography = _load_geography(config, plan, workspace, city.id)
    footprint = _load_source_footprint(config, plan, workspace, city.id)
    boundary_record = geography["output_tables"]["city_boundary"]
    boundary_path = config.project_path(str(boundary_record["path"]))
    boundary = gpd.read_parquet(boundary_path)
    nlcd = config.raw["nlcd"]
    bounds = _aligned_nlcd_bounds(
        boundary,
        resolution=float(nlcd["native_resolution_m"]),
        edge_offset=float(nlcd["native_grid_edge_offset_m"]),
        halo_pixels=int(nlcd["subset_halo_pixels"]),
    )
    city_workspace = workspace.city(city.id)
    raw_root = (
        city_workspace.raw / str(config.raw["outputs"]["raw_stage_directory"])
    )
    nlcd_sources: list[dict[str, Any]] = []
    for product, coverage_key in (
        ("land_cover", "land_cover_coverage_id"),
        ("impervious", "impervious_coverage_id"),
    ):
        coverage_id = str(nlcd[coverage_key])
        destination = raw_root / "nlcd" / f"nlcd_2016_{product}.tif"
        query = _nlcd_query(coverage_id, bounds)
        retrieval = _download_file(
            client=client,
            url=str(nlcd["wcs_endpoint"]),
            destination=destination,
            params=query,
            timeout_seconds=timeout_seconds,
            budget=budget,
        )
        schema = _inspect_nlcd(destination, product=product, bounds=bounds)
        nlcd_sources.append(
            {
                "source_id": f"nlcd_2016_{product}_{city.id}",
                "product": product,
                "provider": nlcd["provider"],
                "dataset": nlcd["dataset"],
                "release_date": nlcd["release_date"],
                "doi": nlcd["doi"],
                "license_note": nlcd["license_note"],
                "request_method": "GET",
                "request_url": nlcd["wcs_endpoint"],
                "request_parameters": [list(pair) for pair in query],
                "response": retrieval,
                "file": _file_record(config, destination),
                "content_sha256": schema["sha256"],
                "raster_schema_verified": True,
                "schema": schema,
            }
        )

    terrain_table_record = footprint["output_tables"]["terrain_tiles"]
    terrain_table_path = config.project_path(str(terrain_table_record["path"]))
    terrain_tiles = gpd.read_parquet(terrain_table_path)
    if terrain_tiles.empty or terrain_tiles["tile_id"].duplicated().any():
        raise PortablePredictorSourceEvidenceV1Error(
            f"{city.id} terrain footprint is empty or duplicated."
        )
    terrain_sources: list[dict[str, Any]] = []
    for row in terrain_tiles.sort_values("tile_id").itertuples(index=False):
        tile_id = str(row.tile_id)
        url = str(row.url)
        _authorize_tile_url(client, url, methods=("get",))
        destination = raw_root / "terrain" / f"{tile_id}.tif"
        retrieval = _download_file(
            client=client,
            url=url,
            destination=destination,
            params=None,
            timeout_seconds=timeout_seconds,
            budget=budget,
        )
        schema = _inspect_srtm(destination, tile_id=tile_id)
        terrain_sources.append(
            {
                "source_id": f"srtm_gl1_v3_{tile_id.lower()}",
                "tile_id": tile_id,
                "provider": config.raw["terrain"]["provider"],
                "dataset": config.raw["terrain"]["dataset"],
                "opentopo_id": config.raw["terrain"]["opentopo_id"],
                "dataset_doi": config.raw["terrain"]["dataset_doi"],
                "license_note": config.raw["terrain"]["license_note"],
                "request_method": "GET",
                "request_url": url,
                "response": retrieval,
                "file": _file_record(config, destination),
                "content_sha256": schema["sha256"],
                "raster_schema_verified": True,
                "schema": schema,
            }
        )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": "complete_city_static_source_evidence",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "experiment_id": plan.experiment_id,
        "city_id": city.id,
        "target_values_status": city.target_values_status,
        "inputs": {
            "geography_manifest": _file_record(
                config, _geography_manifest_path(workspace, city.id)
            ),
            "geography_commit_sha256": geography["commit_sha256"],
            "source_footprint_manifest": _file_record(
                config, _source_footprint_manifest_path(workspace, city.id)
            ),
            "source_footprint_commit_sha256": footprint["commit_sha256"],
        },
        "source_families": {
            "nlcd_land_cover_and_imperviousness": {
                "evidence_complete_for_v2_contract_review": True,
                "contract_frozen": False,
                "native_subset_bounds_epsg5070": list(bounds),
                "native_resolution_m": 30,
                "grid_edge_offset_m": 15,
                "subset_halo_pixels": 2,
                "aggregation_or_feature_values_computed": False,
                "sources": nlcd_sources,
            },
            "terrain_windows": {
                "evidence_complete_for_v2_contract_review": True,
                "contract_frozen": False,
                "content_sha256_frozen": True,
                "raster_schema_verified": True,
                "slope_halo_m": config.raw["terrain"]["slope_halo_m"],
                "aggregation_slope_or_feature_values_computed": False,
                "sources": terrain_sources,
            },
        },
        "candidate_downstream_rules_not_executed": dict(
            config.raw["candidate_downstream_rules"]
        ),
        "access_contract": dict(config.raw["access_contract"]),
    }
    _write_manifest_no_clobber(payload, manifest_path)
    return _verify_city_source_evidence(config, workspace, city.id)


def _terminal_path(config: SourceEvidenceConfig) -> Path:
    path = config.project_path(str(config.raw["outputs"]["terminal"]))
    if _relative(config, path) != TERMINAL_PATH:
        raise PortablePredictorSourceEvidenceV1Error("Terminal path changed.")
    return path


def _run_git(
    project_root: Path,
    *arguments: str,
    binary: bool = False,
    accepted_returncodes: tuple[int, ...] = (0,),
) -> str | bytes:
    completed = subprocess.run(
        ["git", "-C", str(project_root), *arguments],
        check=False,
        capture_output=True,
        text=not binary,
    )
    if completed.returncode not in accepted_returncodes:
        stderr = (
            completed.stderr.decode("utf-8", errors="replace")
            if binary
            else completed.stderr
        )
        raise PortablePredictorSourceEvidenceV1Error(
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


def _git_blob(project_root: Path, commit: str, relative: str) -> bytes:
    listing = _run_git(project_root, "ls-tree", commit, "--", relative)
    assert isinstance(listing, str)
    parts = listing.strip().split(maxsplit=3)
    if (
        len(parts) != 4
        or parts[0] not in {"100644", "100755"}
        or parts[1] != "blob"
        or parts[3] != relative
    ):
        raise PortablePredictorSourceEvidenceV1Error(
            f"Published output is not one regular Git blob: {relative}"
        )
    raw = _run_git(project_root, "show", f"{commit}:{relative}", binary=True)
    assert isinstance(raw, bytes)
    return raw


def _authenticate_output_publication(
    config: SourceEvidenceConfig,
    terminal_payload: Mapping[str, Any],
) -> str:
    """Require one direct append-only Git publication of all eight outputs."""

    from la_heat.multicity.plan_source_evidence_hotfix_transition_v10 import (
        _locate_v10_publication_commit,
    )

    head_raw = _run_git(config.project_root, "rev-parse", "HEAD")
    assert isinstance(head_raw, str)
    head = head_raw.strip()
    plan_path = config.project_path(config.raw["stage"]["plan_path"])
    plan_payload, _ = _json_with_commit(plan_path, label="canonical planning v10")
    v10_publication = _locate_v10_publication_commit(
        config.project_root,
        plan_payload,
        current_head=head,
    )
    additions_raw = _run_git(
        config.project_root,
        "log",
        "--all",
        "--diff-filter=A",
        "--format=%H",
        "--",
        TERMINAL_PATH,
    )
    assert isinstance(additions_raw, str)
    additions = [line for line in additions_raw.splitlines() if line]
    if len(additions) != 1:
        raise PortablePredictorSourceEvidenceV1Error(
            "The terminal must have one unique Git addition."
        )
    publication = additions[0]
    ancestry_raw = _run_git(
        config.project_root,
        "rev-list",
        "--parents",
        "-n",
        "1",
        publication,
    )
    assert isinstance(ancestry_raw, str)
    if ancestry_raw.split() != [publication, v10_publication]:
        raise PortablePredictorSourceEvidenceV1Error(
            "The source-evidence publication must be the direct child of canonical v10."
        )
    delta_raw = _run_git(
        config.project_root,
        "diff-tree",
        "--no-commit-id",
        "--name-status",
        "-r",
        "-z",
        "--no-renames",
        v10_publication,
        publication,
        binary=True,
    )
    assert isinstance(delta_raw, bytes)
    fields = delta_raw.split(b"\0")
    if fields[-1:] != [b""]:
        raise PortablePredictorSourceEvidenceV1Error(
            "Publication delta is not valid NUL-delimited Git output."
        )
    fields = fields[:-1]
    if len(fields) % 2:
        raise PortablePredictorSourceEvidenceV1Error(
            "Publication delta contains an incomplete status/path pair."
        )
    pairs: list[tuple[str, str]] = []
    try:
        for index in range(0, len(fields), 2):
            pairs.append((fields[index].decode("ascii"), fields[index + 1].decode("utf-8")))
    except UnicodeDecodeError as exc:
        raise PortablePredictorSourceEvidenceV1Error(
            "Publication delta contains noncanonical text."
        ) from exc
    if len(pairs) != len(TRACKED_OUTPUT_PATHS) or set(pairs) != {
        ("A", path) for path in TRACKED_OUTPUT_PATHS
    }:
        raise PortablePredictorSourceEvidenceV1Error(
            "Publication must add exactly the eight preregistered tracked outputs."
        )
    for relative in TRACKED_OUTPUT_PATHS:
        current_path = config.project_path(relative)
        if _git_blob(config.project_root, publication, relative) != current_path.read_bytes():
            raise PortablePredictorSourceEvidenceV1Error(
                f"Published output bytes differ from current bytes: {relative}"
            )
        additions_for_path = _run_git(
            config.project_root,
            "log",
            "--all",
            "--diff-filter=A",
            "--format=%H",
            "--",
            relative,
        )
        assert isinstance(additions_for_path, str)
        if [line for line in additions_for_path.splitlines() if line] != [publication]:
            raise PortablePredictorSourceEvidenceV1Error(
                f"Tracked output does not have one unique addition: {relative}"
            )
    if not _is_ancestor(config.project_root, publication, head):
        raise PortablePredictorSourceEvidenceV1Error(
            "Source-evidence publication is not an ancestor of current HEAD."
        )
    history = _run_git(
        config.project_root,
        "log",
        "--format=%H",
        f"{publication}..{head}",
        "--",
        *TRACKED_OUTPUT_PATHS,
    )
    assert isinstance(history, str)
    if history.strip():
        raise PortablePredictorSourceEvidenceV1Error(
            "An append-only source-evidence output changed after publication."
        )
    if terminal_payload.get("tracked_output_paths") != list(TRACKED_OUTPUT_PATHS):
        raise PortablePredictorSourceEvidenceV1Error(
            "Published terminal changed its exact tracked-output declaration."
        )
    return publication


def _runtime_record(config: SourceEvidenceConfig) -> dict[str, Any]:
    digest, payload = code_runtime_fingerprint(
        project_root=config.project_root,
        relative_paths=CODE_PATHS,
        algorithm_version=ALGORITHM_VERSION,
    )
    payload["relative_paths"] = list(CODE_PATHS)
    payload["sha256"] = digest
    return payload


def _checkpoint_record(
    config: SourceEvidenceConfig, path: Path, payload: Mapping[str, Any]
) -> dict[str, Any]:
    record = _file_record(config, path)
    record["commit_sha256"] = payload["commit_sha256"]
    record["state"] = payload["state"]
    return record


def _verify_terminal(
    config: SourceEvidenceConfig,
    *,
    publication_authenticator: Any | None = None,
    plan_authorization_override: Mapping[str, Any] | None = None,
    require_publication: bool = True,
) -> dict[str, Any]:
    plan_authorization = (
        dict(plan_authorization_override)
        if plan_authorization_override is not None
        else _authenticate_plan(
            config,
            publication_authenticator=publication_authenticator,
        )
    )
    path = _terminal_path(config)
    payload, _ = _json_with_commit(path, label="source-evidence V1 terminal")
    if (
        payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("algorithm_version") != ALGORITHM_VERSION
        or payload.get("state") != COMPLETE_STATE
        or payload.get("stage_id") != STAGE_ID
        or payload.get("experiment_id") != config.raw["stage"]["experiment_id"]
        or payload.get("plan_authorization") != plan_authorization
        or payload.get("authorization_scope") != expected_plan_authorization_scope()
        or payload.get("tracked_output_paths") != list(TRACKED_OUTPUT_PATHS)
        or payload.get("terminal_written_last") is not True
    ):
        raise PortablePredictorSourceEvidenceV1Error("Terminal identity changed.")
    if payload.get("config") != {
        "path": CONFIG_PATH,
        "bytes": config.path.stat().st_size,
        "sha256": sha256_file(config.path),
        "semantic_sha256": config.semantic_sha256,
    }:
        raise PortablePredictorSourceEvidenceV1Error("Terminal config binding changed.")
    if payload.get("access_contract") != config.raw["access_contract"]:
        raise PortablePredictorSourceEvidenceV1Error("Terminal access contract changed.")
    checkpoints = payload.get("tracked_checkpoints")
    if not isinstance(checkpoints, dict) or set(checkpoints) != set(TRACKED_OUTPUT_PATHS[:-1]):
        raise PortablePredictorSourceEvidenceV1Error(
            "Terminal checkpoint set is not the exact preregistered tracked output set."
        )
    for relative, record in checkpoints.items():
        if record.get("path") != relative:
            raise PortablePredictorSourceEvidenceV1Error(
                "Terminal checkpoint path and key disagree."
            )
        checkpoint_path = _verify_file_record(
            config,
            {key: record[key] for key in ("path", "bytes", "sha256")},
        )
        checkpoint, _ = _json_with_commit(checkpoint_path, label=relative)
        if (
            checkpoint.get("commit_sha256") != record.get("commit_sha256")
            or checkpoint.get("state") != record.get("state")
        ):
            raise PortablePredictorSourceEvidenceV1Error(
                f"Terminal checkpoint identity changed: {relative}"
            )
    if payload.get("code_runtime") != _runtime_record(config):
        raise PortablePredictorSourceEvidenceV1Error("Source-evidence runtime changed.")

    plan = load_multicity_plan(config.project_path(config.raw["stage"]["experiment_config"]))
    workspace = MulticityWorkspace.from_plan(plan)
    _geography.verify_city_geography(plan.path, "phoenix_az")
    _footprints.verify_city_source_footprints(plan.path, "phoenix_az")
    for city_id in ("houston_tx", "chicago_il"):
        _verify_new_geography(config, workspace, city_id)
        _verify_new_source_footprint(config, workspace, city_id)
    for city_id in ("phoenix_az", "houston_tx", "chicago_il"):
        _verify_city_source_evidence(config, workspace, city_id)
    result = deepcopy(payload)
    if require_publication:
        result["publication_status"] = "authenticated_git_publication"
        result["publication_git_commit"] = _authenticate_output_publication(
            config,
            payload,
        )
    else:
        result["publication_status"] = "awaiting_git_publication"
    return result


def _terminal_is_tracked_at_head(config: SourceEvidenceConfig) -> bool:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(config.project_root),
            "cat-file",
            "-e",
            f"HEAD:{TERMINAL_PATH}",
        ],
        check=False,
        capture_output=True,
    )
    return completed.returncode == 0


def stage_portable_predictor_source_evidence_v1(
    config_path: str | Path = CONFIG_PATH,
    *,
    check_only: bool = False,
    client: Any | None = None,
    timeout_seconds: float = 240.0,
    publication_authenticator: Any | None = None,
) -> dict[str, Any]:
    """Stage or authenticate all preregistered evidence checkpoints.

    ``check_only`` opens only tracked manifests/configuration, the already
    authenticated public-source files named by them, and local Git/runtime
    metadata.  It constructs no HTTP client and makes zero network requests.
    """

    config = _read_config(config_path)
    if timeout_seconds <= 0 or timeout_seconds > 900:
        raise PortablePredictorSourceEvidenceV1Error(
            "Timeout must be in the preregistered safe range (0, 900]."
        )
    terminal_path = _terminal_path(config)
    if check_only:
        return _verify_terminal(
            config,
            publication_authenticator=publication_authenticator,
        )
    if terminal_path.is_file():
        if _terminal_is_tracked_at_head(config):
            return _verify_terminal(
                config,
                publication_authenticator=publication_authenticator,
            )
        existing_outputs = tuple(
            relative
            for relative in TRACKED_OUTPUT_PATHS
            if config.project_path(relative).exists()
        )
        plan_authorization = _authenticate_plan_for_resume(
            config,
            allowed_untracked_paths=existing_outputs,
        )
        terminal_payload, _ = _json_with_commit(
            terminal_path,
            label="unpublished source-evidence V1 terminal",
        )
        recorded_plan = terminal_payload.get("plan_authorization")
        if not isinstance(recorded_plan, dict):
            raise PortablePredictorSourceEvidenceV1Error(
                "Unpublished terminal lacks its pre-write plan authentication."
            )
        if recorded_plan != plan_authorization:
            raise PortablePredictorSourceEvidenceV1Error(
                "Unpublished terminal plan authentication no longer replays."
            )
        return _verify_terminal(
            config,
            plan_authorization_override=plan_authorization,
            require_publication=False,
        )

    partial_outputs = tuple(
        relative
        for relative in TRACKED_OUTPUT_PATHS[:-1]
        if config.project_path(relative).exists()
    )
    if partial_outputs:
        plan_authorization = _authenticate_plan_for_resume(
            config,
            allowed_untracked_paths=partial_outputs,
        )
    else:
        plan_authorization = _authenticate_plan(
            config,
            publication_authenticator=publication_authenticator,
        )
    plan = load_multicity_plan(config.project_path(config.raw["stage"]["experiment_config"]))
    if plan.experiment_id != config.raw["stage"]["experiment_id"]:
        raise PortablePredictorSourceEvidenceV1Error("Experiment identity changed.")
    workspace = MulticityWorkspace.from_plan(plan)
    session = _footprints._retrying_session() if client is None else client
    strict_client = _StrictClient(
        session,
        allowed=_allowed_network_contract(config, plan),
        maximum_requests=int(config.raw["limits"]["maximum_network_requests"]),
    )
    budget = _DownloadBudget(
        maximum_single_bytes=int(
            config.raw["limits"]["maximum_single_download_bytes"]
        ),
        maximum_total_bytes=int(
            config.raw["limits"]["maximum_total_download_bytes"]
        ),
    )

    city_payloads: dict[str, dict[str, Any]] = {}
    for city_id in config.raw["stage"]["new_geography_city_ids"]:
        city = _city(plan, str(city_id))
        _stage_new_geography(config, plan, workspace, city, strict_client)
    for city_id in config.raw["stage"]["new_source_footprint_city_ids"]:
        city = _city(plan, str(city_id))
        _stage_new_source_footprint(config, plan, workspace, city, strict_client)
    for city_id in config.raw["stage"]["authorized_city_ids"]:
        city = _city(plan, str(city_id))
        city_payloads[city.id] = _stage_city_static_source_evidence(
            config,
            plan,
            workspace,
            city,
            strict_client,
            timeout_seconds=timeout_seconds,
            budget=budget,
        )

    checkpoints: dict[str, dict[str, Any]] = {}
    for city_id in ("houston_tx", "chicago_il"):
        path = _geography_manifest_path(workspace, city_id)
        payload = _verify_new_geography(config, workspace, city_id)
        checkpoints[_relative(config, path)] = _checkpoint_record(config, path, payload)
    for city_id in ("houston_tx", "chicago_il"):
        path = _source_footprint_manifest_path(workspace, city_id)
        payload = _verify_new_source_footprint(config, workspace, city_id)
        checkpoints[_relative(config, path)] = _checkpoint_record(config, path, payload)
    for city_id in ("phoenix_az", "houston_tx", "chicago_il"):
        path = _city_evidence_manifest_path(config, workspace, city_id)
        checkpoints[_relative(config, path)] = _checkpoint_record(
            config,
            path,
            city_payloads[city_id],
        )
    if set(checkpoints) != set(TRACKED_OUTPUT_PATHS[:-1]):
        raise PortablePredictorSourceEvidenceV1Error(
            "The staged tracked output set differs from the preregistration."
        )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": COMPLETE_STATE,
        "stage_id": STAGE_ID,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "experiment_id": plan.experiment_id,
        "plan_authorization": plan_authorization,
        "authorization_scope": expected_plan_authorization_scope(),
        "config": {
            "path": CONFIG_PATH,
            "bytes": config.path.stat().st_size,
            "sha256": sha256_file(config.path),
            "semantic_sha256": config.semantic_sha256,
        },
        "cities": {
            city_id: {
                "source_evidence_commit_sha256": city_payloads[city_id][
                    "commit_sha256"
                ],
                "nlcd_content_and_schema_evidence_complete": True,
                "terrain_content_and_schema_evidence_complete": True,
                "portable_predictor_contract_frozen": False,
                "predictor_values_computed": False,
            }
            for city_id in ("phoenix_az", "houston_tx", "chicago_il")
        },
        "tracked_output_paths": list(TRACKED_OUTPUT_PATHS),
        "tracked_checkpoints": checkpoints,
        "tracked_output_set_exact": True,
        "terminal_written_last": True,
        "network_audit": {
            "request_count": strict_client.request_count,
            "downloaded_payload_bytes": budget.downloaded_bytes,
            "request_limit": config.raw["limits"]["maximum_network_requests"],
            "single_download_byte_limit": config.raw["limits"][
                "maximum_single_download_bytes"
            ],
            "total_download_byte_limit": config.raw["limits"][
                "maximum_total_download_bytes"
            ],
            "redirects_followed": 0,
        },
        "access_contract": dict(config.raw["access_contract"]),
        "locks": {
            "portable_predictor_contract_frozen": False,
            "portable_water_distance_feature_names_frozen": False,
            "predictor_build_authorized": False,
            "protocol_lock_created": False,
            "external_targets_unlocked": False,
            "external_target_values_read": False,
            "external_prediction_commit_exists": False,
        },
        "next_gate": {
            "stage_id": "separate_portable_predictor_contract_freeze_v2_decision",
            "tracked_only_transition_required_before_v2": True,
            "predictor_construction_remains_closed": True,
            "model_target_protocol_and_operational_claims_remain_closed": True,
        },
        "code_runtime": _runtime_record(config),
    }
    _write_manifest_no_clobber(payload, terminal_path)
    return _verify_terminal(
        config,
        plan_authorization_override=plan_authorization,
        require_publication=False,
    )
