"""Target-blind review of portable shoreline-distance sources and algorithms.

The review authenticates an already present public coastline vector and records
the scientific source decision that still remains.  It never computes a
distance surface, constructs a predictor, opens an external target, or makes a
network request.
"""

from __future__ import annotations

import json
import math
import tomllib
from pathlib import Path
from typing import Any, Final

import geopandas as gpd

from la_heat.multicity.config import load_multicity_plan
from la_heat.provenance import (
    atomic_json,
    canonical_sha256,
    code_runtime_fingerprint,
    geometry_semantic_sha256,
    sha256_file,
)
from la_heat.static_sources import CENSUS_2019_COASTLINE, validate_source_file

SCHEMA_VERSION: Final = 1
ALGORITHM_VERSION: Final = "portable-water-distance-review-v1"
COMPLETE_STATE: Final = "review_complete_source_not_frozen"
DEFAULT_CONFIG: Final = Path("configs/multicity/water_distance_review_v1.toml")
DEFAULT_MANIFEST: Final = Path(
    "manifests/multicity/reviews/portable_water_distance/"
    "WATER_DISTANCE_REVIEW.json"
)

EXPECTED_CANDIDATE_IDS: Final = (
    "census_tiger_line_2019",
    "gshhg_2_3_7_full",
    "natural_earth_10m",
    "noaa_cusp",
    "usgs_3dhp",
)
EXPECTED_ACCESS_CONTRACT: Final = {
    "audit_program_network_requests": 0,
    "official_documentation_web_review_performed": True,
    "candidate_data_download_requests": 0,
    "source_geometry_read": True,
    "distance_values_computed": False,
    "predictor_construction_performed": False,
    "model_fit_performed": False,
    "model_predictions_computed": False,
    "landsat_thermal_values_read": False,
    "landsat_target_qa_values_read": False,
    "external_lst_values_read": False,
    "external_target_files_opened": False,
}
CODE_PATHS: Final = (
    "configs/multicity/experiment.toml",
    "configs/multicity/water_distance_review_v1.toml",
    "scripts/audit_multicity_water_distance_review.py",
    "src/la_heat/multicity/config.py",
    "src/la_heat/multicity/water_distance_review.py",
    "src/la_heat/provenance.py",
    "src/la_heat/static_sources.py",
)


class WaterDistanceReviewError(ValueError):
    """Raised when the target-blind review contract does not authenticate."""


def _require_exact_keys(
    value: dict[str, Any],
    expected: set[str],
    *,
    label: str,
) -> None:
    observed = set(value)
    if observed != expected:
        raise WaterDistanceReviewError(
            f"{label} keys changed; missing={sorted(expected - observed)}, "
            f"unexpected={sorted(observed - expected)}."
        )


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


