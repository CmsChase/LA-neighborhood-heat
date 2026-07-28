"""Build a compact, auditable display export for the project website."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import MultiPolygon, Polygon, box

ALGORITHM_VERSION = "website-display-export-v3"
DISPLAY_FILES = ("tracts.json", "evaluation-2025.json", "metrics.json")
FINAL_RELATIVE = Path("data/processed/final_test_2025/final_evaluation")
TRACT_RELATIVE = Path("data/interim/targets/primary_tract_manifest.parquet")
NEIGHBORHOOD_RELATIVE = Path(
    "data/raw/neighborhoods/mapping-la/la-county-neighborhoods-v6.geojson"
)
EVIDENCE_RELATIVE = Path("manifests/final_test_2025/evaluation/EVIDENCE_EXPORT.json")
MAPPING_LA_COMMIT = "5acc817cd8e9ef1800dc9641493e46efe7ce35b0"
MAPPING_LA_SHA256 = "ada200f59e0d2cd7e04a212eb5510cfe570765d68b7ff29d83b97cc5abeb6ead"
MAPPING_LA_SOURCE_URL = (
    "https://raw.githubusercontent.com/datadesk/mapping-la-data/"
    f"{MAPPING_LA_COMMIT}/geojson/la-county-neighborhoods-v6.geojson"
)
HERO_GRID_COLUMNS = 40
HERO_GRID_MIN_CELL_COVERAGE = 0.15
SOURCE_RELATIVES = (
    TRACT_RELATIVE,
    NEIGHBORHOOD_RELATIVE,
    FINAL_RELATIVE / "evaluation_rows.parquet",
    FINAL_RELATIVE / "model_metrics.csv",
    FINAL_RELATIVE / "per_date_metrics.csv",
    FINAL_RELATIVE / "protocol_gates.csv",
    FINAL_RELATIVE / "crossed_bootstrap.json",
    FINAL_RELATIVE / "hotspot_summary.csv",
    FINAL_RELATIVE / "sensor_summary.csv",
    FINAL_RELATIVE / "qa_missingness_summary.csv",
    EVIDENCE_RELATIVE,
)


class WebsiteExportError(RuntimeError):
    """Raised when display data cannot be authenticated or safely exported."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: object) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(encoded)
    temporary.replace(path)


def _safe_scalar(value: object) -> object:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(value).date().isoformat()
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if pd.isna(value):
        return None
    return str(value)


def _records(frame: pd.DataFrame) -> list[dict[str, object]]:
    return [
        {str(key): _safe_scalar(value) for key, value in row.items()}
        for row in frame.to_dict(orient="records")
    ]


def _format_coordinate(value: float) -> str:
    text = f"{value:.1f}"
    return text.rstrip("0").rstrip(".")


def _ring_path(
    coordinates: list[tuple[float, float]],
    *,
    min_x: float,
    min_y: float,
    scale: float,
    offset_x: float,
    offset_y: float,
    height: float,
) -> str:
    projected = [
        (
            offset_x + (float(x) - min_x) * scale,
            height - offset_y - (float(y) - min_y) * scale,
        )
        for x, y in coordinates
    ]
    if not projected:
        return ""
    head = projected[0]
    segments = [f"M{_format_coordinate(head[0])},{_format_coordinate(head[1])}"]
    segments.extend(
        f"L{_format_coordinate(x)},{_format_coordinate(y)}" for x, y in projected[1:]
    )
    segments.append("Z")
    return "".join(segments)


def geometry_svg_path(
    geometry: Polygon | MultiPolygon,
    *,
    min_x: float,
    min_y: float,
    scale: float,
    offset_x: float,
    offset_y: float,
    height: float,
) -> str:
    """Convert a projected Polygon or MultiPolygon to a compact SVG path."""
    polygons = [geometry] if isinstance(geometry, Polygon) else list(geometry.geoms)
    parts: list[str] = []
    for polygon in polygons:
        parts.append(
            _ring_path(
                list(polygon.exterior.coords),
                min_x=min_x,
                min_y=min_y,
                scale=scale,
                offset_x=offset_x,
                offset_y=offset_y,
                height=height,
            )
        )
        for interior in polygon.interiors:
            parts.append(
                _ring_path(
                    list(interior.coords),
                    min_x=min_x,
                    min_y=min_y,
                    scale=scale,
                    offset_x=offset_x,
                    offset_y=offset_y,
                    height=height,
                )
            )
    return "".join(parts)


