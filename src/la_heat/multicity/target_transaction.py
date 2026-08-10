"""Authenticate the frozen target workload without opening target assets."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import pandas as pd

from la_heat.aligned_landsat import REQUIRED_ASSETS
from la_heat.multicity.portable_predictor_components import CITY_IDS
from la_heat.multicity.portable_predictor_inventory import (
    DEFAULT_CONFIG as INVENTORY_CONFIG,
)
from la_heat.multicity.portable_predictor_inventory import (
    EXTERNAL_CITY_IDS,
    verify_portable_predictor_inventory,
)
from la_heat.multicity.target_context import (
    MANIFEST_PATH as TARGET_CONTEXT_MANIFEST,
)
from la_heat.multicity.target_context import (
    load_target_city_context,
    stage_multicity_target_contexts,
)
from la_heat.provenance import atomic_json, canonical_sha256

ALGORITHM_VERSION: Final = "multicity-target-build-plan-v1"
PREPARED_STATE: Final = "prepared_target_blind_builder_not_authorized"
MANIFEST_PATH: Final = Path("manifests/multicity/targets/TARGET_BUILD_PLAN.json")
INVENTORY_MANIFEST: Final = Path(
    "manifests/multicity/predictors/PORTABLE_PREDICTOR_INVENTORY.json"
)
SOURCE_CITY_ID: Final = "los_angeles_ca"
SOURCE_LANE: Final = "los_angeles_2020_2024_source"
EXTERNAL_LANE: Final = "three_city_2025_combined_external"
FROZEN_REQUIRED_ASSETS: Final = (
    "lwir11",
    "qa_pixel",
    "qa",
    "cdist",
    "qa_radsat",
)
EXPECTED_CITY_COUNTS: Final = {
    "los_angeles_ca": {"overpasses": 90, "scenes": 177, "keys": 98_640},
    "phoenix_az": {"overpasses": 22, "scenes": 44, "keys": 8_250},
    "houston_tx": {"overpasses": 21, "scenes": 42, "keys": 13_671},
    "chicago_il": {"overpasses": 21, "scenes": 21, "keys": 16_380},
}
EXPECTED_LANE_COUNTS: Final = {
    SOURCE_LANE: {"overpasses": 90, "scenes": 177, "keys": 98_640},
    EXTERNAL_LANE: {"overpasses": 64, "scenes": 107, "keys": 38_301},
}


class TargetTransactionError(RuntimeError):
    """Raised when frozen target identities no longer form one exact workload."""


def _scene_ids(value: object) -> tuple[str, ...]:
    identifiers = tuple(str(value).split("|"))
    if (
        not identifiers
        or any(not identifier or identifier.strip() != identifier for identifier in identifiers)
        or len(identifiers) != len(set(identifiers))
    ):
        raise TargetTransactionError("Overpass scene identities are invalid.")
    return identifiers


def authenticate_city_target_relationships(
    *,
    city_id: str,
    items: pd.DataFrame,
    overpasses: pd.DataFrame,
    keys: pd.DataFrame,
    context_geoids: tuple[str, ...],
    target_grid_sha256: str,
    expected_counts: Mapping[str, int] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Bind frozen item IDs to overpasses and tract keys using metadata only."""

    if city_id not in CITY_IDS:
        raise TargetTransactionError(f"Unknown city: {city_id}")
    item_columns = {
        "item_id",
        "platform",
        "acquisition_local_date",
    }
    overpass_columns = {
        "overpass_id",
        "platform",
        "local_date",
        "scene_ids",
        "scene_count",
        "primary_eligible",
        "source_lock_sha256",
    }
    key_columns = {
        "city_id",
        "tract_geoid",
        "target_date",
        "overpass_id",
        "platform",
    }
    if not item_columns.issubset(items) or not overpass_columns.issubset(overpasses):
        raise TargetTransactionError(f"Frozen Landsat metadata schema changed for {city_id}.")
    if not key_columns.issubset(keys):
        raise TargetTransactionError(f"Frozen predictor-key schema changed for {city_id}.")
    for label, frame in (("items", items), ("overpasses", overpasses), ("keys", keys)):
        forbidden = sorted(
            str(column)
            for column in frame.columns
            if "href" in str(column).lower()
            or str(column).lower() in {"asset", "assets"}
        )
        if forbidden:
            raise TargetTransactionError(
                f"Target planning {label} may not contain asset hrefs: {forbidden}"
            )
    if items.empty or items["item_id"].astype(str).duplicated().any():
        raise TargetTransactionError(f"Scene identities are empty or duplicated for {city_id}.")

    geoids = tuple(str(value) for value in context_geoids)
    if not geoids or len(geoids) != len(set(geoids)):
        raise TargetTransactionError(f"Target-context GEOIDs are invalid for {city_id}.")
    dates = pd.to_datetime(keys["target_date"], errors="raise")
    key_city = keys["city_id"].astype(str)
    if (
        keys.empty
        or set(key_city) != {city_id}
        or keys.duplicated(["city_id", "tract_geoid", "target_date"]).any()
    ):
        raise TargetTransactionError(f"Predictor keys are invalid for {city_id}.")

    primary = overpasses.loc[overpasses["primary_eligible"].eq(True)].copy()  # noqa: E712
    primary["local_date"] = primary["local_date"].astype(str)
    primary = primary.sort_values(["local_date", "overpass_id"], kind="stable")
    if (
        primary.empty
        or primary["overpass_id"].astype(str).duplicated().any()
        or primary["local_date"].duplicated().any()
        or set(keys["overpass_id"].astype(str)) != set(primary["overpass_id"].astype(str))
    ):
        raise TargetTransactionError(f"Primary overpass-key universe changed for {city_id}.")

    item_lookup = items.set_index(items["item_id"].astype(str), drop=False)
    context_set = set(geoids)
    used_scenes: list[str] = []
    units: list[dict[str, Any]] = []
    lane = SOURCE_LANE if city_id == SOURCE_CITY_ID else EXTERNAL_LANE
    for overpass in primary.itertuples(index=False):
        overpass_id = str(overpass.overpass_id)
        target_date = str(overpass.local_date)
        platform = str(overpass.platform)
        scenes = _scene_ids(overpass.scene_ids)
        if len(scenes) != int(overpass.scene_count) or any(
            scene_id not in item_lookup.index for scene_id in scenes
        ):
            raise TargetTransactionError(f"Scene membership changed for {overpass_id}.")
        scene_rows = item_lookup.loc[list(scenes)]
        if (
            set(scene_rows["platform"].astype(str)) != {platform}
            or set(scene_rows["acquisition_local_date"].astype(str)) != {target_date}
        ):
            raise TargetTransactionError(f"Scene metadata disagrees with {overpass_id}.")

        selected_keys = keys.loc[keys["overpass_id"].astype(str).eq(overpass_id)]
        selected_dates = dates.loc[selected_keys.index].dt.strftime("%Y-%m-%d")
        if (
            len(selected_keys) != len(geoids)
            or set(selected_keys["tract_geoid"].astype(str)) != context_set
            or set(selected_keys["platform"].astype(str)) != {platform}
            or set(selected_dates) != {target_date}
        ):
            raise TargetTransactionError(f"Tract keys disagree with {overpass_id}.")
        source_lock = str(overpass.source_lock_sha256)
        if len(source_lock) != 64:
            raise TargetTransactionError(f"Source lock is invalid for {overpass_id}.")
        unit: dict[str, Any] = {
            "unit_id": f"target:{city_id}:{overpass_id}",
            "kind": "overpass_target",
            "lane": lane,
            "city_id": city_id,
            "target_date": target_date,
            "overpass_id": overpass_id,
            "platform": platform,
            "scene_ids": list(scenes),
            "scene_count": len(scenes),
            "tract_key_count": len(selected_keys),
            "source_lock_sha256": source_lock,
            "target_grid_sha256": target_grid_sha256,
        }
        unit["relationship_sha256"] = canonical_sha256(unit)
        units.append(unit)
        used_scenes.extend(scenes)

    if len(used_scenes) != len(set(used_scenes)):
        raise TargetTransactionError(f"A scene was reused across overpasses for {city_id}.")
    summary: dict[str, Any] = {
        "role": "source_anchor" if city_id == SOURCE_CITY_ID else "external_confirmation",
        "lane": lane,
        "tract_count": len(geoids),
        "overpass_target_unit_count": len(units),
        "primary_scene_count": len(used_scenes),
        "target_key_count": len(keys),
        "date_range": [units[0]["target_date"], units[-1]["target_date"]],
        "target_grid_sha256": target_grid_sha256,
        "relationships_sha256": canonical_sha256(units),
    }
    observed = {
        "overpasses": len(units),
        "scenes": len(used_scenes),
        "keys": len(keys),
    }
    if expected_counts is not None and observed != dict(expected_counts):
        raise TargetTransactionError(f"Frozen workload counts changed for {city_id}: {observed}")
    return summary, units