def _read_review_config(path: str | Path) -> tuple[Path, dict[str, Any]]:
    config_path = Path(path).resolve()
    if not config_path.is_file():
        raise FileNotFoundError(config_path)
    with config_path.open("rb") as handle:
        payload = tomllib.load(handle)
    if not isinstance(payload, dict):
        raise WaterDistanceReviewError("Review configuration must be a TOML table.")
    _require_exact_keys(
        payload,
        {
            "review",
            "semantic_decision",
            "census_benchmark",
            "algorithm_recommendation",
            "next_geometry_pilot",
            "access_contract",
            "candidates",
        },
        label="review configuration",
    )

    review = payload["review"]
    if not isinstance(review, dict):
        raise WaterDistanceReviewError("review must be a table.")
    _require_exact_keys(
        review,
        {
            "schema_version",
            "algorithm_version",
            "review_id",
            "status",
            "review_date",
            "scope",
            "outcome",
        },
        label="review",
    )
    expected_review = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "review_id": "portable_water_distance_source_review_v1",
        "status": COMPLETE_STATE,
        "review_date": "2026-07-29",
        "scope": "target-blind source and algorithm review",
        "outcome": "conditional_census_benchmark_pending_global_geometry_pilot",
    }
    if not _strict_equal(review, expected_review):
        raise WaterDistanceReviewError("Review identity or non-frozen status changed.")

    semantic = payload["semantic_decision"]
    if not isinstance(semantic, dict):
        raise WaterDistanceReviewError("semantic_decision must be a table.")
    _require_exact_keys(
        semantic,
        {
            "scientifically_preferred_definition",
            "census_fallback_definition",
            "census_fallback_interpretation",
            "cross_border_ocean_should_count",
            "source_scope_is_resolved",
            "resolution_blocker",
        },
        label="semantic_decision",
    )
    if semantic["cross_border_ocean_should_count"] is not True:
        raise WaterDistanceReviewError("Cross-border ocean membership changed.")
    if semantic["source_scope_is_resolved"] is not False:
        raise WaterDistanceReviewError("The review may not silently resolve the source scope.")

    census = payload["census_benchmark"]
    if not isinstance(census, dict):
        raise WaterDistanceReviewError("census_benchmark must be a table.")
    _require_exact_keys(
        census,
        {
            "source_id",
            "path",
            "layer",
            "publisher",
            "dataset",
            "official_url",
            "technical_documentation",
            "expected_sha256",
            "expected_bytes",
            "expected_crs",
            "filter_field",
            "filter_value",
            "expected_row_count",
            "expected_geometry_semantic_sha256",
            "expected_bounds",
            "expected_name_counts",
        },
        label="census_benchmark",
    )
    exact_census = {
        "source_id": CENSUS_2019_COASTLINE.source_id,
        "path": f"data/raw/static/{CENSUS_2019_COASTLINE.filename}",
        "layer": "tl_2019_us_coastline.shp",
        "publisher": CENSUS_2019_COASTLINE.publisher,
        "dataset": "TIGER/Line 2019 national coastline",
        "official_url": CENSUS_2019_COASTLINE.urls[0],
        "technical_documentation": (
            "https://www2.census.gov/geo/pdfs/maps-data/data/tiger/"
            "tgrshp2019/TGRSHP2019_TechDoc.pdf"
        ),
        "expected_sha256": CENSUS_2019_COASTLINE.expected_sha256,
        "expected_bytes": CENSUS_2019_COASTLINE.expected_bytes,
        "expected_crs": "EPSG:4269",
        "filter_field": "MTFCC",
        "filter_value": "L4150",
        "expected_row_count": 4248,
        "expected_geometry_semantic_sha256": (
            "7a57e6388d3e702ab2ac6fdee4afb019e"
            "e4ee093b5f14984ca235364f6b21f71"
        ),
        "expected_bounds": [-179.147236, -14.548699, 179.77847, 71.39038],
        "expected_name_counts": {
            "Pacific": 1895,
            "Atlantic": 998,
            "Gulf": 668,
            "Great Lakes": 377,
            "Caribe": 128,
            "Caribbean": 74,
            "Atlántico": 69,
            "Arctic": 39,
        },
    }
    if not _strict_equal(census, exact_census):
        raise WaterDistanceReviewError("Census benchmark identity or audit contract changed.")

    algorithm = payload["algorithm_recommendation"]
    if not isinstance(algorithm, dict):
        raise WaterDistanceReviewError("algorithm_recommendation must be a table.")
    _require_exact_keys(
        algorithm,
        {
            "status",
            "source_filter_rule",
            "census_name_field_role",
            "support_rule",
            "city_crs_rule",
            "distance_rule",
            "search_rule",
            "search_radii_km",
            "aggregation_rule",
            "proposed_global_feature_names",
            "proposed_census_fallback_feature_names",
            "forbid_phase1_alias",
            "phase1_outputs_immutable",
        },
        label="algorithm_recommendation",
    )
    if algorithm["status"] != "reviewed_not_implemented_or_frozen":
        raise WaterDistanceReviewError("The reviewed algorithm may not claim implementation.")
    if (
        algorithm["census_name_field_role"]
        != "audit-only inventory; never select or exclude a line by NAME"
    ):
        raise WaterDistanceReviewError("Census NAME may be used only for source audit.")
    if algorithm["search_radii_km"] != [64, 128, 256, 512, 1024, 2048]:
        raise WaterDistanceReviewError("The deterministic search-radius ladder changed.")
    if algorithm["forbid_phase1_alias"] is not True:
        raise WaterDistanceReviewError("Phase I feature aliasing must remain forbidden.")
    if algorithm["phase1_outputs_immutable"] is not True:
        raise WaterDistanceReviewError("Phase I outputs must remain immutable.")

    pilot = payload["next_geometry_pilot"]
    if not isinstance(pilot, dict):
        raise WaterDistanceReviewError("next_geometry_pilot must be a table.")
    _require_exact_keys(
        pilot,
        {
            "candidate_id",
            "source_values_only",
            "target_or_qa_access_allowed",
            "predictor_construction_allowed",
            "required_global_ocean_level",
            "required_named_great_lake_count",
            "exclude_river_lake_flag",
            "require_level1_polygon_to_shoreline_rule",
            "require_antimeridian_normalization_rule",
            "compare_against_census_at_fixed_unlabeled_points",
            "require_search_radius_invariance",
            "require_strtree_bruteforce_parity",
            "require_projected_geodesic_tolerance_audit",
        },
        label="next_geometry_pilot",
    )
    expected_pilot = {
        "candidate_id": "gshhg_2_3_7_full",
        "source_values_only": True,
        "target_or_qa_access_allowed": False,
        "predictor_construction_allowed": False,
        "required_global_ocean_level": 1,
        "required_named_great_lake_count": 5,
        "exclude_river_lake_flag": True,
        "require_level1_polygon_to_shoreline_rule": True,
        "require_antimeridian_normalization_rule": True,
        "compare_against_census_at_fixed_unlabeled_points": True,
        "require_search_radius_invariance": True,
        "require_strtree_bruteforce_parity": True,
        "require_projected_geodesic_tolerance_audit": True,
    }
    if not _strict_equal(pilot, expected_pilot):
        raise WaterDistanceReviewError("The target-blind geometry-pilot gate changed.")

    access = payload["access_contract"]
    if not isinstance(access, dict) or not _strict_equal(
        access, EXPECTED_ACCESS_CONTRACT
    ):
        raise WaterDistanceReviewError("The no-target/no-predictor access contract changed.")

    candidates = payload["candidates"]
    if not isinstance(candidates, list) or any(
        not isinstance(candidate, dict) for candidate in candidates
    ):
        raise WaterDistanceReviewError("candidates must be an array of tables.")
    candidate_keys = {
        "id",
        "scope",
        "versioning",
        "resolution_or_scale",
        "license",
        "decision",
        "reason",
        "official_reference",
    }
    for candidate in candidates:
        _require_exact_keys(candidate, candidate_keys, label="candidate")
        if not str(candidate["official_reference"]).startswith("https://"):
            raise WaterDistanceReviewError("Every candidate needs an official HTTPS reference.")
    if tuple(candidate["id"] for candidate in candidates) != EXPECTED_CANDIDATE_IDS:
        raise WaterDistanceReviewError("Candidate identity or order changed.")
    return config_path, payload