def tract_display_name(name: object, namelsad: object) -> str:
    """Build a useful Census tract label from the authenticated TIGER fields."""
    tract_number = str(name or "").strip()
    tract_type = str(namelsad or "").strip() or "Census Tract"
    if tract_number:
        return f"{tract_type} {tract_number}"
    return tract_type


def _load_mapping_la_neighborhoods(
    path: Path,
    *,
    target_crs: object,
    enforce_source_hash: bool = True,
) -> gpd.GeoDataFrame:
    if enforce_source_hash and _sha256(path) != MAPPING_LA_SHA256:
        raise WebsiteExportError("The frozen Mapping L.A. neighborhood snapshot changed.")
    neighborhoods = gpd.read_file(path)
    required = {"name", "metadata", "geometry"}
    if not required.issubset(neighborhoods.columns):
        raise WebsiteExportError(
            "Mapping L.A. source is missing "
            f"{sorted(required - set(neighborhoods.columns))}."
        )
    if neighborhoods.crs is None:
        raise WebsiteExportError("Mapping L.A. neighborhood source has no CRS.")
    city_mask = neighborhoods["metadata"].map(
        lambda value: isinstance(value, dict) and value.get("city") == "los-angeles"
    )
    neighborhoods = neighborhoods.loc[city_mask, ["name", "geometry"]].copy()
    neighborhoods["name"] = neighborhoods["name"].astype("string").str.strip()
    if (
        len(neighborhoods) != 114
        or neighborhoods["name"].isna().any()
        or neighborhoods["name"].eq("").any()
        or neighborhoods["name"].duplicated().any()
    ):
        raise WebsiteExportError(
            "Expected exactly 114 uniquely named Mapping L.A. city neighborhoods."
        )
    if neighborhoods.geometry.is_empty.any() or not neighborhoods.geometry.is_valid.all():
        raise WebsiteExportError("Mapping L.A. source contains invalid or empty geometry.")
    return neighborhoods.to_crs(target_crs).sort_values("name", kind="stable").reset_index(
        drop=True
    )


def _assign_neighborhoods(
    tracts: gpd.GeoDataFrame,
    neighborhoods: gpd.GeoDataFrame,
) -> list[dict[str, object]]:
    """Assign human-readable Mapping L.A. labels by maximum covered area."""
    if tracts.crs is None or neighborhoods.crs is None or tracts.crs != neighborhoods.crs:
        raise WebsiteExportError("Tracts and neighborhoods must share one projected CRS.")
    if not tracts.crs.is_projected:
        raise WebsiteExportError("Neighborhood overlap must be computed in a projected CRS.")

    tract_surface = gpd.GeoDataFrame(
        {
            "tractIndex": np.arange(len(tracts), dtype=np.int64),
            "tractArea": tracts.geometry.area.to_numpy(),
        },
        geometry=tracts.geometry.to_numpy(),
        crs=tracts.crs,
    )
    neighborhood_surface = neighborhoods.rename(columns={"name": "neighborhood"})
    intersections = gpd.overlay(
        tract_surface,
        neighborhood_surface[["neighborhood", "geometry"]],
        how="intersection",
        keep_geom_type=False,
    )
    intersections["intersectionArea"] = intersections.geometry.area
    intersections = intersections.loc[intersections["intersectionArea"].gt(0)].copy()
    areas = (
        intersections.groupby(["tractIndex", "neighborhood"], as_index=False, sort=True)[
            "intersectionArea"
        ]
        .sum()
        .sort_values(
            ["tractIndex", "intersectionArea", "neighborhood"],
            ascending=[True, False, True],
            kind="stable",
        )
    )
    covered_sum = areas.groupby("tractIndex", sort=True)["intersectionArea"].sum()
    covered_union = intersections[["tractIndex", "geometry"]].dissolve(
        by="tractIndex",
        sort=True,
    ).geometry.area
    tract_areas = tract_surface.set_index("tractIndex")["tractArea"]
    if len(covered_union) != len(tracts):
        missing = sorted(set(range(len(tracts))) - set(covered_union.index))
        raise WebsiteExportError(
            f"Mapping L.A. labels did not cover every tract; missing {missing[:5]}."
        )
    coverage = covered_union / tract_areas
    if coverage.le(0).any() or coverage.gt(1.00001).any():
        raise WebsiteExportError("Mapping L.A. tract coverage is outside the valid range.")

    assignments: list[dict[str, object]] = []
    for tract_index in range(len(tracts)):
        rows = areas.loc[areas["tractIndex"].eq(tract_index)].copy()
        covered_area = float(covered_sum.loc[tract_index])
        rows["coveredShare"] = rows["intersectionArea"] / covered_area
        overlaps = [
            [str(row.neighborhood), round(float(row.coveredShare), 4)]
            for row in rows.itertuples(index=False)
        ]
        assignments.append(
            {
                "neighborhood": overlaps[0][0],
                "neighborhoodShare": overlaps[0][1],
                "neighborhoodCoverage": round(float(coverage.loc[tract_index]), 4),
                "neighborhoods": overlaps,
            }
        )
    return assignments


