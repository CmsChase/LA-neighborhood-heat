"""One-time public geography and optical catalog screen; never request raster assets."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import geopandas as gpd
import requests
import shapely
from shapely.geometry import shape

from la_heat.inventory import SceneRecord, group_physical_overpasses
from la_heat.multicity import geography
from la_heat.multicity import source_footprints as footprints
from la_heat.multicity.config import CitySpec
from la_heat.provenance import canonical_sha256

ROOT = Path(__file__).resolve().parents[1]
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
CENSUS = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Census2020/MapServer"
)
MIRROR = "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services"
CITIES = (
    ("colorado_springs_co", "Colorado Springs", "08", "0816000", "America/Denver", "EPSG:32613"),
    ("charlotte_nc", "Charlotte", "37", "3712000", "America/New_York", "EPSG:32617"),
)


def catalog_query(client, city, boundary, year, *, sentinel=False):
    start = date(year, 3 if sentinel else 5, 1)
    end = date(year, 10, 31)
    fields = footprints.SENTINEL_FIELDS if sentinel else footprints.LANDSAT_FIELDS
    properties = footprints.SENTINEL_PROPERTIES if sentinel else footprints.LANDSAT_PROPERTIES
    collection = "sentinel-2-l2a" if sentinel else "landsat-c2-l2"
    query = (
        None
        if sentinel
        else {
            "platform": {"in": ["landsat-8", "landsat-9"]},
            "landsat:collection_category": {"eq": "T1"},
            "landsat:correction": {"eq": "L2SP"},
        }
    )
    features, _, provenance = footprints.fetch_public_stac_metadata(
        client,
        api=STAC,
        collection=collection,
        bbox_wgs84=tuple(float(x) for x in boundary.total_bounds),
        datetime_interval=footprints.local_date_interval_to_utc(start, end, city.timezone),
        fields=fields,
        properties=properties,
        page_limit=100,
        query=query,
    )
    table = footprints.build_optical_item_table(
        features,
        source="sentinel_mgrs" if sentinel else "landsat_wrs",
        collection=collection,
        expected_properties=properties,
        allowed_platforms=("sentinel-2a", "sentinel-2b", "sentinel-2c")
        if sentinel
        else ("landsat-8", "landsat-9"),
        local_start_date=start,
        local_end_date=end,
        timezone=city.timezone,
        city_boundary=boundary,
        analysis_crs="EPSG:5070",
    )
    return table, provenance


def screen_city(client, name, basename, state, geoid, zone, grid):
    city = CitySpec(
        name,
        basename,
        state,
        geoid,
        zone,
        grid,
        "metadata_screen_only",
        "sealed",
        Path("experiments/source_city_metadata_screen.py"),
    )
    census_client = geography._retrying_session()
    place_url = f"{MIRROR}/USA_Census_2020_Redistricting_Incorporated_Places/FeatureServer/0"
    tract_url = f"{MIRROR}/USA_Census_2020_Redistricting_Tracts/FeatureServer/0"
    place_src = geography.LayerCandidate(
        "Esri Census 2020 place mirror",
        place_url,
        "Esri (2020 Census mirror)",
        "pilot_mirror_not_protocol_frozen",
        "13ea1fb24ca14842bb265e6ec6ac1d46",
    )
    tract_src = geography.LayerCandidate(
        "Esri Census 2020 tract mirror",
        tract_url,
        "Esri (2020 Census mirror)",
        "pilot_mirror_not_protocol_frozen",
        "e3a7d2d3e5834b7eb6b1c2943141ced6",
    )
    place = geography.standardize_place(
        geography._download_place(census_client, place_src, city).frame, city
    )
    tract_raw = geography._download_tracts(
        census_client, tract_src, city, tuple(float(x) for x in place.total_bounds)
    )
    tracts = geography.standardize_tracts(tract_raw.frame, city)
    audit, selected = geography.select_city_tracts(
        place,
        tracts,
        city_id=name,
        analysis_crs="EPSG:5070",
        minimum_place_area_fraction=0.50,
        exclude_special_use_tracts=True,
    )
    boundary = place.to_crs("EPSG:4326")
    result = {
        "city_id": name,
        "place_geoid": geoid,
        "boundary_vintage": 2020,
        "official_place_layer": f"{CENSUS}/26",
        "official_tract_layer": f"{CENSUS}/6",
        "place_source": place_url,
        "tract_source": tract_url,
        "geometry_provider_note": (
            "2020 Census Esri pilot mirror; official TIGERweb endpoint failed TLS "
            "on this host; official geometry equivalence not yet independently checked"
        ),
        "place_bbox_wgs84": [float(x) for x in boundary.total_bounds],
        "place_geometry_sha256": canonical_sha256(
            shapely.to_wkb(shapely.normalize(boundary.geometry.iloc[0]), hex=True)
        ),
        "tract_query_bbox_candidates": len(tracts),
        "selected_tract_count": len(selected),
        "selected_tract_geoids": sorted(selected["tract_geoid"].astype(str).tolist()),
        "excluded_by_reason": {
            str(k): int(v)
            for k, v in audit.loc[~audit["primary_included"], "primary_exclusion_reason"]
            .value_counts()
            .items()
        },
        "years": {},
        "sentinel_catalog": {},
    }
    for year in range(2020, 2025):
        landsat, query = catalog_query(client, city, boundary, year)
        scenes = [
            SceneRecord(
                item_id=str(r.item_id),
                platform=str(r.platform),
                acquired_utc=datetime.fromisoformat(str(r.acquired_utc).replace("Z", "+00:00")),
                local_date=str(r.acquisition_local_date),
                wrs_path=str(r.wrs_path),
                wrs_row=str(r.wrs_row),
                cloud_cover_percent=float("nan"),
                city_coverage_fraction=float(r.city_overlap_fraction),
                geometry_wgs84=r.geometry,
                asset_hrefs={},
            )
            for r in landsat.to_crs("EPSG:4326").itertuples(index=False)
        ]
        overpasses = group_physical_overpasses(
            scenes,
            city_geometry_wgs84=boundary.geometry.union_all(),
            analysis_crs="EPSG:5070",
            maximum_time_gap_minutes=15,
        )
        candidates = [
            o
            for o in overpasses
            if o.union_city_coverage_fraction >= 0.98 and not o.ambiguous_local_date
        ]
        result["years"][str(year)] = {
            "raw_catalog_items": query["query_response_items"],
            "intersecting_t1_l2sp_scenes": len(landsat),
            "physical_overpasses_all_coverage": len(overpasses),
            "distinct_dates_all_coverage": len({o.local_date for o in overpasses}),
            "candidate_overpasses_98pct": len(candidates),
            "candidate_distinct_dates_98pct": len({o.local_date for o in candidates}),
            "candidate_dates": [o.local_date for o in candidates],
            "candidate_scene_ids": [list(o.scene_ids) for o in candidates],
            "query": query["query"],
            "page_count": query["page_count"],
        }
        sentinel, sentinel_query = catalog_query(client, city, boundary, year, sentinel=True)
        result["sentinel_catalog"][str(year)] = {
            "intersecting_l2a_items_march_october": len(sentinel),
            "distinct_dates_march_october": int(sentinel["acquisition_local_date"].nunique()),
            "platforms": sorted(set(sentinel["platform"].astype(str))),
            "query": sentinel_query["query"],
            "page_count": sentinel_query["page_count"],
        }
        print(
            f"{name} {year}: Landsat {len(landsat)} scenes -> "
            f"{len(candidates)} candidates; Sentinel {len(sentinel)} metadata",
            flush=True,
        )
    return result


def main():
    output = ROOT / "exports" / "SOURCE_CITY_METADATA_SCREEN" / "summary.json"
    client = requests.Session()
    client.headers["User-Agent"] = "la-neighborhood-heat/0.1 metadata-only screen"
    payload = {
        "queried_utc": datetime.now(UTC).isoformat(),
        "contract": (
            "2020 Census place/tract, >=0.5 tract overlap, no 98xxxx, "
            "Landsat-8/9 C2 L2 T1 L2SP May-Oct 2020-24, 15-minute WRS grouping, "
            ">=0.98 city coverage, Sentinel-2 L2A Mar-Oct catalog only"
        ),
        "no_asset_hrefs_or_science_pixels_requested": True,
        "cities": {},
    }
    for city in CITIES:
        try:
            payload["cities"][city[0]] = screen_city(client, *city)
        except Exception as exc:
            payload["cities"][city[0]] = {
                "incomplete": True,
                "error_type": type(exc).__name__,
                "error": str(exc)[:300],
            }
            print(f"{city[0]} incomplete: {type(exc).__name__}: {exc}", flush=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Evidence: {output}")


def asset_metadata_only():
    """Project named catalog asset subfields without requesting any href."""
    output = ROOT / "exports" / "SOURCE_CITY_METADATA_SCREEN" / "summary.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    client = requests.Session()
    required = ("lwir11", "qa_pixel", "qa_radsat", "qa", "cdist")
    for city in payload["cities"].values():
        if city.get("incomplete"):
            continue
        for year in city["years"].values():
            ids = sorted({item for group in year["candidate_scene_ids"] for item in group})
            found = {}
            for start in range(0, len(ids), 50):
                body = {
                    "collections": ["landsat-c2-l2"],
                    "ids": ids[start : start + 50],
                    "limit": 100,
                    "fields": {
                        "include": ["id"] + [f"assets.{key}.type" for key in required],
                        "exclude": ["links"],
                    },
                }
                response = client.post(f"{STAC}/search", json=body, timeout=45)
                response.raise_for_status()
                for feature in response.json()["features"]:
                    assets = feature.get("assets", {})
                    if any("href" in value for value in assets.values()):
                        raise ValueError("Catalog projection unexpectedly exposed an asset href")
                    found[feature["id"]] = sorted(assets)
            if set(found) != set(ids):
                raise ValueError("Asset-key projection omitted candidate scene IDs")
            year["asset_key_audit"] = {
                "queried_scene_count": len(ids),
                "required_asset_keys": list(required),
                "all_five_keys_present": sum(set(required).issubset(found[item]) for item in ids),
                "missing_by_key": {
                    key: sum(key not in found[item] for item in ids) for key in required
                },
                "projection": (
                    "id and only assets.{lwir11,qa_pixel,qa_radsat,qa,cdist}.type; no href"
                ),
            }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Asset-key metadata: {output}")


def other_catalog_metadata_only():
    output = ROOT / "exports" / "SOURCE_CITY_METADATA_SCREEN" / "summary.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    client = requests.Session()
    sentinel_keys = ("B02", "B03", "B04", "B08", "B8A", "B11", "B12", "SCL", "product-metadata")
    for city in payload["cities"].values():
        if city.get("incomplete"):
            continue
        bbox = city["place_bbox_wgs84"]
        body = {
            "collections": ["esa-worldcover"],
            "bbox": bbox,
            "limit": 100,
            "datetime": "2020-01-01T00:00:00Z/2020-12-31T23:59:59Z",
            "fields": {"include": ["id", "geometry"], "exclude": ["assets", "links"]},
        }
        response = client.post(f"{STAC}/search", json=body, timeout=45)
        response.raise_for_status()
        tiles = response.json()["features"]
        place_response = client.get(
            city["place_source"] + "/query",
            params={
                "where": f"GEOID='{city['place_geoid']}'",
                "outFields": "GEOID",
                "returnGeometry": "true",
                "outSR": "4326",
                "f": "geojson",
            },
            timeout=45,
        )
        place_response.raise_for_status()
        place_feature = place_response.json()["features"]
        if len(place_feature) != 1 or not tiles:
            raise ValueError("Place or WorldCover catalog geometry missing")
        place_geom = shape(place_feature[0]["geometry"])
        tile_union = shapely.union_all([shape(f["geometry"]) for f in tiles])
        coverage = gpd.GeoSeries([place_geom, tile_union], crs="EPSG:4326").to_crs("EPSG:5070")
        city["worldcover_2020_catalog"] = {
            "collection": "esa-worldcover",
            "item_ids": sorted(f["id"] for f in tiles),
            "all_v100": all("2020_v100" in f["id"] for f in tiles),
            "tile_union_place_geometry_coverage_fraction": float(
                coverage.iloc[0].intersection(coverage.iloc[1]).area / coverage.iloc[0].area
            ),
            "note": (
                "Catalog footprint coverage of 2020 Census mirror place; "
                "fixed eligible-land mask classes and no-data pixels not read"
            ),
        }
        for year_text, record in city["sentinel_catalog"].items():
            year = int(year_text)
            start = date(year, 3, 1)
            end = date(year, 10, 31)
            sbody = {
                "collections": ["sentinel-2-l2a"],
                "bbox": bbox,
                "limit": 100,
                "datetime": footprints.local_date_interval_to_utc(
                    start,
                    end,
                    "America/Denver"
                    if city["city_id"] == "colorado_springs_co"
                    else "America/New_York",
                ),
                "fields": {"include": ["id", "geometry"], "exclude": ["assets", "links"]},
            }
            response = client.post(f"{STAC}/search", json=sbody, timeout=45)
            response.raise_for_status()
            sample = response.json()["features"][0]["id"]
            keys_body = {
                "collections": ["sentinel-2-l2a"],
                "ids": [sample],
                "limit": 1,
                "fields": {
                    "include": ["id"] + [f"assets.{key}.type" for key in sentinel_keys],
                    "exclude": ["links"],
                },
            }
            response = client.post(f"{STAC}/search", json=keys_body, timeout=45)
            response.raise_for_status()
            assets = response.json()["features"][0].get("assets", {})
            if any("href" in value for value in assets.values()):
                raise ValueError("Sentinel projection unexpectedly exposed an asset href")
            record["sample_asset_keys"] = {
                "item_id": sample,
                "all_nine_required_keys_present": set(sentinel_keys).issubset(assets),
                "missing_keys": sorted(set(sentinel_keys) - set(assets)),
                "sample_only_no_full_qa_or_window_audit": True,
            }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Other catalog metadata: {output}")


def prepare_pilot_metadata():
    output = ROOT / "exports" / "SOURCE_CITY_METADATA_SCREEN" / "summary.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    client = requests.Session()
    for city in payload["cities"].values():
        if city.get("incomplete"):
            continue
        response = client.get(
            city["place_source"] + "/query",
            params={
                "where": f"GEOID='{city['place_geoid']}'",
                "outFields": "GEOID,AREALAND",
                "returnGeometry": "false",
                "f": "json",
            },
            timeout=30,
        )
        response.raise_for_status()
        features = response.json()["features"]
        if len(features) != 1:
            raise ValueError("Place area lookup did not return exactly one record")
        land_m2 = int(features[0]["attributes"]["AREALAND"])
        city["place_source_land_area_m2"] = land_m2
        pilot = []
        for year in city["years"].values():
            options = sorted(zip(year["candidate_dates"], year["candidate_scene_ids"], strict=True))
            for day, ids in (options[0], options[-1]):
                pilot.append({"local_date": day, "scene_ids": ids})
        city["qa_pilot_design_not_authorized"] = {
            "selection": (
                "first and last >=98% nonambiguous May-Oct physical overpass "
                "in each year; target/scene cloud cover never consulted"
            ),
            "physical_overpasses": len(pilot),
            "scenes": sum(len(record["scene_ids"]) for record in pilot),
            "date_scene_ids": pilot,
            "uncompressed_city_land_window_lower_order_bytes": {
                "four_30m_qa_16bit_each_x_10_overpasses": int(land_m2 / 900 * 8 * 10),
                "one_10m_worldcover_uint8_mask": int(land_m2 / 100),
            },
            "warning": (
                "Pixel payload estimate only; COG range request overhead, "
                "duplicate margins, compression and whole-scene downloads are not included"
            ),
        }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Frozen proposed pilot metadata: {output}")


if __name__ == "__main__":
    import sys

    if "--asset-keys-only" in sys.argv:
        asset_metadata_only()
    elif "--other-catalog-only" in sys.argv:
        other_catalog_metadata_only()
    elif "--prepare-pilot-only" in sys.argv:
        prepare_pilot_metadata()
    else:
        main()
