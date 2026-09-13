"""Authorized resumable four-city blind Landsat target build using frozen 4 K QA."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pandas as pd

from la_heat.config import load_config
from la_heat.multicity import m3_blind_prediction_v1 as helpers
from la_heat.multicity import m3_blind_prediction_v2 as prediction
from la_heat.multicity import m3_blind_spatial_blocks_v1 as blocks
from la_heat.multicity.m3_blind_predictor_sentinel_static_runtime_v1 import _city_support
from la_heat.multicity.m3_source_offline_qa import (
    candidate_config,
    candidate_target_config_sha256,
)
from la_heat.multicity.target_context import TargetCityContext
from la_heat.multicity.target_processor import (
    PlanetaryComputerSceneHydrator,
    aggregate_authorized_overpass,
)
from la_heat.provenance import canonical_frame_sha256, canonical_sha256, sha256_file

ALGORITHM_VERSION: Final = "m3-blind-target-v1"
AUTHORIZATION_PATH: Final = Path(
    "manifests/multicity/next_experiment/M3_BLIND_TARGET_V1_AUTHORIZATION.json"
)
VALUES_OPENED_PATH: Final = Path("data/interim/multicity/m3_blind_target_v1/VALUES_OPENED.json")
OUTPUT_ROOT: Final = Path("data/interim/multicity/m3_blind_target_v1")
COMPLETION_PATH: Final = Path(
    "manifests/multicity/next_experiment/blind_evaluation_v1/M3_BLIND_TARGETS_COMPLETE.json"
)
PREDICTION_COMMIT: Final = "295ccca0ea0239cf6eb5633b736a7565abbac3cc47992bae6bcbc9ba4d6f74ac"
BLOCKS_COMMIT: Final = "7e9e3025161a5d5688a439a491d0283aad20363441023a3af5abf6f79bb9fdfa"
EXPECTED_DATES: Final = {
    "seattle_wa": 54,
    "denver_co": 31,
    "atlanta_ga": 28,
    "miami_fl": 30,
}
CODE_PATHS: Final = (
    "src/la_heat/multicity/m3_blind_target_v1.py",
    "src/la_heat/multicity/target_processor.py",
    "src/la_heat/multicity/m3_source_offline_qa.py",
    "src/la_heat/target_aggregation.py",
    "src/la_heat/aligned_landsat.py",
    "src/la_heat/mosaic.py",
    "scripts/authorize_m3_blind_target_v1.py",
    "scripts/run_m3_blind_target_v1.py",
    "configs/research.toml",
)
REQUIRED_OUTPUT_COLUMNS: Final = {
    "city_id",
    "target_date",
    "tract_geoid",
    "target_lst_c",
    "target_available",
    "date_usable",
}


class M3BlindTargetError(RuntimeError):
    """Raised when the combined blind-target claim changes."""


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise M3BlindTargetError(f"Cannot read {path}") from error
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    if canonical_sha256(body) != payload.get("commit_sha256"):
        raise M3BlindTargetError(f"Invalid commit: {path}")
    return payload


def _committed(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["commit_sha256"] = canonical_sha256(payload)
    return result


def _inventory_record(root: Path, city_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    feasibility = _read(
        helpers._inside(
            root,
            f"manifests/multicity/next_experiment/cities/{city_id}/FEASIBILITY.json",
        )
    )
    record = dict(feasibility["landsat"]["outputs"]["physical_overpasses"])
    return feasibility, record


def build_authorization(project_root: str | Path) -> dict[str, Any]:
    """Build authorization from metadata only; do not open Landsat hrefs or values."""

    root = Path(project_root).resolve()
    prediction_completion = _read(helpers._inside(root, prediction.COMPLETION_PATH))
    block_completion = _read(helpers._inside(root, blocks.COMPLETION_PATH))
    if (
        prediction_completion.get("commit_sha256") != PREDICTION_COMMIT
        or block_completion.get("commit_sha256") != BLOCKS_COMMIT
    ):
        raise M3BlindTargetError("Prediction-before-target anchors changed.")
    cities = []
    for city_id in helpers.BLIND_CITY_IDS:
        feasibility, record = _inventory_record(root, city_id)
        if (
            feasibility.get("passes") is not True
            or feasibility["landsat"]["eligible_unique_physical_date_count"]
            != EXPECTED_DATES[city_id]
            or feasibility["landsat"]["access_contract"]["landsat_asset_hrefs_read"] is not False
        ):
            raise M3BlindTargetError(f"{city_id} target inventory changed.")
        cities.append(
            {
                "city_id": city_id,
                "feasibility_commit_sha256": feasibility["commit_sha256"],
                "physical_overpasses": record,
                "eligible_date_count": EXPECTED_DATES[city_id],
            }
        )
    code = []
    for relative in CODE_PATHS:
        path = helpers._inside(root, relative)
        code.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_target_authorized",
            "claim": "one_indivisible_combined_four_city_target_claim",
            "prediction_completion_commit_sha256": PREDICTION_COMMIT,
            "spatial_blocks_completion_commit_sha256": BLOCKS_COMMIT,
            "qa_candidate_id": "4k",
            "qa_threshold_kelvin": 4.0,
            "city_count": 4,
            "eligible_date_count": sum(EXPECTED_DATES.values()),
            "cities": cities,
            "code": code,
            "values_opened_marker": VALUES_OPENED_PATH.as_posix(),
            "permissions": {
                "hydrate_exact_frozen_landsat_scene_hrefs": True,
                "read_lwir11_qa_pixel_qa_cdist_and_qa_radsat": True,
                "apply_locked_4k_qa_and_tract_aggregation": True,
                "write_resumable_overpass_and_combined_target_tables": True,
                "network_reads": True,
                "change_city_date_qa_support_model_uq_or_risk": False,
                "fit_retrain_retune_or_select": False,
                "score_or_evaluate_before_target_completion_authentication": False,
            },
            "next_safe_stage": "open_combined_claim_and_run_resumable_target_build",
        }
    )


def create_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    payload = build_authorization(root)
    helpers._write_json_exclusive(payload, helpers._inside(root, AUTHORIZATION_PATH))
    return authenticate_authorization(root)


def authenticate_authorization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observed = _read(helpers._inside(root, AUTHORIZATION_PATH))
    if observed != build_authorization(root):
        raise M3BlindTargetError("Blind-target authorization drifted.")
    return observed


def _open_claim(root: Path, permit: dict[str, Any]) -> dict[str, Any]:
    path = helpers._inside(root, VALUES_OPENED_PATH)
    if path.exists():
        marker = _read(path)
    else:
        marker = _committed(
            {
                "schema_version": 1,
                "algorithm_version": ALGORITHM_VERSION,
                "state": "m3_blind_target_values_opened",
                "authorization_commit_sha256": permit["commit_sha256"],
                "prediction_completion_commit_sha256": PREDICTION_COMMIT,
                "claim": permit["claim"],
                "retuning_after_this_marker": False,
            }
        )
        helpers._write_json_exclusive(marker, path)
    if marker.get("authorization_commit_sha256") != permit["commit_sha256"]:
        raise M3BlindTargetError("Values-opened marker has another authorization.")
    return marker


class _Gate:
    def __init__(self, root: Path, permit_commit: str) -> None:
        self.root = root
        self.permit_commit = permit_commit

    def before_first_value_access(self) -> None:
        permit = authenticate_authorization(self.root)
        marker = _read(helpers._inside(self.root, VALUES_OPENED_PATH))
        if (
            permit["commit_sha256"] != self.permit_commit
            or marker.get("authorization_commit_sha256") != self.permit_commit
        ):
            raise M3BlindTargetError("Target value gate failed.")


def _context(root: Path, city_id: str) -> TargetCityContext:
    support = _city_support(root, city_id)
    block_table = pd.read_parquet(helpers._inside(root, blocks.OUTPUT_PATH))
    city_blocks = block_table.loc[block_table["city_id"].eq(city_id)].copy()
    metadata = [column for column in blocks.OUTPUT_COLUMNS if column != "city_id"]
    tracts = support.tracts.merge(
        city_blocks.loc[:, metadata], on="tract_geoid", validate="one_to_one"
    )
    tracts["GEOID"] = tracts["tract_geoid"].astype("string")
    if len(tracts) != blocks.EXPECTED_TRACTS[city_id]:
        raise M3BlindTargetError(f"{city_id} target context changed.")
    return TargetCityContext(
        city_id=city_id,
        grid=support.grid,
        zones=support.zones,
        eligible_land=support.eligible_land,
        tracts=tracts,
        locks={},
    )


def _inventory(root: Path, permit_city: dict[str, Any]) -> pd.DataFrame:
    record = permit_city["physical_overpasses"]
    path = helpers._inside(root, record["path"])
    if path.stat().st_size != record["bytes"] or sha256_file(path) != record["sha256"]:
        raise M3BlindTargetError("Physical-overpass inventory drifted.")
    frame = pd.read_parquet(path)
    selected = frame.loc[frame["primary_eligible"].astype(bool)].copy()
    if len(selected) != permit_city["eligible_date_count"]:
        raise M3BlindTargetError("Eligible target-date count changed.")
    return selected.sort_values(["local_date", "overpass_id"]).reset_index(drop=True)


def _process_city(root: Path, permit: dict[str, Any], city: dict[str, Any]) -> dict[str, Any]:
    city_id = city["city_id"]
    context = _context(root, city_id)
    config = candidate_config(load_config(root / "configs/research.toml"), "4k")
    config_sha = candidate_target_config_sha256(load_config(root / "configs/research.toml"), "4k")
    gate = _Gate(root, permit["commit_sha256"])
    hydrator = PlanetaryComputerSceneHydrator()
    inventory = _inventory(root, city)
    outputs = []
    for index, row in enumerate(inventory.itertuples(index=False), start=1):
        overpass_dir = helpers._inside(root, OUTPUT_ROOT / "overpasses" / city_id)
        marker_path = overpass_dir / f"{row.overpass_id}.json"
        table_path = overpass_dir / f"{row.overpass_id}.parquet"
        if marker_path.exists():
            marker = _read(marker_path)
        else:
            result = aggregate_authorized_overpass(
                scene_ids=tuple(str(row.scene_ids).split("|")),
                gate=gate,
                hydrator=hydrator,
                context=context,
                config=config,
                target_date=str(row.local_date),
                overpass_id=str(row.overpass_id),
                platform=str(row.platform),
                union_city_coverage_fraction=min(float(row.union_city_coverage_fraction), 1.0),
                target_config_sha256=config_sha,
                tract_manifest_sha256=city["feasibility_commit_sha256"],
            )
            table = result.tract_date_qa.copy()
            table.insert(0, "city_id", city_id)
            helpers._write_parquet_exclusive(table, table_path)
            record = helpers._record(root, table_path, rows=len(table))
            record["semantic_sha256"] = canonical_frame_sha256(
                table, sort_by=["city_id", "target_date", "tract_geoid"]
            )
            marker = _committed(
                {
                    "schema_version": 1,
                    "algorithm_version": ALGORITHM_VERSION,
                    "state": "m3_blind_target_overpass_complete",
                    "authorization_commit_sha256": permit["commit_sha256"],
                    "city_id": city_id,
                    "overpass_id": row.overpass_id,
                    "target_date": str(row.local_date),
                    "output": record,
                    "summary": result.summary,
                }
            )
            helpers._write_json_exclusive(marker, marker_path)
        outputs.append(marker)
        print(
            f"BLIND_TARGET_OVERPASS_COMPLETE {city_id} {index}/{len(inventory)}",
            flush=True,
        )
    frames = [pd.read_parquet(helpers._inside(root, item["output"]["path"])) for item in outputs]
    combined = pd.concat(frames, ignore_index=True).sort_values(
        ["city_id", "target_date", "tract_geoid"]
    )
    city_path = helpers._inside(root, OUTPUT_ROOT / "cities" / city_id / "targets_4k.parquet")
    helpers._write_parquet_exclusive(combined, city_path)
    record = helpers._record(root, city_path, rows=len(combined))
    record["semantic_sha256"] = canonical_frame_sha256(
        combined, sort_by=["city_id", "target_date", "tract_geoid"]
    )
    return {
        "city_id": city_id,
        "date_count": len(outputs),
        "usable_date_count": int(
            combined.loc[:, ["target_date", "date_usable"]].drop_duplicates()["date_usable"].sum()
        ),
        "target_available_rows": int(combined["target_available"].sum()),
        "output": record,
    }


def run(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    if helpers._inside(root, COMPLETION_PATH).exists():
        return authenticate_completion(root)
    marker = _open_claim(root, permit)
    cities = [_process_city(root, permit, city) for city in permit["cities"]]
    completion = _committed(
        {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "state": "m3_blind_targets_complete",
            "authorization_commit_sha256": permit["commit_sha256"],
            "values_opened_commit_sha256": marker["commit_sha256"],
            "prediction_completion_commit_sha256": PREDICTION_COMMIT,
            "qa_candidate_id": "4k",
            "city_count": 4,
            "date_count": sum(item["date_count"] for item in cities),
            "usable_date_count": sum(item["usable_date_count"] for item in cities),
            "target_available_rows": sum(item["target_available_rows"] for item in cities),
            "cities": cities,
            "audit": {
                "prediction_committed_before_target_values_opened": True,
                "retuning_after_target_access": False,
                "target_completion_authenticated_before_scoring": True,
            },
            "next_safe_stage": "authenticate_completion_then_run_frozen_evaluation",
        }
    )
    helpers._write_json_exclusive(completion, helpers._inside(root, COMPLETION_PATH))
    return authenticate_completion(root)


def authenticate_completion(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    permit = authenticate_authorization(root)
    payload = _read(helpers._inside(root, COMPLETION_PATH))
    if (
        payload.get("authorization_commit_sha256") != permit["commit_sha256"]
        or payload.get("date_count") != sum(EXPECTED_DATES.values())
        or payload.get("audit", {}).get("retuning_after_target_access") is not False
    ):
        raise M3BlindTargetError("Target completion changed.")
    for city in payload["cities"]:
        record = city["output"]
        path = helpers._inside(root, record["path"])
        frame = pd.read_parquet(path)
        if (
            city.get("date_count") != EXPECTED_DATES[city["city_id"]]
            or not REQUIRED_OUTPUT_COLUMNS.issubset(frame.columns)
            or frame["city_id"].nunique() != 1
            or frame["city_id"].iloc[0] != city["city_id"]
            or frame["target_date"].nunique() != city["date_count"]
            or int(frame["target_available"].sum()) != city["target_available_rows"]
            or not helpers._matches(
                root, record, OUTPUT_ROOT / "cities" / city["city_id"] / "targets_4k.parquet"
            )
            or canonical_frame_sha256(frame, sort_by=["city_id", "target_date", "tract_geoid"])
            != record["semantic_sha256"]
        ):
            raise M3BlindTargetError("Target output drifted.")
    return payload