def _require_closed_locks(plan_raw: dict[str, Any]) -> dict[str, bool]:
    locks = plan_raw["locks"]
    required_false = {
        "protocol_locked": locks["protocol_locked"],
        "external_targets_unlocked": locks["external_targets_unlocked"],
        "external_target_values_read": locks["external_target_values_read"],
        "external_prediction_commit_exists": locks[
            "external_prediction_commit_exists"
        ],
        "allow_predictor_construction": locks["allow_predictor_construction"],
        "allow_model_fitting": locks["allow_model_fitting"],
        "allow_external_target_access": locks["allow_external_target_access"],
    }
    if any(value is not False for value in required_false.values()):
        raise WaterDistanceReviewError(
            "Portable-water review requires every computation and target lock closed."
        )
    return {key: False for key in required_false}


def _audit_census_benchmark(
    *,
    project_root: Path,
    settings: dict[str, Any],
) -> dict[str, Any]:
    path = project_root / str(settings["path"])
    validation = validate_source_file(CENSUS_2019_COASTLINE, path)
    archive_uri = f"zip://{path.resolve()}!{settings['layer']}"
    try:
        frame = gpd.read_file(archive_uri)
    except Exception as exc:
        raise WaterDistanceReviewError("Cannot read the audited Census vector.") from exc

    if set(frame.columns) != {"NAME", "MTFCC", "geometry"}:
        raise WaterDistanceReviewError("Census coastline schema changed.")
    if len(frame) != int(settings["expected_row_count"]):
        raise WaterDistanceReviewError("Census coastline row count changed.")
    if frame.crs is None or frame.crs.to_string() != settings["expected_crs"]:
        raise WaterDistanceReviewError("Census coastline CRS changed.")
    if not frame["MTFCC"].astype(str).eq(settings["filter_value"]).all():
        raise WaterDistanceReviewError("Census coastline contains a non-L4150 row.")
    name_counts = {
        str(name): int(count)
        for name, count in frame["NAME"].astype(str).value_counts().items()
    }
    if name_counts != settings["expected_name_counts"]:
        raise WaterDistanceReviewError("Census coastline NAME inventory changed.")
    if (
        frame.geometry.isna().any()
        or frame.geometry.is_empty.any()
        or not frame.geometry.is_valid.all()
    ):
        raise WaterDistanceReviewError("Census coastline has missing, empty, or invalid geometry.")
    geometry_types = {
        str(name): int(count)
        for name, count in frame.geom_type.value_counts().items()
    }
    if geometry_types != {"LineString": int(settings["expected_row_count"])}:
        raise WaterDistanceReviewError("Census coastline geometry type changed.")
    bounds = [float(value) for value in frame.total_bounds]
    if any(
        not math.isclose(observed, float(expected), abs_tol=1e-9, rel_tol=0.0)
        for observed, expected in zip(bounds, settings["expected_bounds"], strict=True)
    ):
        raise WaterDistanceReviewError("Census coastline bounds changed.")
    semantic_hash = geometry_semantic_sha256(frame)
    if semantic_hash != settings["expected_geometry_semantic_sha256"]:
        raise WaterDistanceReviewError("Census coastline geometry semantics changed.")

    return {
        "source_id": settings["source_id"],
        "path": settings["path"],
        "layer": settings["layer"],
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "validation": validation,
        "crs": frame.crs.to_string(),
        "columns": list(frame.columns),
        "row_count": len(frame),
        "mtfcc_values": ["L4150"],
        "name_counts": name_counts,
        "bounds": bounds,
        "geometry_types": geometry_types,
        "all_geometry_nonempty_and_valid": True,
        "geometry_semantic_sha256": semantic_hash,
        "distance_values_computed": False,
    }


