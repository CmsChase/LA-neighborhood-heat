"""Build the compact, read-only four-city Atlas display payload.

The exporter reads only the already-opened M3 blind-evaluation rows and the
frozen Census tract geometries.  It does not recompute any scientific metric.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import MultiPolygon, Polygon

CITIES = {
    "seattle_wa": ("Seattle", "SEA"),
    "denver_co": ("Denver", "DEN"),
    "atlanta_ga": ("Atlanta", "ATL"),
    "miami_fl": ("Miami", "MIA"),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text if text != "-0" else "0"


def _polygonal(geometry: object) -> Polygon | MultiPolygon:
    valid = shapely.make_valid(geometry)
    polygons = [part for part in shapely.get_parts(valid) if isinstance(part, Polygon)]
    if not polygons:
        raise ValueError("Tract geometry has no polygonal component.")
    return polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)


def _ring_path(
    coordinates: object,
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
    head, *tail = projected
    return "".join(
        [f"M{_number(head[0])},{_number(head[1])}"]
        + [f"L{_number(x)},{_number(y)}" for x, y in tail]
        + ["Z"]
    )


def _svg_path(
    geometry: Polygon | MultiPolygon,
    *,
    min_x: float,
    min_y: float,
    scale: float,
    offset_x: float,
    offset_y: float,
    height: float,
) -> str:
    polygons = [geometry] if isinstance(geometry, Polygon) else list(geometry.geoms)
    parts: list[str] = []
    for polygon in polygons:
        parts.append(
            _ring_path(
                polygon.exterior.coords,
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
                    interior.coords,
                    min_x=min_x,
                    min_y=min_y,
                    scale=scale,
                    offset_x=offset_x,
                    offset_y=offset_y,
                    height=height,
                )
            )
    return "".join(parts)


def _round(value: object, digits: int = 3) -> float:
    return round(float(value), digits)


def build_payload(root: Path) -> dict[str, object]:
    evaluation_path = (
        root / "data/processed/multicity/m3_blind_evaluation_v1/scored_rows.parquet"
    )
    metrics_path = (
        root / "data/processed/multicity/m3_blind_evaluation_v1/city_metrics.parquet"
    )
    rows = pd.read_parquet(evaluation_path)
    metrics = pd.read_parquet(metrics_path).set_index("city_id")
    cities: list[dict[str, object]] = []
    sources = [
        {
            "path": evaluation_path.relative_to(root).as_posix(),
            "sha256": _sha256(evaluation_path),
            "bytes": evaluation_path.stat().st_size,
        },
        {
            "path": metrics_path.relative_to(root).as_posix(),
            "sha256": _sha256(metrics_path),
            "bytes": metrics_path.stat().st_size,
        },
    ]

    for city_id, (name, code) in CITIES.items():
        geometry_path = (
            root
            / "data/raw/multicity/next_experiment_feasibility"
            / city_id
            / "census/primary_tracts.parquet"
        )
        geography = gpd.read_parquet(geometry_path)
        geography = geography.loc[geography["primary_included"].eq(True)].copy()  # noqa: E712
        geography["tract_geoid"] = geography["tract_geoid"].astype(str).str.zfill(11)
        geography["geometry"] = geography.geometry.map(_polygonal)
        geography["geometry"] = geography.geometry.simplify(25, preserve_topology=True)
        geography = geography.sort_values("tract_geoid").reset_index(drop=True)

        min_x, min_y, max_x, max_y = (float(value) for value in geography.total_bounds)
        width = 960.0
        height = 720.0
        padding = 34.0
        scale = min(
            (width - 2 * padding) / (max_x - min_x),
            (height - 2 * padding) / (max_y - min_y),
        )
        rendered_width = (max_x - min_x) * scale
        rendered_height = (max_y - min_y) * scale
        offset_x = (width - rendered_width) / 2
        offset_y = (height - rendered_height) / 2

        index_by_geoid = {
            geoid: index for index, geoid in enumerate(geography["tract_geoid"])
        }
        tracts = [
            {
                "id": row.tract_geoid,
                "name": f"Census tract {row.tract_basename}",
                "path": _svg_path(
                    row.geometry,
                    min_x=min_x,
                    min_y=min_y,
                    scale=scale,
                    offset_x=offset_x,
                    offset_y=offset_y,
                    height=height,
                ),
            }
            for row in geography.itertuples(index=False)
        ]

        city_rows = rows.loc[rows["city_id"].eq(city_id)].copy()
        city_rows["tract_geoid"] = city_rows["tract_geoid"].astype(str).str.zfill(11)
        city_rows["target_date"] = city_rows["target_date"].astype(str)
        dates: list[dict[str, object]] = []
        for target_date, date_rows in city_rows.groupby("target_date", sort=True):
            records = []
            for row in date_rows.sort_values("tract_geoid").itertuples(index=False):
                records.append(
                    [
                        index_by_geoid[row.tract_geoid],
                        _round(row.target_lst_c),
                        _round(row.b1_prediction_c),
                        _round(row.m3_prediction_c),
                        _round(row.m3_error_c),
                        _round(row.m3_lower_c),
                        _round(row.m3_upper_c),
                    ]
                )
            dates.append({"date": target_date, "records": records})

        default_date = max(dates, key=lambda item: (len(item["records"]), item["date"]))[
            "date"
        ]
        metric = metrics.loc[city_id]
        cities.append(
            {
                "id": city_id,
                "name": name,
                "code": code,
                "viewBox": [0, 0, int(width), int(height)],
                "tracts": tracts,
                "dates": dates,
                "defaultDate": default_date,
                "metrics": {
                    "dates": int(metric["date_count"]),
                    "rows": int(metric["row_count"]),
                    "blocks": int(metric["spatial_block_count"]),
                    "b1MaeC": _round(metric["b1_equal_date_mae_c"], 4),
                    "m3MaeC": _round(metric["m3_equal_date_mae_c"], 4),
                    "coveragePercent": _round(metric["m3_interval_coverage"] * 100, 2),
                    "medianSpearman": _round(metric["median_per_date_m3_spearman"], 3),
                },
            }
        )
        sources.append(
            {
                "path": geometry_path.relative_to(root).as_posix(),
                "sha256": _sha256(geometry_path),
                "bytes": geometry_path.stat().st_size,
            }
        )

    return {
        "schemaVersion": 1,
        "state": "opened-blind-evaluation-display-only",
        "title": "Four City Surface Heat Atlas",
        "displayRules": {
            "endpoint": "QA-filtered daytime Landsat land-surface temperature",
            "mapModes": ["observed", "m3", "b1", "error"],
            "geometrySimplificationMeters": 25,
            "displayValuePrecisionC": 0.001,
            "metricsRecomputed": False,
        },
        "cities": cities,
        "sources": sources,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("atlas/public/data/four-city-atlas.json"),
    )
    args = parser.parse_args()
    root = args.project_root.resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = build_payload(root)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"Wrote {output} ({output.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
