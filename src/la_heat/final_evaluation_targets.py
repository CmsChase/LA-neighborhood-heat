"""Fail-closed construction of the one-time 2025 Landsat target table.

The inventory-authentication phase is target blind: it reads only frozen
metadata, tract geometry, and the predeclared tract-date key universe.  The
transaction phase requires an explicit callback immediately before the first
target/QA value access.  That boundary covers both remote Landsat assets and
same-claim recovery caches.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol
from urllib.parse import urlsplit

import geopandas as gpd
import numpy as np
import pandas as pd

from la_heat.aligned_landsat import (
    REQUIRED_ASSETS,
    AlignedScene,
    read_aligned_scene_from_hrefs,
)
from la_heat.config import ResearchConfig, load_config
from la_heat.final_test_inventory import (
    ALGORITHM_VERSION as INVENTORY_ALGORITHM_VERSION,
)
from la_heat.final_test_inventory import (
    KEY_UNIVERSE_FILENAME,
    OVERPASS_FILENAME,
    PRIMARY_FILENAME,
    SCENE_FILENAME,
    build_target_blind_key_universe,
)
from la_heat.final_test_inventory import (
    SCHEMA_VERSION as INVENTORY_SCHEMA_VERSION,
)
from la_heat.grid import FixedGrid
from la_heat.guardrails import (
    validate_static_eligible_denominator,
    validate_target_qa_contract,
    validate_unique_primary_key,
)
from la_heat.inventory import OVERPASS_CSV_DTYPES, SCENE_CSV_DTYPES
from la_heat.landsat import zonal_mask_identity_hashes
from la_heat.mosaic import mosaic_aligned_scenes
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
from la_heat.stage_config import target_config_payload, target_config_sha256
from la_heat.target_aggregation import TargetAggregationResult, aggregate_target_mosaic
from la_heat.target_builder import (
    _canonical_tract_manifest_hash,
    _fixed_grid_and_zones,
)
from la_heat.targets import assign_relative_endpoints

FINAL_TEST_YEAR: Final = 2025
FINAL_TARGET_ALGORITHM_VERSION: Final = "final-evaluation-targets-v1-claim-bound"
FINAL_TARGET_PIPELINE_FILES: Final = (
    "pyproject.toml",
    "src/la_heat/aligned_landsat.py",
    "src/la_heat/config.py",
    "src/la_heat/final_evaluation_targets.py",
    "src/la_heat/final_test_inventory.py",
    "src/la_heat/grid.py",
    "src/la_heat/guardrails.py",
    "src/la_heat/inventory.py",
    "src/la_heat/landmask.py",
    "src/la_heat/landsat.py",
    "src/la_heat/mosaic.py",
    "src/la_heat/provenance.py",
    "src/la_heat/stage_config.py",
    "src/la_heat/target_aggregation.py",
    "src/la_heat/target_builder.py",
    "src/la_heat/targets.py",
)
INVENTORY_OUTPUT_FILES: Final = (
    SCENE_FILENAME,
    OVERPASS_FILENAME,
    PRIMARY_FILENAME,
    KEY_UNIVERSE_FILENAME,
)
CACHE_OUTPUT_FILES: Final = (
    "tract_date_qa.parquet",
    "date_summary.parquet",
    "scene_contributions.parquet",
)
TARGET_BUILD_LOCK_FILENAME: Final = "TARGET_BUILD_LOCK.json"
CACHE_COMMIT_FILENAME: Final = "CACHE_COMMIT.json"

FINAL_TARGET_COLUMNS: Final = (
    "tract_geoid",
    "target_date",
    "overpass_id",
    "platform",
    "source_scene_count",
    "source_scene_ids",
    "spatial_block",
    "latitude_quartile",
    "longitude_quartile",
    "rasterized_pixel_count",
    "footprint_pixel_count",
    "eligible_pixel_count_static",
    "eligible_pixel_identity_sha256",
    "valid_pixel_count",
    "footprint_fraction",
    "valid_fraction",
    "target_lst_c",
    "mean_lst_c",
    "std_lst_c",
    "median_st_uncertainty_k",
    "median_cloud_distance_km",
    "p10_lst_c",
    "p90_lst_c",
    "p90_st_uncertainty_k",
    "tract_exclusion_reason",
    "target_available",
    "lst_anomaly_c",
    "relative_hotspot_top20",
    "date_usable",
    "date_exclusion_reason",
    "config_sha256",
    "tract_manifest_sha256",
    "grid_sha256",
)
FINAL_DATE_SUMMARY_COLUMNS: Final = (
    "target_date",
    "overpass_id",
    "platform",
    "scene_ids",
    "scene_count",
    "union_city_coverage_fraction",
    "tract_count",
    "retained_tract_count",
    "retained_tract_fraction",
    "date_usable",
    "date_exclusion_reason",
    "relative_endpoint_coverage_pass",
    "minimum_eligible_joint_cell_retention_fraction",
    "relative_hotspot_count",
    "median_target_lst_c",
    "p05_target_lst_c",
    "p95_target_lst_c",
    "grid_sha256",
    "zone_raster_sha256",
    "eligible_mask_sha256",
    "config_sha256",
    "tract_manifest_sha256",
)
FINAL_SCENE_CONTRIBUTION_COLUMNS: Final = (
    "target_date",
    "overpass_id",
    "scene_id",
    "selected_valid_pixel_count",
    "tract_geoid",
)
KEY_UNIVERSE_COLUMNS: Final = (
    "tract_geoid",
    "target_date",
    "overpass_id",
    "platform",
    "spatial_block",
    "latitude_quartile",
    "longitude_quartile",
)


class FinalEvaluationTargetError(RuntimeError):
    """Raised when the final target transaction cannot prove its locks."""


class SceneReader(Protocol):
    """Injected scene reader used to make the value-opening boundary testable."""

    def __call__(
        self,
        *,
        scene_id: str,
        asset_hrefs: dict[str, str],
        grid: FixedGrid,
        config: ResearchConfig,
    ) -> AlignedScene: ...


@dataclass(frozen=True, slots=True)
class AuthenticatedFinalInventory:
    """Frozen target-blind metadata and support authenticated in memory."""

    project_root: Path
    inventory_path: Path
    inventory_file_sha256: str
    inventory_commit_sha256: str
    city: gpd.GeoDataFrame
    tracts: gpd.GeoDataFrame
    scenes: pd.DataFrame
    primary_overpasses: pd.DataFrame
    key_universe: pd.DataFrame
    locks: dict[str, str]

    @property
    def readiness_record(self) -> dict[str, object]:
        """Return a JSON-safe target-blind record for evaluator readiness."""

        dates = _normalized_dates(
            self.primary_overpasses["local_date"],
            label="Authenticated final primary overpasses",
        )
        return {
            "schema_version": 1,
            "state": "authenticated_target_blind_final_landsat_inventory",
            "final_test_year": FINAL_TEST_YEAR,
            "inventory_path": str(self.inventory_path),
            "inventory_file_sha256": self.inventory_file_sha256,
            "inventory_commit_sha256": self.inventory_commit_sha256,
            "scene_count": int(len(self.scenes)),
            "physical_overpass_count": int(len(self.primary_overpasses)),
            "tract_count": int(len(self.tracts)),
            "key_count": int(len(self.key_universe)),
            "tract_crs": self.tracts.crs.to_string(),
            "target_dates": [value.date().isoformat() for value in dates],
            "target_blind": True,
            "target_assets_opened": False,
            "target_or_qa_values_read": False,
            "locks": dict(sorted(self.locks.items())),
        }


@dataclass(frozen=True, slots=True)
class FinalTargetArtifacts:
    """In-memory final target outputs after the value boundary has opened."""

    target_qa: pd.DataFrame
    date_summary: pd.DataFrame
    scene_contributions: pd.DataFrame
    audit: dict[str, object]


@dataclass(slots=True)
class _ValuesAccessGate:
    callback: Callable[[], None]
    opened: bool = False

    def before_first_value_access(self) -> None:
        if self.opened:
            return
        self.callback()
        self.opened = True


def final_target_pipeline_fingerprint(
    project_root: str | Path | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return the exact code/runtime identity for final target construction."""

    root = (
        Path(__file__).resolve().parents[2]
        if project_root is None
        else Path(project_root).resolve()
    )
    return code_runtime_fingerprint(
        project_root=root,
        relative_paths=FINAL_TARGET_PIPELINE_FILES,
        algorithm_version=FINAL_TARGET_ALGORITHM_VERSION,
    )