def _build_review_payload(
    config_path: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    project_root = config_path.parents[2]
    plan = load_multicity_plan(project_root / "configs/multicity/experiment.toml")
    locks = _require_closed_locks(plan.raw)
    source_audit = _audit_census_benchmark(
        project_root=project_root,
        settings=config["census_benchmark"],
    )
    code_sha256, code_payload = code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=CODE_PATHS,
        algorithm_version=ALGORITHM_VERSION,
    )
    code_payload["relative_paths"] = list(CODE_PATHS)
    code_payload["sha256"] = code_sha256

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "state": COMPLETE_STATE,
        "review_id": config["review"]["review_id"],
        "review_date": config["review"]["review_date"],
        "review_scope": config["review"]["scope"],
        "review_outcome": config["review"]["outcome"],
        "experiment_id": plan.experiment_id,
        "plan_semantic_sha256": plan.semantic_sha256,
        "source_lock_created": False,
        "algorithm_lock_created": False,
        "predictor_build_authorized": False,
        "semantic_decision": config["semantic_decision"],
        "census_benchmark": {
            key: value
            for key, value in config["census_benchmark"].items()
            if not key.startswith("expected_")
        },
        "candidate_assessments": config["candidates"],
        "algorithm_recommendation": config["algorithm_recommendation"],
        "next_geometry_pilot": config["next_geometry_pilot"],
        "locks": locks,
        "access_contract": config["access_contract"],
        "source_audit": source_audit,
        "review_config": {
            "path": config_path.relative_to(project_root).as_posix(),
            "sha256": sha256_file(config_path),
            "bytes": config_path.stat().st_size,
        },
        "code_runtime": code_payload,
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    return payload


def audit_water_distance_review(
    config_path: str | Path = DEFAULT_CONFIG,
    *,
    output_path: str | Path = DEFAULT_MANIFEST,
    write: bool = True,
) -> dict[str, Any]:
    """Create or reauthenticate the deterministic, target-blind review record."""

    resolved_config, config = _read_review_config(config_path)
    project_root = resolved_config.parents[2]
    payload = _build_review_payload(resolved_config, config)
    destination = Path(output_path)
    if not destination.is_absolute():
        destination = project_root / destination
    if write:
        atomic_json(payload, destination)
        return payload

    if not destination.is_file():
        raise FileNotFoundError(destination)
    try:
        committed = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WaterDistanceReviewError("Cannot read review manifest.") from exc
    if not isinstance(committed, dict):
        raise WaterDistanceReviewError("Review manifest must be a JSON object.")
    recorded = committed.get("commit_sha256")
    body = {key: value for key, value in committed.items() if key != "commit_sha256"}
    if not isinstance(recorded, str) or recorded != canonical_sha256(body):
        raise WaterDistanceReviewError("Review manifest internal commit is invalid.")
    if not _strict_equal(committed, payload):
        raise WaterDistanceReviewError("Review manifest is stale or changed.")
    return committed