def _build_hero_pixel_grid(tracts: gpd.GeoDataFrame) -> dict[str, object]:
    """Rasterize the tract surface into equal display squares for the homepage."""
    min_x, min_y, max_x, max_y = [float(value) for value in tracts.total_bounds]
    cell_size = (max_x - min_x) / HERO_GRID_COLUMNS
    rows = int(math.ceil((max_y - min_y) / cell_size))
    grid_records: list[dict[str, object]] = []
    for row in range(rows):
        top = max_y - row * cell_size
        for column in range(HERO_GRID_COLUMNS):
            left = min_x + column * cell_size
            grid_records.append(
                {
                    "column": column,
                    "row": row,
                    "geometry": box(
                        left,
                        top - cell_size,
                        left + cell_size,
                        top,
                    ),
                }
            )
    grid = gpd.GeoDataFrame(grid_records, geometry="geometry", crs=tracts.crs)
    tract_surface = gpd.GeoDataFrame(
        {"tractIndex": np.arange(len(tracts), dtype=np.int64)},
        geometry=tracts.geometry.to_numpy(),
        crs=tracts.crs,
    )
    intersections = gpd.overlay(
        grid,
        tract_surface,
        how="intersection",
        keep_geom_type=False,
    )
    intersections["intersectionArea"] = intersections.geometry.area
    overlaps = intersections.groupby(
        ["column", "row", "tractIndex"], as_index=False, sort=True
    )["intersectionArea"].sum()
    selected = (
        overlaps.sort_values(
            ["column", "row", "intersectionArea", "tractIndex"],
            ascending=[True, True, False, True],
            kind="stable",
        )
        .drop_duplicates(["column", "row"], keep="first")
        .copy()
    )
    selected["cellCoverage"] = selected["intersectionArea"] / (cell_size * cell_size)
    selected = selected.loc[
        selected["cellCoverage"].ge(HERO_GRID_MIN_CELL_COVERAGE)
    ].sort_values(["row", "column"], kind="stable")
    cells = [
        [int(row.column), int(row.row), int(row.tractIndex)]
        for row in selected.itertuples(index=False)
    ]
    if not cells:
        raise WebsiteExportError("Homepage pixel grid did not contain any LA cells.")
    return {
        "columns": HERO_GRID_COLUMNS,
        "rows": rows,
        "pixelCount": len(cells),
        "cells": cells,
    }