def _read_table(root: Path, inventory: dict[str, Any], city_id: str, kind: str) -> pd.DataFrame:
    record = inventory["output_tables"][f"{city_id}/{kind}"]
    path = (root / str(record["path"])).resolve()
    if not path.is_relative_to(root):
        raise TargetTransactionError("Predictor inventory path escapes the project.")
    return pd.read_parquet(path)


def stage_multicity_target_build_plan(
    project_root: str | Path,
    *,
    check_only: bool = False,
) -> dict[str, Any]:
    """Write or reproduce the deterministic, still-unauthorized target plan."""

    root = Path(project_root).resolve()
    if tuple(REQUIRED_ASSETS) != FROZEN_REQUIRED_ASSETS:
        raise TargetTransactionError("Frozen Landsat target asset contract changed.")
    if tuple(CITY_IDS) != (SOURCE_CITY_ID, *EXTERNAL_CITY_IDS):
        raise TargetTransactionError("Frozen source/external city order changed.")
    inventory = verify_portable_predictor_inventory(root / INVENTORY_CONFIG)
    contexts = stage_multicity_target_contexts(root, check_only=True)

    city_summaries: dict[str, dict[str, Any]] = {}
    overpass_units: list[dict[str, Any]] = []
    for city_id in CITY_IDS:
        context = load_target_city_context(root, city_id)
        summary, units = authenticate_city_target_relationships(
            city_id=city_id,
            items=_read_table(root, inventory, city_id, "landsat_items"),
            overpasses=_read_table(root, inventory, city_id, "overpasses"),
            keys=_read_table(root, inventory, city_id, "keys"),
            context_geoids=tuple(context.tracts["tract_geoid"].astype(str)),
            target_grid_sha256=context.grid.sha256,
            expected_counts=EXPECTED_CITY_COUNTS[city_id],
        )
        summary["context_locks"] = dict(context.locks)
        city_summaries[city_id] = summary
        overpass_units.extend(units)

    lanes = {
        SOURCE_LANE: {
            "city_ids": [SOURCE_CITY_ID],
            "years": [2020, 2021, 2022, 2023, 2024],
            "purpose": "los_angeles_training_and_calibration_labels",
            **EXPECTED_LANE_COUNTS[SOURCE_LANE],
        },
        EXTERNAL_LANE: {
            "city_ids": list(EXTERNAL_CITY_IDS),
            "years": [2025],
            "purpose": "one_combined_external_confirmation_cohort",
            "single_append_only_claim_required": True,
            "per_city_claims_forbidden": True,
            **EXPECTED_LANE_COUNTS[EXTERNAL_LANE],
        },
    }
    observed_lanes: dict[str, dict[str, int]] = {}
    for lane in (SOURCE_LANE, EXTERNAL_LANE):
        members = [unit for unit in overpass_units if unit["lane"] == lane]
        observed_lanes[lane] = {
            "overpasses": len(members),
            "scenes": sum(int(unit["scene_count"]) for unit in members),
            "keys": sum(int(unit["tract_key_count"]) for unit in members),
        }
    if observed_lanes != EXPECTED_LANE_COUNTS:
        raise TargetTransactionError(f"Source/external lane counts changed: {observed_lanes}")

    work_units = list(overpass_units)
    for city_id in CITY_IDS:
        city = city_summaries[city_id]
        work_units.append(
            {
                "unit_id": f"compile:{city_id}",
                "kind": "city_compile",
                "lane": city["lane"],
                "city_id": city_id,
                "expected_overpass_unit_count": city["overpass_target_unit_count"],
                "expected_target_key_count": city["target_key_count"],
            }
        )
    work_units.append(
        {
            "unit_id": "merge:four_city_targets",
            "kind": "final_merge",
            "city_ids": list(CITY_IDS),
            "expected_city_compile_count": len(CITY_IDS),
            "expected_target_key_count": sum(
                int(city["target_key_count"]) for city in city_summaries.values()
            ),
        }
    )
    for ordinal, unit in enumerate(work_units, start=1):
        unit["ordinal"] = ordinal
    if len(overpass_units) != 154 or len(work_units) != 159:
        raise TargetTransactionError("Target work-plan unit counts changed.")

    payload: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "state": PREPARED_STATE,
        "input_locks": {
            "predictor_inventory": {
                "path": INVENTORY_MANIFEST.as_posix(),
                "commit_sha256": inventory["commit_sha256"],
            },
            "target_contexts": {
                "path": TARGET_CONTEXT_MANIFEST.as_posix(),
                "commit_sha256": contexts["commit_sha256"],
            },
        },
        "target_asset_contract": {
            "required_assets": list(FROZEN_REQUIRED_ASSETS),
            "required_assets_exact": True,
            "asset_hrefs_present_in_plan": False,
            "later_hydration_must_use_frozen_item_ids": True,
            "date_or_scene_reselection_permitted": False,
        },
        "output_contract": {
            "staging_root": "data/interim/multicity/targets",
            "phase_i_target_outputs_reused": False,
            "signed_urls_tokens_or_cookies_persisted": False,
        },
        "cohort_lanes": lanes,
        "cities": city_summaries,
        "work_plan": {
            "overpass_target_unit_count": len(overpass_units),
            "city_compile_unit_count": len(CITY_IDS),
            "final_merge_unit_count": 1,
            "total_unit_count": len(work_units),
            "units_sha256": canonical_sha256(work_units),
            "units": work_units,
        },
        "authorization": {
            "asset_href_hydration_authorized": False,
            "source_target_build_authorized": False,
            "external_target_build_authorized": False,
            "external_target_values_open_authorized": False,
            "model_fit_or_prediction_authorized": False,
            "external_claim_created": False,
        },
        "access_contract": {
            "network_access_performed": False,
            "landsat_asset_hrefs_read": False,
            "landsat_thermal_or_target_qa_values_read": False,
            "target_tables_read": False,
            "model_fit_or_prediction_performed": False,
        },
        "next_safe_stage": "wait_for_predictor_readiness_then_lock_protocol",
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    manifest_path = root / MANIFEST_PATH
    if check_only:
        try:
            observed = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise TargetTransactionError(
                f"Cannot read target build plan: {manifest_path}"
            ) from error
        if observed != payload:
            raise TargetTransactionError("Target build plan no longer reproduces exactly.")
        return observed
    atomic_json(payload, manifest_path)
    return payload