def _read_stable_json(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise FinalEvaluationTargetError(f"{label} does not exist: {path}")
    before = sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FinalEvaluationTargetError(f"{label} is not valid JSON: {path}") from error
    if sha256_file(path) != before:
        raise FinalEvaluationTargetError(f"{label} changed while being read: {path}")
    if not isinstance(payload, dict):
        raise FinalEvaluationTargetError(f"{label} must be a JSON object: {path}")
    return payload


def _verify_canonical_commit(payload: dict[str, Any], *, label: str) -> str:
    working = dict(payload)
    recorded = working.pop("commit_sha256", None)
    if not isinstance(recorded, str) or canonical_sha256(working) != recorded:
        raise FinalEvaluationTargetError(f"{label} canonical commit is invalid.")
    return recorded


def _path_within_root(value: object, *, root: Path, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise FinalEvaluationTargetError(f"{label} path is absent.")
    path = Path(value).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise FinalEvaluationTargetError(
            f"{label} path escapes the authenticated project root: {path}"
        ) from error
    return path


def _verify_locked_file(
    path: Path,
    *,
    expected_sha256: object,
    expected_bytes: object,
    label: str,
) -> None:
    if not path.is_file():
        raise FinalEvaluationTargetError(f"{label} file is absent: {path}")
    if not isinstance(expected_bytes, int) or path.stat().st_size != expected_bytes:
        raise FinalEvaluationTargetError(f"{label} byte count failed its lock.")
    if not isinstance(expected_sha256, str) or sha256_file(path) != expected_sha256:
        raise FinalEvaluationTargetError(f"{label} SHA-256 failed its lock.")


def _verify_exact_columns(
    frame: pd.DataFrame,
    expected: tuple[str, ...],
    *,
    label: str,
) -> None:
    if tuple(frame.columns) != expected:
        raise FinalEvaluationTargetError(
            f"{label} schema drifted; expected {list(expected)}, "
            f"received {list(frame.columns)}."
        )


def _normalized_dates(series: pd.Series, *, label: str) -> pd.Series:
    try:
        values = pd.to_datetime(series, errors="raise")
    except (TypeError, ValueError) as error:
        raise FinalEvaluationTargetError(f"{label} contains an invalid date.") from error
    if getattr(values.dt, "tz", None) is not None:
        values = values.dt.tz_convert(None)
    return values.dt.normalize()


def _verify_inventory_state(
    payload: dict[str, Any],
    *,
    expected_scene_count: int,
    expected_overpass_count: int,
    expected_tract_count: int,
    expected_key_count: int,
) -> None:
    exact = {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "algorithm_version": INVENTORY_ALGORITHM_VERSION,
        "state": "target_blind_inventory_frozen",
        "final_test_year": FINAL_TEST_YEAR,
        "target_blind": True,
        "target_assets_opened": False,
        "target_or_qa_values_read": False,
        "labels_created": False,
        "models_loaded": False,
        "model_scores_read": False,
        "one_time_evaluation_consumed": False,
        "global_scene_cloud_cover_filter": False,
        "scene_count": expected_scene_count,
        "physical_overpass_count": expected_overpass_count,
        "primary_overpass_count": expected_overpass_count,
        "tract_count": expected_tract_count,
        "key_count": expected_key_count,
    }
    mismatches = {
        key: {"expected": value, "received": payload.get(key)}
        for key, value in exact.items()
        if payload.get(key) != value
    }
    if mismatches:
        raise FinalEvaluationTargetError(
            f"Final Landsat inventory state/count lock failed: {mismatches}"
        )


def _authenticate_inventory_outputs(
    payload: dict[str, Any],
    *,
    inventory_directory: Path,
    root: Path,
) -> dict[str, Path]:
    output_records = payload.get("output_files")
    if not isinstance(output_records, dict) or set(output_records) != set(
        INVENTORY_OUTPUT_FILES
    ):
        raise FinalEvaluationTargetError(
            "Final Landsat inventory does not lock the exact four metadata outputs."
        )
    paths: dict[str, Path] = {}
    for filename in INVENTORY_OUTPUT_FILES:
        record = output_records[filename]
        if not isinstance(record, dict):
            raise FinalEvaluationTargetError(
                f"Final Landsat inventory record is invalid: {filename}"
            )
        path = _path_within_root(
            record.get("path"),
            root=root,
            label=f"Final Landsat inventory {filename}",
        )
        if path != (inventory_directory / filename).resolve():
            raise FinalEvaluationTargetError(
                f"Final Landsat inventory path is not canonical: {filename}"
            )
        _verify_locked_file(
            path,
            expected_sha256=record.get("sha256"),
            expected_bytes=record.get("bytes"),
            label=f"Final Landsat inventory {filename}",
        )
        paths[filename] = path
    return paths


def _authenticate_support(
    payload: dict[str, Any],
    *,
    root: Path,
    expected_tract_count: int,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, dict[str, str]]:
    support = payload.get("frozen_support")
    if not isinstance(support, dict):
        raise FinalEvaluationTargetError("Final Landsat inventory lacks frozen support.")
    if support.get("tract_count") != expected_tract_count:
        raise FinalEvaluationTargetError("Frozen support tract count failed its lock.")

    city_path = _path_within_root(
        support.get("city_boundary_path"),
        root=root,
        label="Frozen city boundary",
    )
    tract_path = _path_within_root(
        support.get("primary_tract_path"),
        root=root,
        label="Frozen primary tract manifest",
    )
    _verify_locked_file(
        city_path,
        expected_sha256=support.get("city_boundary_sha256"),
        expected_bytes=city_path.stat().st_size if city_path.is_file() else None,
        label="Frozen city boundary",
    )
    _verify_locked_file(
        tract_path,
        expected_sha256=support.get("primary_tract_sha256"),
        expected_bytes=tract_path.stat().st_size if tract_path.is_file() else None,
        label="Frozen primary tract manifest",
    )
    try:
        city = gpd.read_file(city_path)
        tracts = gpd.read_parquet(tract_path)
    except Exception as error:
        raise FinalEvaluationTargetError("Frozen target support cannot be read.") from error
    if sha256_file(city_path) != support.get("city_boundary_sha256"):
        raise FinalEvaluationTargetError("Frozen city boundary changed while being read.")
    if sha256_file(tract_path) != support.get("primary_tract_sha256"):
        raise FinalEvaluationTargetError(
            "Frozen primary tract manifest changed while being read."
        )
    try:
        city_semantic = geometry_semantic_sha256(city)
        tract_semantic = _canonical_tract_manifest_hash(tracts)
    except (TypeError, ValueError) as error:
        raise FinalEvaluationTargetError("Frozen target support schema is invalid.") from error
    if city_semantic != support.get("city_boundary_geometry_sha256"):
        raise FinalEvaluationTargetError(
            "Frozen city boundary semantic geometry lock failed."
        )
    if tract_semantic != support.get("primary_tract_commit_sha256"):
        raise FinalEvaluationTargetError(
            "Frozen primary tract semantic commit failed."
        )
    if len(tracts) != expected_tract_count:
        raise FinalEvaluationTargetError("Frozen primary tract cardinality drifted.")
    required = {
        "GEOID",
        "primary_included",
        "spatial_block",
        "latitude_quartile",
        "longitude_quartile",
    }
    if required - set(tracts):
        raise FinalEvaluationTargetError("Frozen primary tract schema is incomplete.")
    geoids = tracts["GEOID"].astype("string")
    if (
        geoids.isna().any()
        or geoids.duplicated().any()
        or not geoids.str.fullmatch(r"\d{11}").all()
        or not tracts["primary_included"].eq(True).all()  # noqa: E712
    ):
        raise FinalEvaluationTargetError(
            "Frozen primary tract GEOIDs or inclusion flags are invalid."
        )
    tracts = tracts.copy()
    tracts["GEOID"] = geoids
    if "tract_manifest_sha256" in tracts:
        recorded = tracts["tract_manifest_sha256"].drop_duplicates().tolist()
        if recorded != [tract_semantic]:
            raise FinalEvaluationTargetError(
                "Frozen primary tract embedded semantic lock drifted."
            )
    locks = {
        "city_boundary_file_sha256": str(support["city_boundary_sha256"]),
        "city_boundary_geometry_sha256": city_semantic,
        "primary_tract_file_sha256": str(support["primary_tract_sha256"]),
        "tract_manifest_sha256": tract_semantic,
    }
    return city, tracts, locks


def _authenticate_metadata_tables(
    *,
    payload: dict[str, Any],
    paths: dict[str, Path],
    tracts: gpd.GeoDataFrame,
    expected_scene_count: int,
    expected_overpass_count: int,
    expected_key_count: int,
    expected_key_semantic_sha256: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    try:
        scenes = pd.read_csv(
            paths[SCENE_FILENAME],
            dtype=SCENE_CSV_DTYPES,
            float_precision="round_trip",
        )
        overpasses = pd.read_csv(
            paths[OVERPASS_FILENAME],
            dtype=OVERPASS_CSV_DTYPES,
            float_precision="round_trip",
        )
        primary = pd.read_csv(
            paths[PRIMARY_FILENAME],
            dtype=OVERPASS_CSV_DTYPES,
            float_precision="round_trip",
        )
        keys = pd.read_parquet(paths[KEY_UNIVERSE_FILENAME])
    except Exception as error:
        raise FinalEvaluationTargetError(
            "Frozen final Landsat metadata outputs cannot be read."
        ) from error
    for filename, frame in (
        (SCENE_FILENAME, scenes),
        (OVERPASS_FILENAME, overpasses),
        (PRIMARY_FILENAME, primary),
        (KEY_UNIVERSE_FILENAME, keys),
    ):
        record = payload["output_files"][filename]
        if sha256_file(paths[filename]) != record["sha256"]:
            raise FinalEvaluationTargetError(
                f"Final Landsat metadata changed while being read: {filename}"
            )
        if len(frame) != record.get("rows"):
            raise FinalEvaluationTargetError(
                f"Final Landsat metadata row count drifted: {filename}"
            )
    key_schema = canonical_sha256(
        [(column, str(dtype)) for column, dtype in keys.dtypes.items()]
    )
    if key_schema != payload["output_files"][KEY_UNIVERSE_FILENAME].get(
        "schema_sha256"
    ):
        raise FinalEvaluationTargetError("Final Landsat key-universe schema lock failed.")

    semantics = payload.get("semantic_hashes")
    if not isinstance(semantics, dict):
        raise FinalEvaluationTargetError(
            "Final Landsat inventory lacks semantic table locks."
        )
    observed_semantics = {
        "scenes": canonical_frame_sha256(scenes, sort_by=["item_id"]),
        "overpasses": canonical_frame_sha256(
            overpasses, sort_by=["local_date", "overpass_id"]
        ),
        "primary_overpasses": canonical_frame_sha256(
            primary, sort_by=["local_date", "overpass_id"]
        ),
        "key_universe": canonical_frame_sha256(
            keys, sort_by=["target_date", "tract_geoid"]
        ),
    }
    if observed_semantics != semantics:
        raise FinalEvaluationTargetError(
            "Final Landsat inventory semantic table lock failed."
        )
    if observed_semantics["key_universe"] != expected_key_semantic_sha256:
        raise FinalEvaluationTargetError(
            "Final Landsat key universe differs from the evaluation lock."
        )

    if len(scenes) != expected_scene_count or scenes["item_id"].duplicated().any():
        raise FinalEvaluationTargetError("Final Landsat scene cardinality drifted.")
    scene_asset_columns = {f"{asset}_href" for asset in REQUIRED_ASSETS}
    if (
        {"item_id", "platform", "local_date"} | scene_asset_columns
    ) - set(scenes):
        raise FinalEvaluationTargetError("Final Landsat scene schema is incomplete.")
    for column in scene_asset_columns:
        for href in scenes[column].astype("string"):
            parsed = urlsplit(str(href))
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.query
                or parsed.fragment
            ):
                raise FinalEvaluationTargetError(
                    "Final Landsat inventory contains a noncanonical asset URL."
                )

    if len(overpasses) != expected_overpass_count or len(primary) != expected_overpass_count:
        raise FinalEvaluationTargetError("Final Landsat overpass cardinality drifted.")
    if not primary["primary_eligible"].eq(True).all():  # noqa: E712
        raise FinalEvaluationTargetError(
            "Final Landsat primary manifest contains an ineligible overpass."
        )
    dates = _normalized_dates(primary["local_date"], label="Primary overpass manifest")
    if (
        dates.duplicated().any()
        or not dates.dt.year.eq(FINAL_TEST_YEAR).all()
        or primary["overpass_id"].duplicated().any()
    ):
        raise FinalEvaluationTargetError(
            "Final Landsat primary overpasses are not unique 2025 dates."
        )
    locked_scene_ids = set(scenes["item_id"].astype("string"))
    scene_dates = _normalized_dates(scenes["local_date"], label="Final scene inventory")
    if not scene_dates.dt.year.eq(FINAL_TEST_YEAR).all():
        raise FinalEvaluationTargetError(
            "Final Landsat scene inventory contains a non-2025 scene."
        )
    scene_lookup = scenes.set_index("item_id", drop=False)
    for row in primary.itertuples(index=False):
        scene_ids = tuple(str(row.scene_ids).split("|"))
        if (
            not scene_ids
            or len(scene_ids) != int(row.scene_count)
            or not set(scene_ids).issubset(locked_scene_ids)
            or not isinstance(row.source_lock_sha256, str)
            or len(row.source_lock_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in row.source_lock_sha256.lower()
            )
        ):
            raise FinalEvaluationTargetError(
                f"Final overpass source lock is invalid: {row.overpass_id}"
            )
        selected_scenes = scene_lookup.loc[list(scene_ids)]
        if isinstance(selected_scenes, pd.Series):
            selected_scenes = selected_scenes.to_frame().T
        if (
            not selected_scenes["local_date"].eq(str(row.local_date)).all()
            or not selected_scenes["platform"].eq(str(row.platform)).all()
        ):
            raise FinalEvaluationTargetError(
                f"Final overpass scene date/platform identity drifted: {row.overpass_id}"
            )

    _verify_exact_columns(keys, KEY_UNIVERSE_COLUMNS, label="Final key universe")
    keys = keys.copy()
    keys["tract_geoid"] = keys["tract_geoid"].astype("string")
    keys["target_date"] = _normalized_dates(
        keys["target_date"], label="Final key universe"
    )
    if (
        len(keys) != expected_key_count
        or keys.duplicated(["tract_geoid", "target_date"]).any()
        or not keys["target_date"].dt.year.eq(FINAL_TEST_YEAR).all()
    ):
        raise FinalEvaluationTargetError("Final Landsat key cardinality drifted.")
    expected_keys = build_target_blind_key_universe(tracts, primary)
    expected_keys["tract_geoid"] = expected_keys["tract_geoid"].astype("string")
    expected_keys["target_date"] = _normalized_dates(
        expected_keys["target_date"], label="Reconstructed final key universe"
    )
    expected_keys = expected_keys.loc[:, list(KEY_UNIVERSE_COLUMNS)]
    expected_hash = canonical_frame_sha256(
        expected_keys, sort_by=["target_date", "tract_geoid"]
    )
    if expected_hash != observed_semantics["key_universe"]:
        raise FinalEvaluationTargetError(
            "Final Landsat key universe is not the exact tract-date cross product."
        )
    return scenes, primary, keys


def authenticate_final_landsat_inventory(
    inventory_path: str | Path,
    *,
    expected_inventory_file_sha256: str,
    expected_inventory_commit_sha256: str,
    expected_key_semantic_sha256: str,
    expected_scene_count: int,
    expected_overpass_count: int,
    expected_tract_count: int,
    expected_key_count: int,
    project_root: str | Path | None = None,
) -> AuthenticatedFinalInventory:
    """Authenticate frozen 2025 metadata/support without opening target assets."""

    root = (
        Path(__file__).resolve().parents[2]
        if project_root is None
        else Path(project_root).resolve()
    )
    path = Path(inventory_path)
    path = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise FinalEvaluationTargetError(
            "Final Landsat inventory path escapes the project root."
        ) from error
    if expected_key_count != expected_overpass_count * expected_tract_count:
        raise FinalEvaluationTargetError(
            "Expected final key count is not dates multiplied by tracts."
        )
    if any(
        count <= 0
        for count in (
            expected_scene_count,
            expected_overpass_count,
            expected_tract_count,
            expected_key_count,
        )
    ):
        raise FinalEvaluationTargetError("Expected final cardinalities must be positive.")
    if not path.is_file() or sha256_file(path) != expected_inventory_file_sha256:
        raise FinalEvaluationTargetError(
            "Final Landsat inventory file SHA-256 failed its evaluation lock."
        )
    payload = _read_stable_json(path, label="Final Landsat inventory")
    commit = _verify_canonical_commit(payload, label="Final Landsat inventory")
    if commit != expected_inventory_commit_sha256:
        raise FinalEvaluationTargetError(
            "Final Landsat inventory canonical commit failed its evaluation lock."
        )
    _verify_inventory_state(
        payload,
        expected_scene_count=expected_scene_count,
        expected_overpass_count=expected_overpass_count,
        expected_tract_count=expected_tract_count,
        expected_key_count=expected_key_count,
    )
    output_paths = _authenticate_inventory_outputs(
        payload,
        inventory_directory=path.parent,
        root=root,
    )
    city, tracts, support_locks = _authenticate_support(
        payload,
        root=root,
        expected_tract_count=expected_tract_count,
    )
    scenes, primary, keys = _authenticate_metadata_tables(
        payload=payload,
        paths=output_paths,
        tracts=tracts,
        expected_scene_count=expected_scene_count,
        expected_overpass_count=expected_overpass_count,
        expected_key_count=expected_key_count,
        expected_key_semantic_sha256=expected_key_semantic_sha256,
    )
    locks = {
        "inventory_file_sha256": expected_inventory_file_sha256,
        "inventory_commit_sha256": commit,
        "key_universe_semantic_sha256": expected_key_semantic_sha256,
        "scene_inventory_semantic_sha256": str(payload["semantic_hashes"]["scenes"]),
        "primary_overpass_semantic_sha256": str(
            payload["semantic_hashes"]["primary_overpasses"]
        ),
        **support_locks,
    }
    return AuthenticatedFinalInventory(
        project_root=root,
        inventory_path=path,
        inventory_file_sha256=expected_inventory_file_sha256,
        inventory_commit_sha256=commit,
        city=city,
        tracts=tracts,
        scenes=scenes,
        primary_overpasses=primary,
        key_universe=keys,
        locks=locks,
    )


def _cache_lock(base_lock: dict[str, str], row: Any) -> dict[str, str]:
    return {
        **base_lock,
        "overpass_id": str(row.overpass_id),
        "overpass_source_sha256": str(row.source_lock_sha256),
    }


def _committed_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(result)
    return result


def _write_exclusive_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags)
    except FileExistsError:
        raise
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _bind_target_build_directory(
    target_directory: Path,
    *,
    build_lock: dict[str, Any],
) -> None:
    lock_path = target_directory / TARGET_BUILD_LOCK_FILENAME
    committed = _committed_payload(build_lock)
    if lock_path.exists():
        observed = _read_stable_json(lock_path, label="Final target build lock")
        _verify_canonical_commit(observed, label="Final target build lock")
        if observed != committed:
            raise FinalEvaluationTargetError(
                "Final target staging directory is bound to a different claim or lock."
            )
        return
    if target_directory.exists():
        unexpected = [
            path.name
            for path in target_directory.iterdir()
            if path.name != TARGET_BUILD_LOCK_FILENAME
        ]
        if unexpected:
            raise FinalEvaluationTargetError(
                "Final target staging directory contains uncommitted artifacts."
            )
    try:
        _write_exclusive_json(lock_path, committed)
    except FileExistsError:
        observed = _read_stable_json(lock_path, label="Final target build lock")
        if observed != committed:
            raise FinalEvaluationTargetError(
                "Concurrent final target build claimed a different lock."
            ) from None


def _cache_is_current(
    cache_directory: Path,
    *,
    expected_lock: dict[str, str],
    gate: _ValuesAccessGate,
) -> bool:
    commit_path = cache_directory / CACHE_COMMIT_FILENAME
    if not commit_path.exists():
        return False
    gate.before_first_value_access()
    payload = _read_stable_json(commit_path, label="Final target cache commit")
    _verify_canonical_commit(payload, label="Final target cache commit")
    if (
        payload.get("state") != "complete"
        or payload.get("cache_lock") != expected_lock
    ):
        raise FinalEvaluationTargetError(
            "Final target cache is bound to a different or invalid lock."
        )
    outputs = payload.get("output_files")
    if not isinstance(outputs, dict) or set(outputs) != set(CACHE_OUTPUT_FILES):
        raise FinalEvaluationTargetError("Final target cache output contract drifted.")
    for filename in CACHE_OUTPUT_FILES:
        record = outputs[filename]
        path = cache_directory / filename
        if (
            not isinstance(record, dict)
            or not path.is_file()
            or path.stat().st_size != record.get("bytes")
            or sha256_file(path) != record.get("sha256")
        ):
            raise FinalEvaluationTargetError(
                "Final target cache content failed its committed file lock."
            )
    return True


def _process_overpass(
    row: Any,
    *,
    inventory: AuthenticatedFinalInventory,
    config: ResearchConfig,
    grid: FixedGrid,
    zones: np.ndarray,
    static_land: np.ndarray,
    grid_identity: str,
    base_cache_lock: dict[str, str],
    target_directory: Path,
    gate: _ValuesAccessGate,
    scene_reader: SceneReader,
) -> None:
    cache_directory = target_directory / "by_overpass" / str(row.overpass_id)
    expected_lock = _cache_lock(base_cache_lock, row)
    if _cache_is_current(
        cache_directory,
        expected_lock=expected_lock,
        gate=gate,
    ):
        return
    cache_directory.mkdir(parents=True, exist_ok=True)
    (cache_directory / CACHE_COMMIT_FILENAME).unlink(missing_ok=True)
    scene_ids = tuple(str(row.scene_ids).split("|"))
    scene_lookup = inventory.scenes.set_index("item_id", drop=False)
    aligned: list[AlignedScene] = []
    for scene_id in scene_ids:
        if scene_id not in scene_lookup.index:
            raise FinalEvaluationTargetError(
                f"Locked final overpass references an unknown scene: {scene_id}"
            )
        scene = scene_lookup.loc[scene_id]
        if isinstance(scene, pd.DataFrame):
            raise FinalEvaluationTargetError(
                f"Locked final scene ID is duplicated: {scene_id}"
            )
        asset_hrefs = {
            asset: str(scene[f"{asset}_href"]) for asset in REQUIRED_ASSETS
        }
        gate.before_first_value_access()
        observed = scene_reader(
            scene_id=scene_id,
            asset_hrefs=asset_hrefs,
            grid=grid,
            config=config,
        )
        if observed.scene_id != scene_id:
            raise FinalEvaluationTargetError(
                "Final scene reader returned a different scene identity."
            )
        aligned.append(observed)
    if not aligned:
        raise FinalEvaluationTargetError("Final overpass contains no aligned scenes.")
    mosaic = mosaic_aligned_scenes(
        scene_ids=[scene.scene_id for scene in aligned],
        st_values=np.stack([scene.lst_c for scene in aligned]),
        qa_valid=np.stack([scene.valid for scene in aligned]),
        st_qa=np.stack([scene.st_uncertainty_k for scene in aligned]),
        cdist=np.stack([scene.cloud_distance_km for scene in aligned]),
        footprint=np.stack([scene.footprint for scene in aligned]),
    )
    aggregated: TargetAggregationResult = aggregate_target_mosaic(
        tracts=inventory.tracts,
        zone_raster=zones,
        static_land_mask=static_land,
        mosaic=mosaic,
        target_date=str(row.local_date),
        overpass_id=str(row.overpass_id),
        platform=str(row.platform),
        scene_ids=scene_ids,
        union_city_coverage_fraction=float(row.union_city_coverage_fraction),
        grid_identity=grid_identity,
        config_sha256=base_cache_lock["target_config_sha256"],
        tract_manifest_sha256=base_cache_lock["tract_manifest_sha256"],
        config=config,
    )
    targets = aggregated.tract_date_qa
    date_summary = pd.DataFrame([aggregated.summary])
    contributions = aggregated.scene_contributions
    _verify_exact_columns(targets, FINAL_TARGET_COLUMNS, label="Final target cache")
    _verify_exact_columns(
        date_summary,
        FINAL_DATE_SUMMARY_COLUMNS,
        label="Final date-summary cache",
    )
    _verify_exact_columns(
        contributions,
        FINAL_SCENE_CONTRIBUTION_COLUMNS,
        label="Final scene-contribution cache",
    )
    frames = {
        CACHE_OUTPUT_FILES[0]: targets,
        CACHE_OUTPUT_FILES[1]: date_summary,
        CACHE_OUTPUT_FILES[2]: contributions,
    }
    output_records: dict[str, dict[str, Any]] = {}
    for filename, frame in frames.items():
        path = cache_directory / filename
        atomic_parquet(frame, path)
        output_records[filename] = parquet_file_record(path, frame)
    commit = _committed_payload(
        {
            "schema_version": 1,
            "algorithm_version": FINAL_TARGET_ALGORITHM_VERSION,
            "state": "complete",
            "cache_lock": expected_lock,
            "output_files": output_records,
        }
    )
    atomic_json(commit, cache_directory / CACHE_COMMIT_FILENAME)


def _as_scene_id_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(value.split("|"))
    if isinstance(value, (list, tuple, np.ndarray, pd.Series)):
        return tuple(str(item) for item in value)
    raise FinalEvaluationTargetError("Final date summary contains invalid scene IDs.")


def _audit_key_alignment(
    targets: pd.DataFrame,
    *,
    inventory: AuthenticatedFinalInventory,
) -> None:
    observed = targets.loc[:, list(KEY_UNIVERSE_COLUMNS)].copy()
    observed["tract_geoid"] = observed["tract_geoid"].astype("string")
    observed["target_date"] = _normalized_dates(
        observed["target_date"], label="Final target table"
    )
    expected = inventory.key_universe.loc[:, list(KEY_UNIVERSE_COLUMNS)].copy()
    expected["tract_geoid"] = expected["tract_geoid"].astype("string")
    expected["target_date"] = _normalized_dates(
        expected["target_date"], label="Frozen final key universe"
    )
    observed_hash = canonical_frame_sha256(
        observed, sort_by=["target_date", "tract_geoid"]
    )
    expected_hash = canonical_frame_sha256(
        expected, sort_by=["target_date", "tract_geoid"]
    )
    if observed_hash != expected_hash:
        raise FinalEvaluationTargetError(
            "Final target table does not match the exact frozen tract-date key universe."
        )


def _strict_boolean_column(frame: pd.DataFrame, column: str) -> np.ndarray:
    values = frame[column]
    if values.isna().any() or not values.map(
        lambda value: isinstance(value, (bool, np.bool_))
    ).all():
        raise FinalEvaluationTargetError(
            f"Final target boolean field is not strict: {column}"
        )
    return values.to_numpy(dtype=bool)


def _strict_nonnegative_integer_column(
    frame: pd.DataFrame,
    column: str,
) -> np.ndarray:
    values = frame[column]
    if pd.api.types.is_bool_dtype(values.dtype):
        raise FinalEvaluationTargetError(
            f"Final target count field is boolean: {column}"
        )
    try:
        numeric = values.to_numpy(dtype=float, na_value=np.nan)
    except (TypeError, ValueError) as error:
        raise FinalEvaluationTargetError(
            f"Final target count field is not numeric: {column}"
        ) from error
    if (
        not np.isfinite(numeric).all()
        or (numeric < 0).any()
        or not np.equal(numeric, np.floor(numeric)).all()
    ):
        raise FinalEvaluationTargetError(
            f"Final target count field is not a non-negative integer: {column}"
        )
    return numeric.astype(np.int64)


def _strict_fraction_column(
    frame: pd.DataFrame,
    column: str,
    *,
    finite_mask: np.ndarray | None = None,
) -> np.ndarray:
    try:
        numeric = frame[column].to_numpy(dtype=float, na_value=np.nan)
    except (TypeError, ValueError) as error:
        raise FinalEvaluationTargetError(
            f"Final target fraction field is not numeric: {column}"
        ) from error
    required = np.ones(len(frame), dtype=bool) if finite_mask is None else finite_mask
    if (
        np.isinf(numeric).any()
        or not np.isfinite(numeric[required]).all()
        or ((numeric[np.isfinite(numeric)] < 0.0)
            | (numeric[np.isfinite(numeric)] > 1.0)).any()
        or np.isfinite(numeric[~required]).any()
    ):
        raise FinalEvaluationTargetError(
            f"Final target fraction field is invalid: {column}"
        )
    return numeric


def _audit_base_qa_fields(
    targets: pd.DataFrame,
    date_summary: pd.DataFrame,
) -> None:
    """Independently validate primitive QA types, arithmetic, and missingness."""

    available = _strict_boolean_column(targets, "target_available")
    _strict_boolean_column(targets, "date_usable")
    _strict_boolean_column(date_summary, "date_usable")
    _strict_boolean_column(date_summary, "relative_endpoint_coverage_pass")

    labels = targets["relative_hotspot_top20"]
    if not labels.map(
        lambda value: isinstance(value, (bool, np.bool_))
        or value is None
        or value is pd.NA
        or (
            isinstance(value, (float, np.floating))
            and bool(np.isnan(value))
        )
    ).all():
        raise FinalEvaluationTargetError(
            "Final hotspot labels must contain only strict booleans or missing values."
        )

    source_scene_count = _strict_nonnegative_integer_column(
        targets, "source_scene_count"
    )
    rasterized = _strict_nonnegative_integer_column(
        targets, "rasterized_pixel_count"
    )
    footprint = _strict_nonnegative_integer_column(
        targets, "footprint_pixel_count"
    )
    eligible = _strict_nonnegative_integer_column(
        targets, "eligible_pixel_count_static"
    )
    valid = _strict_nonnegative_integer_column(targets, "valid_pixel_count")
    if (
        (source_scene_count < 1).any()
        or (footprint > rasterized).any()
        or (eligible > rasterized).any()
        or (valid > eligible).any()
        or (valid > footprint).any()
    ):
        raise FinalEvaluationTargetError(
            "Final target pixel-count set relationships are invalid."
        )

    footprint_fraction = _strict_fraction_column(
        targets,
        "footprint_fraction",
        finite_mask=rasterized > 0,
    )
    valid_fraction = _strict_fraction_column(
        targets,
        "valid_fraction",
        finite_mask=eligible > 0,
    )
    expected_footprint = np.divide(
        footprint,
        rasterized,
        out=np.full(len(targets), np.nan, dtype=float),
        where=rasterized > 0,
    )
    expected_valid = np.divide(
        valid,
        eligible,
        out=np.full(len(targets), np.nan, dtype=float),
        where=eligible > 0,
    )
    if (
        not np.allclose(
            footprint_fraction,
            expected_footprint,
            rtol=0.0,
            atol=1e-12,
            equal_nan=True,
        )
        or not np.allclose(
            valid_fraction,
            expected_valid,
            rtol=0.0,
            atol=1e-12,
            equal_nan=True,
        )
    ):
        raise FinalEvaluationTargetError(
            "Final target pixel fractions do not reproduce from primitive counts."
        )

    target_statistics = (
        "target_lst_c",
        "mean_lst_c",
        "std_lst_c",
        "p10_lst_c",
        "p90_lst_c",
        "median_st_uncertainty_k",
        "p90_st_uncertainty_k",
        "median_cloud_distance_km",
    )
    for column in target_statistics:
        try:
            values = targets[column].to_numpy(dtype=float, na_value=np.nan)
        except (TypeError, ValueError) as error:
            raise FinalEvaluationTargetError(
                f"Final target statistic is not numeric: {column}"
            ) from error
        if (
            np.isinf(values).any()
            or not np.isfinite(values[available]).all()
            or not np.isnan(values[~available]).all()
        ):
            raise FinalEvaluationTargetError(
                f"Final target statistic missingness is invalid: {column}"
            )
    reasons = targets["tract_exclusion_reason"]
    if reasons.isna().any() or not reasons.map(lambda value: isinstance(value, str)).all():
        raise FinalEvaluationTargetError(
            "Final tract exclusion reasons must be non-missing strings."
        )
    allowed_reasons = {
        "",
        "no_static_eligible_land",
        "insufficient_scene_footprint",
        "insufficient_valid_pixels",
        "insufficient_valid_fraction",
    }
    if not set(reasons).issubset(allowed_reasons):
        raise FinalEvaluationTargetError(
            "Final tract exclusion reasons contain an unknown value."
        )
    if not np.array_equal(reasons.eq("").to_numpy(dtype=bool), available):
        raise FinalEvaluationTargetError(
            "Final target availability is not equivalent to an empty tract exclusion reason."
        )
    target_date_reasons = targets["date_exclusion_reason"]
    if target_date_reasons.isna().any() or not target_date_reasons.map(
        lambda value: isinstance(value, str)
    ).all():
        raise FinalEvaluationTargetError(
            "Final target-row date exclusion reasons must be non-missing strings."
        )
    allowed_date_reasons = {
        "",
        "insufficient_union_city_footprint",
        "insufficient_date_tract_retention",
    }
    if not set(target_date_reasons).issubset(allowed_date_reasons):
        raise FinalEvaluationTargetError(
            "Final target-row date exclusion reasons contain an unknown value."
        )

    for column in (
        "scene_count",
        "tract_count",
        "retained_tract_count",
        "relative_hotspot_count",
    ):
        _strict_nonnegative_integer_column(date_summary, column)
    _strict_fraction_column(date_summary, "union_city_coverage_fraction")
    _strict_fraction_column(date_summary, "retained_tract_fraction")
    _strict_fraction_column(
        date_summary,
        "minimum_eligible_joint_cell_retention_fraction",
    )
    summary_reasons = date_summary["date_exclusion_reason"]
    if summary_reasons.isna().any() or not summary_reasons.map(
        lambda value: isinstance(value, str)
    ).all():
        raise FinalEvaluationTargetError(
            "Final date exclusion reasons must be non-missing strings."
        )
    if not set(summary_reasons).issubset(allowed_date_reasons):
        raise FinalEvaluationTargetError(
            "Final date exclusion reasons contain an unknown value."
        )


def _audit_date_summaries(
    targets: pd.DataFrame,
    date_summary: pd.DataFrame,
    *,
    inventory: AuthenticatedFinalInventory,
    config: ResearchConfig,
) -> None:
    manifest = inventory.primary_overpasses.copy()
    manifest["target_date"] = _normalized_dates(
        manifest["local_date"], label="Final primary overpasses"
    )
    manifest_lookup = manifest.set_index("target_date", drop=False)
    summary = date_summary.copy()
    summary["target_date"] = _normalized_dates(
        summary["target_date"], label="Final date summary"
    )
    landsat = config.raw["landsat"]
    validation = config.raw["validation"]
    if (
        len(summary) != len(manifest)
        or summary["target_date"].duplicated().any()
        or set(summary["target_date"]) != set(manifest["target_date"])
    ):
        raise FinalEvaluationTargetError(
            "Final date summary does not contain every inventory date exactly once."
        )
    for row in summary.itertuples(index=False):
        frozen = manifest_lookup.loc[row.target_date]
        expected_scenes = tuple(str(frozen.scene_ids).split("|"))
        if (
            str(row.overpass_id) != str(frozen.overpass_id)
            or str(row.platform) != str(frozen.platform)
            or _as_scene_id_tuple(row.scene_ids) != expected_scenes
            or int(row.scene_count) != len(expected_scenes)
            or int(row.tract_count) != len(inventory.tracts)
            or not np.isclose(
                float(row.union_city_coverage_fraction),
                float(frozen.union_city_coverage_fraction),
                rtol=0.0,
                atol=1e-12,
            )
        ):
            raise FinalEvaluationTargetError(
                f"Final date-summary source identity drifted: {row.target_date}"
            )
        date_rows = targets.loc[targets["target_date"].eq(row.target_date)]
        availability = date_rows["target_available"].astype(bool)
        expected_date_usable = bool(
            float(row.union_city_coverage_fraction)
            >= float(landsat["minimum_city_union_coverage_fraction"])
            and float(availability.mean())
            >= float(landsat["minimum_date_tract_retention_fraction"])
        )
        expected_date_reason = ""
        if float(row.union_city_coverage_fraction) < float(
            landsat["minimum_city_union_coverage_fraction"]
        ):
            expected_date_reason = "insufficient_union_city_footprint"
        elif float(availability.mean()) < float(
            landsat["minimum_date_tract_retention_fraction"]
        ):
            expected_date_reason = "insufficient_date_tract_retention"
        if (
            date_rows.empty
            or date_rows["date_usable"].nunique(dropna=False) != 1
            or bool(date_rows["date_usable"].iloc[0]) != expected_date_usable
            or bool(row.date_usable) != expected_date_usable
            or date_rows["date_exclusion_reason"].nunique(dropna=False) != 1
            or str(date_rows["date_exclusion_reason"].iloc[0])
            != expected_date_reason
            or str(row.date_exclusion_reason) != expected_date_reason
            or int(row.retained_tract_count) != int(availability.sum())
            or not np.isclose(
                float(row.retained_tract_fraction),
                float(availability.mean()),
                rtol=0.0,
                atol=1e-12,
            )
        ):
            raise FinalEvaluationTargetError(
                f"Final date-summary QA counts drifted: {row.target_date}"
            )
        recomputed, relative = assign_relative_endpoints(
            date_rows,
            hotspot_fraction=1.0 - float(validation["hotspot_quantile"]),
            minimum_tract_fraction=float(
                validation["minimum_relative_endpoint_tract_fraction"]
            ),
            maximum_quartile_retention_gap=float(
                validation["maximum_relative_endpoint_quartile_retention_gap"]
            ),
            minimum_joint_cell_tracts=int(
                validation["minimum_relative_joint_cell_tracts"]
            ),
            minimum_joint_cell_retention_fraction=float(
                validation["minimum_relative_joint_cell_retention_fraction"]
            ),
        )
        observed_labels = date_rows["relative_hotspot_top20"].astype(
            "boolean"
        )
        expected_labels = recomputed["relative_hotspot_top20"].astype(
            "boolean"
        )
        if (
            bool(row.relative_endpoint_coverage_pass)
            != relative.coverage_pass
            or not np.isclose(
                float(row.minimum_eligible_joint_cell_retention_fraction),
                relative.minimum_eligible_joint_cell_retention_fraction,
                rtol=0.0,
                atol=1e-12,
            )
            or int(row.relative_hotspot_count) != relative.hotspot_count
            or not np.allclose(
                date_rows["lst_anomaly_c"].to_numpy(dtype=float),
                recomputed["lst_anomaly_c"].to_numpy(dtype=float),
                rtol=0.0,
                atol=1e-12,
                equal_nan=True,
            )
            or not observed_labels.equals(expected_labels)
        ):
            raise FinalEvaluationTargetError(
                "Final relative-endpoint gate or labels failed independent "
                f"recomputation: {row.target_date}"
            )
        if (
            date_rows["source_scene_count"].nunique(dropna=False) != 1
            or int(date_rows["source_scene_count"].iloc[0]) != len(expected_scenes)
            or date_rows["source_scene_ids"].nunique(dropna=False) != 1
            or str(date_rows["source_scene_ids"].iloc[0])
            != "|".join(expected_scenes)
        ):
            raise FinalEvaluationTargetError(
                f"Final target-row scene identity drifted: {row.target_date}"
            )
        retained = date_rows.loc[availability, "target_lst_c"]
        expected_statistics = (
            float(retained.median()),
            float(retained.quantile(0.05)),
            float(retained.quantile(0.95)),
        )
        observed_statistics = (
            float(row.median_target_lst_c),
            float(row.p05_target_lst_c),
            float(row.p95_target_lst_c),
        )
        if not np.allclose(
            observed_statistics,
            expected_statistics,
            rtol=0.0,
            atol=1e-12,
            equal_nan=True,
        ):
            raise FinalEvaluationTargetError(
                f"Final date-summary target statistics drifted: {row.target_date}"
            )


def _audit_tract_exclusion_reasons(
    targets: pd.DataFrame,
    *,
    config: ResearchConfig,
) -> None:
    landsat = config.raw["landsat"]
    eligible = targets["eligible_pixel_count_static"].to_numpy(dtype=int)
    footprint = targets["footprint_fraction"].to_numpy(dtype=float)
    valid_pixels = targets["valid_pixel_count"].to_numpy(dtype=int)
    valid_fraction = targets["valid_fraction"].to_numpy(dtype=float)
    minimum_footprint = float(landsat["minimum_tract_footprint_fraction"])
    minimum_pixels = int(landsat["minimum_valid_pixels_per_tract"])
    minimum_valid_fraction = float(landsat["minimum_valid_pixel_fraction"])
    expected = np.full(len(targets), "", dtype=object)
    expected[eligible == 0] = "no_static_eligible_land"
    expected[
        (eligible > 0) & (footprint < minimum_footprint)
    ] = "insufficient_scene_footprint"
    expected[
        (eligible > 0)
        & (footprint >= minimum_footprint)
        & (valid_pixels < minimum_pixels)
    ] = "insufficient_valid_pixels"
    expected[
        (valid_pixels >= minimum_pixels)
        & (footprint >= minimum_footprint)
        & (valid_fraction < minimum_valid_fraction)
    ] = "insufficient_valid_fraction"
    observed = targets["tract_exclusion_reason"].fillna("<missing>").astype(str)
    if not np.array_equal(observed.to_numpy(dtype=object), expected):
        raise FinalEvaluationTargetError(
            "Final tract exclusion reasons failed independent QA recomputation."
        )


def _audit_scene_contributions(
    targets: pd.DataFrame,
    contributions: pd.DataFrame,
    *,
    inventory: AuthenticatedFinalInventory,
) -> None:
    if contributions.duplicated(
        ["target_date", "overpass_id", "scene_id", "tract_geoid"]
    ).any():
        raise FinalEvaluationTargetError(
            "Final scene-contribution table contains duplicate keys."
        )
    if (
        contributions["selected_valid_pixel_count"].isna().any()
        or contributions["selected_valid_pixel_count"].le(0).any()
        or not np.equal(
            contributions["selected_valid_pixel_count"],
            np.floor(contributions["selected_valid_pixel_count"]),
        ).all()
    ):
        raise FinalEvaluationTargetError(
            "Final scene-contribution pixel counts are invalid."
        )
    manifest = inventory.primary_overpasses.copy()
    manifest["expected_target_date"] = _normalized_dates(
        manifest["local_date"],
        label="Final primary-overpass contribution dates",
    )
    manifest = manifest.set_index("overpass_id", drop=False)
    for row in contributions[
        ["target_date", "overpass_id", "scene_id"]
    ].drop_duplicates().itertuples(index=False):
        if row.overpass_id not in manifest.index:
            raise FinalEvaluationTargetError(
                "Final scene contribution references an unlocked scene."
            )
        frozen = manifest.loc[row.overpass_id]
        if (
            str(row.scene_id) not in str(frozen["scene_ids"]).split("|")
            or pd.Timestamp(row.target_date)
            != pd.Timestamp(frozen["expected_target_date"])
        ):
            raise FinalEvaluationTargetError(
                "Final scene contribution has incorrect scene/date lineage."
            )
    totals = (
        contributions.groupby(["tract_geoid", "target_date"], sort=False)[
            "selected_valid_pixel_count"
        ]
        .sum()
        .rename("contribution_count")
    )
    rows = targets.set_index(["tract_geoid", "target_date"])
    if not totals.index.difference(rows.index).empty:
        raise FinalEvaluationTargetError(
            "Final scene contributions contain a non-inventory tract-date key."
        )
    observed = totals.reindex(rows.index, fill_value=0).to_numpy()
    if not np.array_equal(observed, rows["valid_pixel_count"].to_numpy()):
        raise FinalEvaluationTargetError(
            "Final scene contributions do not sum to tract valid-pixel counts."
        )


def audit_final_target_artifacts(
    target_qa: pd.DataFrame,
    date_summary: pd.DataFrame,
    scene_contributions: pd.DataFrame,
    *,
    inventory: AuthenticatedFinalInventory,
    config: ResearchConfig,
    expected_target_config_sha256: str,
) -> dict[str, object]:
    """Audit complete final artifacts without applying a post-hoc date gate."""

    targets = target_qa.copy()
    summaries = date_summary.copy()
    contributions = scene_contributions.copy()
    _verify_exact_columns(targets, FINAL_TARGET_COLUMNS, label="Final target table")
    _verify_exact_columns(
        summaries, FINAL_DATE_SUMMARY_COLUMNS, label="Final date summary"
    )
    _verify_exact_columns(
        contributions,
        FINAL_SCENE_CONTRIBUTION_COLUMNS,
        label="Final scene contributions",
    )
    targets["tract_geoid"] = targets["tract_geoid"].astype("string")
    targets["target_date"] = _normalized_dates(
        targets["target_date"], label="Final target table"
    )
    summaries["target_date"] = _normalized_dates(
        summaries["target_date"], label="Final date summary"
    )
    contributions["tract_geoid"] = contributions["tract_geoid"].astype("string")
    contributions["target_date"] = _normalized_dates(
        contributions["target_date"], label="Final scene contributions"
    )
    validate_unique_primary_key(targets)
    if (
        len(targets) != len(inventory.key_universe)
        or targets["tract_geoid"].nunique() != len(inventory.tracts)
        or targets["target_date"].nunique() != len(inventory.primary_overpasses)
        or not targets["target_date"].dt.year.eq(FINAL_TEST_YEAR).all()
    ):
        raise FinalEvaluationTargetError("Final target cardinality drifted.")
    _audit_key_alignment(targets, inventory=inventory)
    _audit_base_qa_fields(targets, summaries)
    try:
        validate_static_eligible_denominator(targets)
        landsat = config.raw["landsat"]
        validate_target_qa_contract(
            targets,
            minimum_footprint_fraction=float(
                landsat["minimum_tract_footprint_fraction"]
            ),
            minimum_valid_fraction=float(landsat["minimum_valid_pixel_fraction"]),
            minimum_valid_pixels=int(landsat["minimum_valid_pixels_per_tract"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise FinalEvaluationTargetError(
            "Final target denominator or QA contract failed."
        ) from error
    _audit_tract_exclusion_reasons(targets, config=config)
    if targets["target_available"].isna().any():
        raise FinalEvaluationTargetError(
            "Final target availability flag contains missing values."
        )
    availability = targets["target_available"].astype(bool)
    if not availability.eq(targets["target_lst_c"].notna()).all():
        raise FinalEvaluationTargetError(
            "Final target availability flag differs from the frozen QA outcome."
        )
    _, zones, static_land, grid_identity = _fixed_grid_and_zones(
        config,
        inventory.city,
        inventory.tracts,
    )
    expected_grid_sha256 = hashlib.sha256(
        grid_identity.encode()
    ).hexdigest()
    rasterized = zones > 0
    eligible = rasterized & static_land
    expected_zone_sha256 = hashlib.sha256(zones.tobytes()).hexdigest()
    expected_eligible_mask_sha256 = hashlib.sha256(
        np.packbits(eligible.ravel()).tobytes()
    ).hexdigest()
    zone_count = len(inventory.tracts)
    expected_rasterized_counts = np.bincount(
        zones[rasterized],
        minlength=zone_count + 1,
    )[1:]
    expected_eligible_counts = np.bincount(
        zones[eligible],
        minlength=zone_count + 1,
    )[1:]
    expected_eligible_identities = zonal_mask_identity_hashes(
        zones,
        eligible,
        zone_count=zone_count,
        grid_identity=grid_identity,
    )
    support = pd.DataFrame(
        {
            "tract_geoid": inventory.tracts["GEOID"].astype("string"),
            "expected_rasterized_pixel_count": expected_rasterized_counts,
            "expected_eligible_pixel_count_static": expected_eligible_counts,
            "expected_eligible_pixel_identity_sha256": (
                expected_eligible_identities
            ),
        }
    )
    observed_support = targets.loc[
        :,
        [
            "tract_geoid",
            "rasterized_pixel_count",
            "eligible_pixel_count_static",
            "eligible_pixel_identity_sha256",
        ],
    ].drop_duplicates()
    support_ok = not observed_support["tract_geoid"].duplicated().any()
    if support_ok:
        observed_support = observed_support.merge(
            support,
            on="tract_geoid",
            how="outer",
            indicator=True,
            validate="one_to_one",
        )
        support_ok = (
            observed_support["_merge"].eq("both").all()
            and np.array_equal(
                observed_support["rasterized_pixel_count"].to_numpy(
                    dtype=int
                ),
                observed_support[
                    "expected_rasterized_pixel_count"
                ].to_numpy(dtype=int),
            )
            and np.array_equal(
                observed_support[
                    "eligible_pixel_count_static"
                ].to_numpy(dtype=int),
                observed_support[
                    "expected_eligible_pixel_count_static"
                ].to_numpy(dtype=int),
            )
            and observed_support["eligible_pixel_identity_sha256"]
            .astype(str)
            .equals(
                observed_support[
                    "expected_eligible_pixel_identity_sha256"
                ].astype(str)
            )
        )
    summary_identity_ok = (
        summaries["grid_sha256"].eq(expected_grid_sha256).all()
        and summaries["zone_raster_sha256"]
        .eq(expected_zone_sha256)
        .all()
        and summaries["eligible_mask_sha256"]
        .eq(expected_eligible_mask_sha256)
        .all()
        and summaries["config_sha256"]
        .eq(expected_target_config_sha256)
        .all()
        and summaries["tract_manifest_sha256"]
        .eq(inventory.locks["tract_manifest_sha256"])
        .all()
    )
    if (
        targets["config_sha256"].nunique(dropna=False) != 1
        or targets["config_sha256"].iloc[0] != expected_target_config_sha256
        or targets["tract_manifest_sha256"].nunique(dropna=False) != 1
        or targets["tract_manifest_sha256"].iloc[0]
        != inventory.locks["tract_manifest_sha256"]
        or targets["grid_sha256"].nunique(dropna=False) != 1
        or targets["grid_sha256"].iloc[0] != expected_grid_sha256
        or not support_ok
        or not summary_identity_ok
    ):
        raise FinalEvaluationTargetError(
            "Final target config, tract, grid, or eligible support identity drifted."
        )
    _audit_date_summaries(
        targets,
        summaries,
        inventory=inventory,
        config=config,
    )
    _audit_scene_contributions(targets, contributions, inventory=inventory)
    return {
        "state": "complete_all_inventory_dates_assessed",
        "target_row_count": int(len(targets)),
        "inventory_date_count": int(targets["target_date"].nunique()),
        "tract_count": int(targets["tract_geoid"].nunique()),
        "exact_key_universe": True,
        "static_eligible_denominator_invariant": True,
        "qa_contract_exact": True,
        "minimum_development_date_gate_applied": False,
    }


def _compile_caches(
    *,
    inventory: AuthenticatedFinalInventory,
    config: ResearchConfig,
    expected_target_config_sha256: str,
    base_cache_lock: dict[str, str],
    target_directory: Path,
    gate: _ValuesAccessGate,
) -> FinalTargetArtifacts:
    target_frames: list[pd.DataFrame] = []
    summary_frames: list[pd.DataFrame] = []
    contribution_frames: list[pd.DataFrame] = []
    for row in inventory.primary_overpasses.itertuples(index=False):
        cache_directory = target_directory / "by_overpass" / str(row.overpass_id)
        expected_lock = _cache_lock(base_cache_lock, row)
        if not _cache_is_current(
            cache_directory,
            expected_lock=expected_lock,
            gate=gate,
        ):
            raise FinalEvaluationTargetError(
                f"Final target cache is incomplete: {row.overpass_id}"
            )
        gate.before_first_value_access()
        target_frames.append(
            pd.read_parquet(cache_directory / CACHE_OUTPUT_FILES[0])
        )
        summary_frames.append(
            pd.read_parquet(cache_directory / CACHE_OUTPUT_FILES[1])
        )
        contribution_frames.append(
            pd.read_parquet(cache_directory / CACHE_OUTPUT_FILES[2])
        )
    targets = pd.concat(target_frames, ignore_index=True)
    summaries = pd.concat(summary_frames, ignore_index=True)
    contributions = pd.concat(contribution_frames, ignore_index=True)
    targets["target_date"] = _normalized_dates(
        targets["target_date"], label="Final target cache"
    )
    summaries["target_date"] = _normalized_dates(
        summaries["target_date"], label="Final date-summary cache"
    )
    contributions["target_date"] = _normalized_dates(
        contributions["target_date"], label="Final scene-contribution cache"
    )
    targets = targets.sort_values(
        ["target_date", "tract_geoid"], kind="stable"
    ).reset_index(drop=True)
    summaries = summaries.sort_values("target_date", kind="stable").reset_index(
        drop=True
    )
    contributions = contributions.sort_values(
        ["target_date", "overpass_id", "scene_id", "tract_geoid"],
        kind="stable",
    ).reset_index(drop=True)
    audit = audit_final_target_artifacts(
        targets,
        summaries,
        contributions,
        inventory=inventory,
        config=config,
        expected_target_config_sha256=expected_target_config_sha256,
    )
    return FinalTargetArtifacts(
        target_qa=targets,
        date_summary=summaries,
        scene_contributions=contributions,
        audit=audit,
    )


def build_final_targets_transaction(
    *,
    inventory: AuthenticatedFinalInventory,
    config: ResearchConfig,
    expected_target_config_sha256: str,
    claim_id: str,
    staging_directory: str | Path,
    values_opened_callback: Callable[[], None],
    scene_reader: SceneReader = read_aligned_scene_from_hrefs,
) -> FinalTargetArtifacts:
    """Build or resume the claim-bound 2025 target transaction.

    The callback must atomically create or authenticate the canonical
    ``VALUES_OPENED`` marker.  If it raises, no target/QA asset or cached target
    table is read.
    """

    if not isinstance(claim_id, str) or not claim_id.strip() or len(claim_id) > 256:
        raise FinalEvaluationTargetError("A bounded non-empty claim ID is required.")
    if not callable(values_opened_callback):
        raise TypeError("values_opened_callback must be callable.")
    if config.final_test_year != FINAL_TEST_YEAR:
        raise FinalEvaluationTargetError("Final target year must remain 2025.")
    try:
        config.path.resolve().relative_to(inventory.project_root)
    except ValueError as error:
        raise FinalEvaluationTargetError(
            "Research configuration path escapes the authenticated project root."
        ) from error
    reloaded_config = load_config(config.path)
    if canonical_sha256(reloaded_config.raw) != canonical_sha256(config.raw):
        raise FinalEvaluationTargetError(
            "In-memory research configuration differs from its on-disk file."
        )
    config.require_final_test_access()
    observed_target_config_sha256 = target_config_sha256(config)
    if observed_target_config_sha256 != expected_target_config_sha256:
        raise FinalEvaluationTargetError(
            "Current target configuration differs from the frozen evaluation lock."
        )
    reauthenticated = authenticate_final_landsat_inventory(
        inventory.inventory_path,
        expected_inventory_file_sha256=inventory.inventory_file_sha256,
        expected_inventory_commit_sha256=inventory.inventory_commit_sha256,
        expected_key_semantic_sha256=inventory.locks[
            "key_universe_semantic_sha256"
        ],
        expected_scene_count=len(inventory.scenes),
        expected_overpass_count=len(inventory.primary_overpasses),
        expected_tract_count=len(inventory.tracts),
        expected_key_count=len(inventory.key_universe),
        project_root=inventory.project_root,
    )
    if reauthenticated.readiness_record != inventory.readiness_record:
        raise FinalEvaluationTargetError(
            "Authenticated final inventory changed before target construction."
        )
    inventory = reauthenticated

    staging = Path(staging_directory)
    staging = (
        staging.resolve()
        if staging.is_absolute()
        else (inventory.project_root / staging).resolve()
    )
    try:
        staging.relative_to(inventory.project_root)
    except ValueError as error:
        raise FinalEvaluationTargetError(
            "Final target staging directory escapes the project root."
        ) from error
    target_directory = staging / "targets"

    grid, zones, static_land, grid_identity = _fixed_grid_and_zones(
        config,
        inventory.city,
        inventory.tracts,
    )
    grid_sha256 = hashlib.sha256(grid_identity.encode()).hexdigest()
    pipeline_sha256, pipeline_payload = final_target_pipeline_fingerprint(
        inventory.project_root
    )
    base_cache_lock = {
        "claim_id": claim_id,
        "target_algorithm_version": FINAL_TARGET_ALGORITHM_VERSION,
        "target_pipeline_sha256": pipeline_sha256,
        "target_config_sha256": observed_target_config_sha256,
        "research_config_file_sha256": sha256_file(config.path),
        "inventory_file_sha256": inventory.inventory_file_sha256,
        "inventory_commit_sha256": inventory.inventory_commit_sha256,
        "key_universe_semantic_sha256": inventory.locks[
            "key_universe_semantic_sha256"
        ],
        "tract_manifest_sha256": inventory.locks["tract_manifest_sha256"],
        "grid_sha256": grid_sha256,
    }
    build_lock: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": FINAL_TARGET_ALGORITHM_VERSION,
        "state": "claim_bound_before_target_values",
        "cache_lock": base_cache_lock,
        "expected_scene_count": int(len(inventory.scenes)),
        "expected_overpass_count": int(len(inventory.primary_overpasses)),
        "expected_tract_count": int(len(inventory.tracts)),
        "expected_key_count": int(len(inventory.key_universe)),
        "target_config_payload": target_config_payload(config),
        "target_pipeline_fingerprint": pipeline_payload,
    }
    _bind_target_build_directory(target_directory, build_lock=build_lock)
    gate = _ValuesAccessGate(values_opened_callback)
    for row in inventory.primary_overpasses.itertuples(index=False):
        _process_overpass(
            row,
            inventory=inventory,
            config=config,
            grid=grid,
            zones=zones,
            static_land=static_land,
            grid_identity=grid_identity,
            base_cache_lock=base_cache_lock,
            target_directory=target_directory,
            gate=gate,
            scene_reader=scene_reader,
        )
    return _compile_caches(
        inventory=inventory,
        config=config,
        expected_target_config_sha256=expected_target_config_sha256,
        base_cache_lock=base_cache_lock,
        target_directory=target_directory,
        gate=gate,
    )