def _build_tracts(
    tract_path: Path,
    neighborhood_path: Path,
) -> tuple[dict[str, object], list[str]]:
    tracts = gpd.read_parquet(tract_path)
    required = {"GEOID", "spatial_block", "geometry"}
    if not required.issubset(tracts.columns):
        raise WebsiteExportError(f"Tract manifest is missing {sorted(required - set(tracts))}.")
    if tracts.crs is None:
        raise WebsiteExportError("Tract manifest has no authenticated CRS.")
    if not tracts.crs.is_projected:
        raise WebsiteExportError("Tract geometry must use a projected metric CRS.")
    tracts = tracts.copy()
    tracts["GEOID"] = tracts["GEOID"].astype("string")
    if not tracts["GEOID"].str.fullmatch(r"\d{11}").all():
        raise WebsiteExportError("Every tract GEOID must be an 11-digit string.")
    if tracts["GEOID"].duplicated().any() or len(tracts) != 1096:
        raise WebsiteExportError("Expected exactly 1,096 unique census tracts.")
    if not tracts.geometry.is_valid.all():
        raise WebsiteExportError("Tract manifest contains invalid geometry.")
    tracts = tracts.sort_values("GEOID", kind="stable").reset_index(drop=True)
    neighborhoods = _load_mapping_la_neighborhoods(
        neighborhood_path,
        target_crs=tracts.crs,
    )
    assignments = _assign_neighborhoods(tracts, neighborhoods)
    pixel_grid = _build_hero_pixel_grid(tracts)
    display_tracts = tracts.copy()
    display_tracts.geometry = display_tracts.geometry.simplify(
        10.0,
        preserve_topology=True,
    )

    min_x, min_y, max_x, max_y = [float(value) for value in display_tracts.total_bounds]
    width, height, margin = 620.0, 760.0, 12.0
    scale = min(
        (width - 2 * margin) / (max_x - min_x),
        (height - 2 * margin) / (max_y - min_y),
    )
    drawn_width = (max_x - min_x) * scale
    drawn_height = (max_y - min_y) * scale
    offset_x = margin + (width - 2 * margin - drawn_width) / 2
    offset_y = margin + (height - 2 * margin - drawn_height) / 2

    features: list[dict[str, object]] = []
    for index, row in enumerate(display_tracts.itertuples(index=False)):
        geometry = row.geometry
        if not isinstance(geometry, (Polygon, MultiPolygon)):
            raise WebsiteExportError(f"Unsupported tract geometry type: {geometry.geom_type}")
        features.append(
            {
                "id": str(row.GEOID),
                "name": tract_display_name(
                    getattr(row, "NAME", ""),
                    getattr(row, "NAMELSAD", ""),
                ),
                "block": str(row.spatial_block),
                **assignments[index],
                "path": geometry_svg_path(
                    geometry,
                    min_x=min_x,
                    min_y=min_y,
                    scale=scale,
                    offset_x=offset_x,
                    offset_y=offset_y,
                    height=height,
                ),
            }
        )

    payload = {
        "schemaVersion": 2,
        "projection": str(tracts.crs),
        "simplificationMeters": 10,
        "viewBox": [0, 0, int(width), int(height)],
        "tractCount": len(features),
        "neighborhoodCount": len(neighborhoods),
        "neighborhoodSource": {
            "name": "Los Angeles Times Mapping L.A.",
            "repositoryCommit": MAPPING_LA_COMMIT,
            "sourceSha256": MAPPING_LA_SHA256,
            "assignment": "maximum-area overlap on covered tract area",
        },
        "pixelGrid": pixel_grid,
        "tracts": features,
    }
    return payload, tracts["GEOID"].astype(str).tolist()


def _build_evaluation(
    evaluation_path: Path,
    tract_ids: list[str],
) -> dict[str, object]:
    frame = pd.read_parquet(evaluation_path)
    required = {
        "tract_geoid",
        "target_date",
        "sensor",
        "y_true",
        "y_pred_b1",
        "y_pred_m2",
        "valid_fraction",
        "median_st_uncertainty_k",
        "sentinel_available",
    }
    if not required.issubset(frame.columns):
        raise WebsiteExportError(
            f"Evaluation rows are missing {sorted(required - set(frame.columns))}."
        )
    frame = frame.copy()
    frame["tract_geoid"] = frame["tract_geoid"].astype("string")
    frame["target_date"] = pd.to_datetime(frame["target_date"]).dt.date.astype(str)
    if len(frame) != 15116 or frame["target_date"].nunique() != 15:
        raise WebsiteExportError("Expected 15,116 rows across 15 formal evaluation dates.")
    if frame[["tract_geoid", "target_date"]].duplicated().any():
        raise WebsiteExportError("Evaluation tract-date keys are not unique.")
    if not np.isfinite(frame[["y_true", "y_pred_b1", "y_pred_m2"]].to_numpy()).all():
        raise WebsiteExportError("Evaluation display values must be finite.")

    tract_index = {geoid: index for index, geoid in enumerate(tract_ids)}
    if not set(frame["tract_geoid"]).issubset(tract_index):
        raise WebsiteExportError("Evaluation contains a GEOID outside the frozen tract universe.")

    dates: list[dict[str, object]] = []
    for target_date, date_frame in frame.groupby("target_date", sort=True):
        date_frame = date_frame.sort_values("tract_geoid", kind="stable")
        sensors = sorted(date_frame["sensor"].astype(str).unique())
        if len(sensors) != 1:
            raise WebsiteExportError(f"{target_date} does not have exactly one sensor.")
        rows = [
            [
                tract_index[str(row.tract_geoid)],
                round(float(row.y_true), 3),
                round(float(row.y_pred_b1), 3),
                round(float(row.y_pred_m2), 3),
                round(float(row.valid_fraction), 3),
                round(float(row.median_st_uncertainty_k), 3),
                1 if bool(row.sentinel_available) else 0,
            ]
            for row in date_frame.itertuples(index=False)
        ]
        dates.append(
            {
                "date": str(target_date),
                "sensor": sensors[0],
                "rowCount": len(rows),
                "records": rows,
            }
        )

    return {
        "schemaVersion": 1,
        "recordFields": [
            "tractIndex",
            "observedLstC",
            "b1PredictedLstC",
            "m2PredictedLstC",
            "validFraction",
            "medianStUncertaintyK",
            "sentinelAvailable",
        ],
        "tractCount": len(tract_ids),
        "evaluationRowCount": len(frame),
        "independentDateCount": len(dates),
        "defaultDate": "2025-09-03",
        "dates": dates,
    }


def _build_metrics(final_dir: Path) -> dict[str, object]:
    model = pd.read_csv(final_dir / "model_metrics.csv", dtype={"model_id": "string"})
    per_date = pd.read_csv(
        final_dir / "per_date_metrics.csv",
        dtype={"model_id": "string", "target_date": "string"},
    )
    gates = pd.read_csv(final_dir / "protocol_gates.csv")
    hotspot = pd.read_csv(final_dir / "hotspot_summary.csv", dtype={"model_id": "string"})
    sensors = pd.read_csv(
        final_dir / "sensor_summary.csv",
        dtype={"model_id": "string", "sensor": "string"},
    )
    qa = pd.read_csv(
        final_dir / "qa_missingness_summary.csv",
        dtype={"target_date": "string", "sensor": "string"},
    )
    bootstrap = json.loads((final_dir / "crossed_bootstrap.json").read_text("utf-8"))

    date_audit_columns = [
        "target_date",
        "sensor",
        "date_usable",
        "evaluation_cohort_count",
        "date_exclusion_reason",
        "retained_tract_fraction",
        "relative_endpoint_coverage_pass",
    ]
    date_audit = qa.loc[qa["summary_level"].eq("date"), date_audit_columns].copy()
    return {
        "schemaVersion": 1,
        "modelMetrics": _records(model),
        "perDateMetrics": _records(per_date),
        "protocolGates": _records(gates),
        "bootstrap": bootstrap,
        "hotspotSummary": _records(hotspot),
        "sensorSummary": _records(sensors),
        "dateAudit": _records(date_audit),
    }


def build_website_export(project_root: Path, output_directory: Path) -> dict[str, object]:
    """Build and authenticate the compact website display export."""
    project_root = project_root.resolve()
    output_directory = output_directory.resolve()
    for relative in SOURCE_RELATIVES:
        if not (project_root / relative).is_file():
            raise WebsiteExportError(f"Required source is missing: {relative.as_posix()}")

    tracts, tract_ids = _build_tracts(
        project_root / TRACT_RELATIVE,
        project_root / NEIGHBORHOOD_RELATIVE,
    )
    evaluation = _build_evaluation(
        project_root / FINAL_RELATIVE / "evaluation_rows.parquet",
        tract_ids,
    )
    metrics = _build_metrics(project_root / FINAL_RELATIVE)
    evidence = json.loads((project_root / EVIDENCE_RELATIVE).read_text("utf-8"))
    if not evidence.get("verified"):
        raise WebsiteExportError("The read-only final-evaluation evidence is not verified.")

    output_directory.mkdir(parents=True, exist_ok=True)
    payloads = {
        "tracts.json": tracts,
        "evaluation-2025.json": evaluation,
        "metrics.json": metrics,
    }
    for name, payload in payloads.items():
        _write_json(output_directory / name, payload)

    sources = [
        {
            "path": relative.as_posix(),
            "bytes": (project_root / relative).stat().st_size,
            "sha256": _sha256(project_root / relative),
        }
        for relative in SOURCE_RELATIVES
    ]
    outputs = [
        {
            "path": name,
            "bytes": (output_directory / name).stat().st_size,
            "sha256": _sha256(output_directory / name),
        }
        for name in DISPLAY_FILES
    ]
    manifest = {
        "schemaVersion": 1,
        "algorithmVersion": ALGORITHM_VERSION,
        "state": "verified-display-export",
        "scientificIdentity": {
            "claimId": evidence["claim_id"],
            "completionCommitSha256": evidence["completion_commit_sha256"],
            "evidenceZipSha256": evidence["zip_sha256"],
            "packageRepositoryGitHead": evidence["package_repository_git_head"],
        },
        "displayRules": {
            "endpoint": "QA-filtered daytime Landsat LST",
            "analysis": "2025 held-out historical hindcast",
            "geometrySimplificationMeters": 10,
            "displayValuePrecisionC": 0.001,
            "temperatureColorDomainC": [28, 56],
            "residualColorDomainC": [-8, 8],
            "metricsRecomputedFromRoundedDisplayValues": False,
            "neighborhoodLabelsAreDisplayOnly": True,
            "neighborhoodAssignment": "maximum-area overlap on covered tract area",
            "heroPixelAssignment": "maximum tract overlap per equal display cell",
            "heroPixelMinimumCellCoverage": HERO_GRID_MIN_CELL_COVERAGE,
        },
        "counts": {
            "tracts": tracts["tractCount"],
            "evaluationRows": evaluation["evaluationRowCount"],
            "independentDates": evaluation["independentDateCount"],
            "neighborhoods": tracts["neighborhoodCount"],
            "heroPixels": tracts["pixelGrid"]["pixelCount"],
        },
        "sources": sources,
        "outputs": outputs,
    }
    _write_json(output_directory / "display-manifest.json", manifest)
    verify_website_export(project_root, output_directory)
    return manifest


def verify_website_export(project_root: Path, output_directory: Path) -> None:
    """Verify source and output hashes recorded by a display export."""
    project_root = project_root.resolve()
    output_directory = output_directory.resolve()
    manifest_path = output_directory / "display-manifest.json"
    if not manifest_path.is_file():
        raise WebsiteExportError("Display manifest is missing.")
    manifest = json.loads(manifest_path.read_text("utf-8"))
    if manifest.get("state") != "verified-display-export":
        raise WebsiteExportError("Display manifest does not report a verified state.")
    if manifest.get("algorithmVersion") != ALGORITHM_VERSION:
        raise WebsiteExportError("Display manifest algorithm version is not recognized.")

    source_items = manifest.get("sources")
    output_items = manifest.get("outputs")
    if not isinstance(source_items, list) or not isinstance(output_items, list):
        raise WebsiteExportError("Display manifest inventories are malformed.")
    if not all(isinstance(item, dict) for item in [*source_items, *output_items]):
        raise WebsiteExportError("Display manifest inventory entries are malformed.")
    source_paths = [str(item.get("path", "")) for item in source_items]
    output_paths = [str(item.get("path", "")) for item in output_items]
    expected_sources = [relative.as_posix() for relative in SOURCE_RELATIVES]
    if len(source_paths) != len(set(source_paths)) or set(source_paths) != set(
        expected_sources
    ):
        raise WebsiteExportError("Display manifest source inventory is not exact.")
    if len(output_paths) != len(set(output_paths)) or set(output_paths) != set(
        DISPLAY_FILES
    ):
        raise WebsiteExportError("Display manifest output inventory is not exact.")

    counts = manifest.get("counts")
    if counts != {
        "tracts": 1096,
        "evaluationRows": 15116,
        "independentDates": 15,
        "neighborhoods": 114,
        "heroPixels": 869,
    }:
        raise WebsiteExportError("Display manifest counts do not match the frozen cohort.")
    rules = manifest.get("displayRules")
    if not isinstance(rules, dict) or rules.get(
        "metricsRecomputedFromRoundedDisplayValues"
    ) is not False:
        raise WebsiteExportError("Display manifest metric-display rule is not frozen.")

    evidence_path = project_root / EVIDENCE_RELATIVE
    if not evidence_path.is_file():
        raise WebsiteExportError("Display evidence attestation is missing.")
    evidence = json.loads(evidence_path.read_text("utf-8"))
    expected_identity = {
        "claimId": evidence.get("claim_id"),
        "completionCommitSha256": evidence.get("completion_commit_sha256"),
        "evidenceZipSha256": evidence.get("zip_sha256"),
        "packageRepositoryGitHead": evidence.get("package_repository_git_head"),
    }
    if not evidence.get("verified") or manifest.get("scientificIdentity") != expected_identity:
        raise WebsiteExportError("Display manifest scientific identity is not authenticated.")

    for item in source_items:
        source = project_root / str(item["path"])
        if not source.is_file() or source.stat().st_size != int(item["bytes"]):
            raise WebsiteExportError(f"Display source changed: {item['path']}")
        if _sha256(source) != item["sha256"]:
            raise WebsiteExportError(f"Display source hash changed: {item['path']}")
    for item in output_items:
        output = output_directory / str(item["path"])
        if not output.is_file() or output.stat().st_size != int(item["bytes"]):
            raise WebsiteExportError(f"Display output changed: {item['path']}")
        if _sha256(output) != item["sha256"]:
            raise WebsiteExportError(f"Display output hash changed: {item['path']}")
