"""Leakage-safe Sentinel-2 optical feature contracts and pure transformations.

This module deliberately has no knowledge of Landsat target values.  It converts
one frozen physical Sentinel-2 acquisition into tract summaries and then builds
target-date composites using only acquisitions whose Los Angeles civil date is
between ``d-60`` and ``d-1``.  Network I/O and cache orchestration live in
``sentinel_feature_builder`` so the scientific transformations are directly
testable with small arrays.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final

import numpy as np
import pandas as pd
from rasterio.enums import Resampling
from rasterio.warp import reproject

from la_heat.grid import FixedGrid
from la_heat.provenance import canonical_sha256
from la_heat.sentinel_inventory import processing_baseline_key

REFLECTANCE_BANDS: Final[tuple[str, ...]] = (
    "B02",
    "B03",
    "B04",
    "B08",
    "B8A",
    "B11",
    "B12",
)
INDEX_COLUMNS: Final[tuple[str, ...]] = (
    "sentinel_ndvi_lag60",
    "sentinel_evi_lag60",
    "sentinel_ndwi_lag60",
    "sentinel_ndbi_lag60",
    "sentinel_albedo_proxy_lag60",
)
SENTINEL_BAND_IDS: Final[dict[str, int]] = {
    "B01": 0,
    "B02": 1,
    "B03": 2,
    "B04": 3,
    "B05": 4,
    "B06": 5,
    "B07": 6,
    "B08": 7,
    "B8A": 8,
    "B09": 9,
    "B10": 10,
    "B11": 11,
    "B12": 12,
}
RAW_INDEX_NAMES: Final[dict[str, str]] = {
    "ndvi": INDEX_COLUMNS[0],
    "evi": INDEX_COLUMNS[1],
    "ndwi": INDEX_COLUMNS[2],
    "ndbi": INDEX_COLUMNS[3],
    "albedo_proxy": INDEX_COLUMNS[4],
}


@dataclass(frozen=True, slots=True)
class BoaCalibration:
    """Sentinel product-level BOA quantification and band offsets."""

    processing_baseline: str
    quantification_value: float
    offsets: tuple[tuple[str, float], ...]

    @property
    def offset_by_band(self) -> dict[str, float]:
        return dict(self.offsets)

    @property
    def sha256(self) -> str:
        return canonical_sha256(
            {
                "processing_baseline": self.processing_baseline,
                "quantification_value": self.quantification_value,
                "offsets": self.offsets,
            }
        )


@dataclass(frozen=True, slots=True)
class AlignedSentinelTile:
    """One frozen tile aligned to the common 20 m acquisition grid."""

    item_id: str
    mgrs_tile: str
    scl: np.ndarray
    reflectance: Mapping[str, np.ndarray]
    calibration_sha256: str


@dataclass(frozen=True, slots=True)
class AcquisitionMosaic:
    """Deterministic adjacent-tile mosaic for one physical acquisition."""

    scl: np.ndarray
    reflectance: Mapping[str, np.ndarray]
    owner_index: np.ndarray
    item_ids: tuple[str, ...]
    mgrs_tiles: tuple[str, ...]
    owned_pixel_counts: tuple[int, ...]
    calibration_sha256s: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompositeArtifacts:
    """Model features and separate audit-only tables."""

    features: pd.DataFrame
    audit: pd.DataFrame
    lineage: pd.DataFrame


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _unique_finite(values: Iterable[str], *, field: str) -> float:
    parsed: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Sentinel metadata has invalid {field}: {value!r}") from exc
        if not math.isfinite(number):
            raise ValueError(f"Sentinel metadata has non-finite {field}.")
        parsed.append(number)
    if not parsed:
        raise ValueError(f"Sentinel metadata lacks {field}.")
    if any(number != parsed[0] for number in parsed[1:]):
        raise ValueError(f"Sentinel metadata has conflicting {field} values.")
    return parsed[0]


def parse_boa_calibration(
    xml_content: bytes | str,
    *,
    processing_baseline: str,
    required_bands: Sequence[str] = REFLECTANCE_BANDS,
) -> BoaCalibration:
    """Parse BOA scaling metadata and fail closed on baseline >= 04 offsets.

    Sentinel-2 processing baseline 04.00 introduced a negative additive BOA
    offset.  Applying only ``DN / 10000`` to those products creates an artificial
    discontinuity, so every required offset must be present for baseline 04.00+
    and partial offset lists are rejected at any baseline.
    """

    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as exc:
        raise ValueError("Sentinel product metadata is not valid XML.") from exc
    baseline_key = processing_baseline_key(processing_baseline)
    requested = tuple(str(band).upper() for band in required_bands)
    unknown = sorted(set(requested) - set(SENTINEL_BAND_IDS))
    if unknown:
        raise ValueError(f"Unknown Sentinel bands requested: {unknown}")
    if len(set(requested)) != len(requested):
        raise ValueError("Required Sentinel bands must be unique.")

    quantification = _unique_finite(
        (
            element.text
            for element in root.iter()
            if _local_name(element.tag) == "BOA_QUANTIFICATION_VALUE"
            and element.text is not None
        ),
        field="BOA_QUANTIFICATION_VALUE",
    )
    if quantification <= 0:
        raise ValueError("BOA_QUANTIFICATION_VALUE must be positive.")

    metadata_baselines = {
        str(element.text).strip().lstrip("N")
        for element in root.iter()
        if _local_name(element.tag) == "PROCESSING_BASELINE" and element.text
    }
    if metadata_baselines:
        parsed_metadata_keys = {processing_baseline_key(value) for value in metadata_baselines}
        if parsed_metadata_keys != {baseline_key}:
            raise ValueError(
                "Product metadata processing baseline disagrees with the frozen inventory."
            )

    offsets_by_id: dict[int, float] = {}
    for element in root.iter():
        if _local_name(element.tag) != "BOA_ADD_OFFSET":
            continue
        band_id_text = element.attrib.get("band_id")
        if band_id_text is None or element.text is None:
            raise ValueError("BOA_ADD_OFFSET lacks band_id or value.")
        try:
            band_id = int(band_id_text)
            value = float(element.text)
        except ValueError as exc:
            raise ValueError("BOA_ADD_OFFSET has a non-numeric band_id or value.") from exc
        if not math.isfinite(value):
            raise ValueError("BOA_ADD_OFFSET must be finite.")
        if band_id in offsets_by_id and offsets_by_id[band_id] != value:
            raise ValueError(f"Conflicting BOA offsets for band_id={band_id}.")
        offsets_by_id[band_id] = value

    required_ids = {SENTINEL_BAND_IDS[band] for band in requested}
    missing_ids = required_ids - set(offsets_by_id)
    if baseline_key >= (4, 0) and missing_ids:
        missing_bands = sorted(
            band for band in requested if SENTINEL_BAND_IDS[band] in missing_ids
        )
        raise ValueError(
            "Processing baseline 04.00+ requires BOA offsets for every band; "
            f"missing {missing_bands}."
        )
    if offsets_by_id and missing_ids:
        raise ValueError("Sentinel metadata contains a partial BOA offset list.")
    offsets = tuple(
        (
            band,
            float(offsets_by_id.get(SENTINEL_BAND_IDS[band], 0.0)),
        )
        for band in requested
    )
    return BoaCalibration(
        processing_baseline=(f"{baseline_key[0]:02d}.{baseline_key[1]:02d}"),
        quantification_value=quantification,
        offsets=offsets,
    )


def decode_boa_reflectance(
    digital_number: np.ndarray,
    *,
    band: str,
    calibration: BoaCalibration,
    nodata_dn: int = 0,
    saturated_dn: int = 65535,
) -> np.ndarray:
    """Decode BOA reflectance without clipping physically possible negatives."""

    normalized_band = band.upper()
    offsets = calibration.offset_by_band
    if normalized_band not in offsets:
        raise ValueError(f"Calibration lacks required band {normalized_band}.")
    values = np.asarray(digital_number)
    if values.ndim != 2:
        raise ValueError("A Sentinel band must be a two-dimensional raster.")
    numeric = values.astype(np.float32, copy=False)
    valid = (
        np.isfinite(numeric)
        & (numeric != float(nodata_dn))
        & (numeric != float(saturated_dn))
    )
    decoded = np.full(values.shape, np.nan, dtype=np.float32)
    decoded[valid] = (
        numeric[valid] + np.float32(offsets[normalized_band])
    ) / np.float32(calibration.quantification_value)
    return decoded


def clear_land_mask(
    scl: np.ndarray,
    reflectance: Mapping[str, np.ndarray],
    *,
    accepted_scl_classes: Sequence[int] = (4, 5),
) -> np.ndarray:
    """Accept only SCL vegetation and bare/not-vegetated land with all bands valid."""

    classes = np.asarray(scl)
    if classes.ndim != 2:
        raise ValueError("SCL must be a two-dimensional raster.")
    accepted = tuple(int(value) for value in accepted_scl_classes)
    if set(accepted) != {4, 5}:
        raise ValueError("Primary Sentinel clear-land classes must be exactly SCL 4 and 5.")
    valid = np.isin(classes, accepted)
    for band in REFLECTANCE_BANDS:
        if band not in reflectance:
            raise ValueError(f"Reflectance mosaic lacks {band}.")
        values = np.asarray(reflectance[band])
        if values.shape != classes.shape:
            raise ValueError(f"Reflectance shape for {band} disagrees with SCL.")
        valid &= np.isfinite(values)
    return valid


def _safe_ratio(numerator: np.ndarray, denominator: np.ndarray, epsilon: float) -> np.ndarray:
    if not math.isfinite(epsilon) or epsilon <= 0:
        raise ValueError("Index denominator epsilon must be positive and finite.")
    output = np.full(numerator.shape, np.nan, dtype=np.float32)
    valid = (
        np.isfinite(numerator)
        & np.isfinite(denominator)
        & (np.abs(denominator) >= epsilon)
    )
    output[valid] = numerator[valid] / denominator[valid]
    return output


def compute_optical_indices(
    reflectance: Mapping[str, np.ndarray],
    *,
    denominator_epsilon: float,
    albedo_coefficients: Mapping[str, float],
) -> dict[str, np.ndarray]:
    """Calculate the five predeclared non-thermal Sentinel predictors."""

    arrays: dict[str, np.ndarray] = {}
    shape: tuple[int, ...] | None = None
    for band in REFLECTANCE_BANDS:
        if band not in reflectance:
            raise ValueError(f"Reflectance input lacks {band}.")
        array = np.asarray(reflectance[band], dtype=np.float32)
        if array.ndim != 2:
            raise ValueError(f"Reflectance input {band} must be two-dimensional.")
        if shape is None:
            shape = array.shape
        elif array.shape != shape:
            raise ValueError("All reflectance bands must share one grid.")
        arrays[band] = array

    coefficient_keys = {str(key).upper() for key in albedo_coefficients}
    expected_albedo = {"B02", "B03", "B04", "B08", "B11", "B12"}
    if coefficient_keys != expected_albedo:
        raise ValueError(
            "Albedo coefficients must be exactly B02, B03, B04, B08, B11, and B12."
        )
    coefficients = {
        str(key).upper(): float(value) for key, value in albedo_coefficients.items()
    }
    if not all(math.isfinite(value) for value in coefficients.values()):
        raise ValueError("Albedo coefficients must be finite.")
    if not math.isclose(sum(coefficients.values()), 1.0, abs_tol=1e-6):
        raise ValueError("Prespecified albedo coefficients must sum to one.")

    b02, b03, b04 = arrays["B02"], arrays["B03"], arrays["B04"]
    b08, b11 = arrays["B08"], arrays["B11"]
    indices = {
        "ndvi": _safe_ratio(b08 - b04, b08 + b04, denominator_epsilon),
        "evi": _safe_ratio(
            np.float32(2.5) * (b08 - b04),
            b08 + np.float32(6.0) * b04 - np.float32(7.5) * b02 + np.float32(1.0),
            denominator_epsilon,
        ),
        "ndwi": _safe_ratio(b03 - b08, b03 + b08, denominator_epsilon),
        "ndbi": _safe_ratio(b11 - b08, b11 + b08, denominator_epsilon),
    }
    albedo = np.zeros(shape, dtype=np.float32)
    valid_albedo = np.ones(shape, dtype=bool)
    for band, coefficient in coefficients.items():
        valid_albedo &= np.isfinite(arrays[band])
        albedo += np.float32(coefficient) * np.nan_to_num(arrays[band], nan=0.0)
    albedo[~valid_albedo] = np.nan
    indices["albedo_proxy"] = albedo
    return indices


def mosaic_aligned_tiles(tiles: Iterable[AlignedSentinelTile]) -> AcquisitionMosaic:
    """Mosaic adjacent MGRS contributors with a frozen QA/lexical ownership rule.

    Tiles are not observations.  In overlap regions, complete SCL-4/5 pixels are
    preferred, then complete non-NoData pixels, then any footprint pixel; exact
    ties use the lexicographically first ``(mgrs_tile, item_id)``.  One selected
    tile owns every asset at a pixel, preventing band-wise mixing.
    """

    ordered = tuple(sorted(tiles, key=lambda value: (value.mgrs_tile, value.item_id)))
    if not ordered:
        raise ValueError("A physical acquisition must contain at least one tile.")
    identities = [(tile.mgrs_tile, tile.item_id) for tile in ordered]
    if len(set(identities)) != len(identities):
        raise ValueError("A physical acquisition contains duplicate tile items.")
    shape = np.asarray(ordered[0].scl).shape
    if len(shape) != 2:
        raise ValueError("Aligned SCL rasters must be two-dimensional.")
    tile_scl: list[np.ndarray] = []
    tile_complete: list[np.ndarray] = []
    for tile in ordered:
        scl = np.asarray(tile.scl)
        if scl.shape != shape:
            raise ValueError("Aligned Sentinel tiles do not share one grid.")
        for band in REFLECTANCE_BANDS:
            if band not in tile.reflectance or np.asarray(tile.reflectance[band]).shape != shape:
                raise ValueError(f"Aligned tile {tile.item_id} lacks a matching {band} raster.")
        complete = np.ones(shape, dtype=bool)
        for band in REFLECTANCE_BANDS:
            complete &= np.isfinite(np.asarray(tile.reflectance[band]))
        tile_scl.append(scl)
        tile_complete.append(complete)

    owner = np.full(shape, -1, dtype=np.int16)
    candidate_tiers = (
        tuple(
            np.isin(scl, [4, 5]) & complete
            for scl, complete in zip(tile_scl, tile_complete, strict=True)
        ),
        tuple(
            (scl != 0) & complete
            for scl, complete in zip(tile_scl, tile_complete, strict=True)
        ),
        tuple(scl != 0 for scl in tile_scl),
    )
    for tier in candidate_tiers:
        for index, candidate in enumerate(tier):
            owner[(owner < 0) & candidate] = index

    scl_mosaic = np.zeros(shape, dtype=np.uint8)
    reflectance_mosaic = {
        band: np.full(shape, np.nan, dtype=np.float32) for band in REFLECTANCE_BANDS
    }
    owned_counts: list[int] = []
    for index, tile in enumerate(ordered):
        selected = owner == index
        owned_counts.append(int(selected.sum()))
        scl_mosaic[selected] = np.asarray(tile.scl, dtype=np.uint8)[selected]
        for band in REFLECTANCE_BANDS:
            reflectance_mosaic[band][selected] = np.asarray(
                tile.reflectance[band], dtype=np.float32
            )[selected]
    return AcquisitionMosaic(
        scl=scl_mosaic,
        reflectance=reflectance_mosaic,
        owner_index=owner,
        item_ids=tuple(tile.item_id for tile in ordered),
        mgrs_tiles=tuple(tile.mgrs_tile for tile in ordered),
        owned_pixel_counts=tuple(owned_counts),
        calibration_sha256s=tuple(tile.calibration_sha256 for tile in ordered),
    )


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    valid = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    if not valid.any():
        return math.nan
    selected_values = values[valid]
    selected_weights = weights[valid]
    order = np.argsort(selected_values, kind="stable")
    selected_values = selected_values[order]
    selected_weights = selected_weights[order]
    cutoff = selected_weights.sum() / 2.0
    index = int(np.searchsorted(np.cumsum(selected_weights), cutoff, side="left"))
    return float(selected_values[min(index, len(selected_values) - 1)])


def _average_to_grid(
    values: np.ndarray,
    valid: np.ndarray,
    *,
    source_grid: FixedGrid,
    target_grid: FixedGrid,
) -> tuple[np.ndarray, np.ndarray]:
    if values.shape != source_grid.shape or valid.shape != source_grid.shape:
        raise ValueError("Source arrays do not match the frozen optical grid.")
    valid_fraction = np.zeros(target_grid.shape, dtype=np.float32)
    numerator = np.zeros(target_grid.shape, dtype=np.float32)
    reproject(
        source=valid.astype(np.float32),
        destination=valid_fraction,
        src_transform=source_grid.transform,
        src_crs=source_grid.crs,
        dst_transform=target_grid.transform,
        dst_crs=target_grid.crs,
        resampling=Resampling.average,
        init_dest_nodata=True,
    )
    reproject(
        source=np.where(valid, values, 0.0).astype(np.float32),
        destination=numerator,
        src_transform=source_grid.transform,
        src_crs=source_grid.crs,
        dst_transform=target_grid.transform,
        dst_crs=target_grid.crs,
        resampling=Resampling.average,
        init_dest_nodata=True,
    )
    np.clip(valid_fraction, 0.0, 1.0, out=valid_fraction)
    averaged = np.full(target_grid.shape, np.nan, dtype=np.float32)
    np.divide(
        numerator,
        valid_fraction,
        out=averaged,
        where=valid_fraction > 0,
    )
    return averaged, valid_fraction


def aggregate_acquisition_to_tracts(
    *,
    physical_acquisition_id: str,
    acquisition_local_date: date | str,
    platform: str,
    processing_baseline: str,
    indices: Mapping[str, np.ndarray],
    base_valid_20m: np.ndarray,
    optical_grid: FixedGrid,
    target_grid: FixedGrid,
    zone_raster_30m: np.ndarray,
    eligible_land_30m: np.ndarray,
    tract_geoids: Sequence[str],
    expected_eligible_counts: Mapping[str, int],
    minimum_acquisition_coverage: float,
) -> pd.DataFrame:
    """Aggregate one acquisition using the invariant 30 m eligible denominator."""

    if not physical_acquisition_id:
        raise ValueError("Physical acquisition ID cannot be empty.")
    acquired = pd.Timestamp(acquisition_local_date)
    if pd.isna(acquired) or acquired.time() != pd.Timestamp(acquired.date()).time():
        raise ValueError("Acquisition local date must be a civil date.")
    if not 0 < minimum_acquisition_coverage <= 1:
        raise ValueError("Minimum acquisition coverage must be in (0, 1].")
    zones = np.asarray(zone_raster_30m)
    eligible = np.asarray(eligible_land_30m, dtype=bool)
    if zones.shape != target_grid.shape or eligible.shape != target_grid.shape:
        raise ValueError("Zone and eligible-land rasters must match the locked target grid.")
    geoids = tuple(str(value) for value in tract_geoids)
    if len(geoids) == 0 or len(set(geoids)) != len(geoids):
        raise ValueError("Tract GEOIDs must be non-empty and unique.")
    zone_ids = np.unique(zones[zones > 0])
    if not np.array_equal(zone_ids, np.arange(1, len(geoids) + 1)):
        raise ValueError("Zone raster must contain every tract exactly in 1-based row order.")
    observed_counts = np.bincount(zones[eligible], minlength=len(geoids) + 1)[1:]
    expected_counts = np.array([int(expected_eligible_counts[geoid]) for geoid in geoids])
    if np.any(expected_counts <= 0) or not np.array_equal(observed_counts, expected_counts):
        raise ValueError("Eligible-land denominator disagrees with the frozen target lock.")

    joint_valid = np.asarray(base_valid_20m, dtype=bool).copy()
    normalized_indices: dict[str, np.ndarray] = {}
    for raw_name in RAW_INDEX_NAMES:
        if raw_name not in indices:
            raise ValueError(f"Acquisition indices lack {raw_name}.")
        values = np.asarray(indices[raw_name], dtype=np.float32)
        if values.shape != optical_grid.shape:
            raise ValueError(f"Acquisition index {raw_name} has the wrong grid shape.")
        normalized_indices[raw_name] = values
        joint_valid &= np.isfinite(values)

    base_values = np.ones(optical_grid.shape, dtype=np.float32)
    _, joint_fraction = _average_to_grid(
        base_values,
        joint_valid,
        source_grid=optical_grid,
        target_grid=target_grid,
    )
    base_weights = np.where(eligible, joint_fraction, 0.0)
    coverage_numerator = np.bincount(
        zones.ravel(), weights=base_weights.ravel(), minlength=len(geoids) + 1
    )[1:]
    coverage = coverage_numerator / observed_counts

    downsampled: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for raw_name, values in normalized_indices.items():
        downsampled[raw_name] = _average_to_grid(
            values,
            joint_valid,
            source_grid=optical_grid,
            target_grid=target_grid,
        )

    records: list[dict[str, object]] = []
    for zone_index, geoid in enumerate(geoids, start=1):
        zone = (zones == zone_index) & eligible
        row: dict[str, object] = {
            "tract_geoid": geoid,
            "physical_acquisition_id": physical_acquisition_id,
            "acquisition_local_date": acquired.date().isoformat(),
            "platform": platform,
            "processing_baseline": processing_baseline,
            "eligible_pixel_count_static": int(observed_counts[zone_index - 1]),
            "valid_area_equivalent_pixels": float(coverage_numerator[zone_index - 1]),
            "acquisition_coverage_fraction": float(coverage[zone_index - 1]),
            "acquisition_qualifies_coverage": bool(
                coverage[zone_index - 1] >= minimum_acquisition_coverage
            ),
        }
        for raw_name, model_name in RAW_INDEX_NAMES.items():
            cell_values, cell_fraction = downsampled[raw_name]
            value = _weighted_median(cell_values[zone], cell_fraction[zone])
            row[model_name] = (
                value if row["acquisition_qualifies_coverage"] else math.nan
            )
            row[f"{model_name}_valid_area_fraction_audit_only"] = float(
                cell_fraction[zone].sum() / observed_counts[zone_index - 1]
            )
        records.append(row)
    output = pd.DataFrame.from_records(records)
    if output.duplicated(["tract_geoid", "physical_acquisition_id"]).any():
        raise AssertionError("Acquisition aggregation produced duplicate tract keys.")
    return output


def _civil_series(values: pd.Series, *, field: str) -> pd.Series:
    try:
        parsed = pd.to_datetime(values, format="%Y-%m-%d", errors="raise")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must contain ISO civil dates.") from exc
    if parsed.isna().any():
        raise ValueError(f"{field} contains missing dates.")
    return parsed


def build_previous_60_day_composites(
    acquisition_tract: pd.DataFrame,
    membership: pd.DataFrame,
    *,
    target_dates: Sequence[date | str],
    tract_geoids: Sequence[str],
    minimum_acquisition_coverage: float,
    minimum_acquisitions: int,
    final_test_year: int,
    unlock_final_test: bool,
) -> CompositeArtifacts:
    """Median-composite qualifying physical acquisitions in exact d-60:d-1 windows."""

    if not 0 < minimum_acquisition_coverage <= 1:
        raise ValueError("Minimum acquisition coverage must be in (0, 1].")
    if minimum_acquisitions < 1:
        raise ValueError("Minimum acquisitions must be positive.")
    required_acquisition = {
        "tract_geoid",
        "physical_acquisition_id",
        "acquisition_local_date",
        "acquisition_coverage_fraction",
        *INDEX_COLUMNS,
    }
    required_membership = {
        "target_date",
        "physical_acquisition_id",
        "acquisition_local_date",
        "lag_days",
    }
    if missing := required_acquisition - set(acquisition_tract):
        raise ValueError(f"Acquisition table lacks columns: {sorted(missing)}")
    if missing := required_membership - set(membership):
        raise ValueError(f"Membership table lacks columns: {sorted(missing)}")

    normalized_targets = pd.to_datetime(
        pd.Series([str(value) for value in target_dates]),
        format="%Y-%m-%d",
        errors="raise",
    )
    if normalized_targets.isna().any() or normalized_targets.duplicated().any():
        raise ValueError("Target dates must be non-missing and unique.")
    if (normalized_targets.dt.year >= final_test_year).any() and not unlock_final_test:
        raise PermissionError(f"Final-test year {final_test_year} remains locked.")
    geoids = tuple(str(value) for value in tract_geoids)
    if not geoids or len(set(geoids)) != len(geoids):
        raise ValueError("Tract GEOIDs must be non-empty and unique.")

    members = membership.copy()
    members["target_date"] = _civil_series(members["target_date"], field="target_date")
    members["acquisition_local_date"] = _civil_series(
        members["acquisition_local_date"], field="acquisition_local_date"
    )
    if members.duplicated(["target_date", "physical_acquisition_id"]).any():
        raise ValueError("Membership has duplicate target/acquisition keys.")
    if not set(members["target_date"]).issubset(set(normalized_targets)):
        raise ValueError("Membership contains an undeclared target date.")
    computed_lag = (members["target_date"] - members["acquisition_local_date"]).dt.days
    numeric_lag = pd.to_numeric(members["lag_days"], errors="raise")
    invalid_temporal = (computed_lag != numeric_lag) | ~computed_lag.between(1, 60)
    if invalid_temporal.any():
        examples = members.loc[
            invalid_temporal,
            ["target_date", "acquisition_local_date", "lag_days"],
        ].head()
        raise ValueError(f"Sentinel lineage violates exact d-60:d-1:\n{examples}")

    acquisition = acquisition_tract.copy()
    acquisition["tract_geoid"] = acquisition["tract_geoid"].astype(str)
    acquisition["acquisition_local_date"] = _civil_series(
        acquisition["acquisition_local_date"], field="acquisition_local_date"
    )
    if acquisition.duplicated(["tract_geoid", "physical_acquisition_id"]).any():
        raise ValueError("Acquisition table has duplicate tract/acquisition keys.")
    if not set(acquisition["tract_geoid"]).issubset(set(geoids)):
        raise ValueError("Acquisition table contains an undeclared tract GEOID.")
    membership_ids = set(members["physical_acquisition_id"].astype(str))
    acquisition["physical_acquisition_id"] = acquisition[
        "physical_acquisition_id"
    ].astype(str)
    for physical_id in membership_ids:
        acquisition_tracts = set(
            acquisition.loc[
                acquisition["physical_acquisition_id"] == physical_id, "tract_geoid"
            ]
        )
        if acquisition_tracts != set(geoids):
            raise ValueError(
                f"Frozen physical acquisition lacks the complete tract universe: {physical_id}"
            )
    numeric_coverage = pd.to_numeric(
        acquisition["acquisition_coverage_fraction"], errors="coerce"
    )
    if numeric_coverage.isna().any() or not numeric_coverage.between(0, 1).all():
        raise ValueError("Acquisition coverage fractions must be finite and in [0, 1].")

    lineage = members.merge(
        acquisition,
        on=["physical_acquisition_id", "acquisition_local_date"],
        how="left",
        validate="one_to_many",
        indicator=True,
    )
    if (lineage["_merge"] != "both").any():
        raise ValueError("At least one frozen acquisition lacks tract summaries.")
    lineage = lineage.drop(columns="_merge")
    complete_indices = lineage[list(INDEX_COLUMNS)].apply(
        lambda column: pd.to_numeric(column, errors="coerce")
    ).notna().all(axis=1)
    coverage = pd.to_numeric(
        lineage["acquisition_coverage_fraction"], errors="coerce"
    )
    lineage["included_in_composite"] = (
        coverage.ge(minimum_acquisition_coverage) & complete_indices
    )
    lineage["source_end_date"] = lineage["acquisition_local_date"]
    lineage["source_age_days_audit_only"] = (
        lineage["target_date"] - lineage["source_end_date"]
    ).dt.days
    if (lineage["source_end_date"] >= lineage["target_date"]).any():
        raise AssertionError("Sentinel lineage includes target-day or future data.")

    base = pd.MultiIndex.from_product(
        [sorted(normalized_targets.dt.date), geoids],
        names=["target_date", "tract_geoid"],
    ).to_frame(index=False)
    base["target_date"] = pd.to_datetime(base["target_date"])
    grouped = lineage.groupby(["target_date", "tract_geoid"], sort=False, observed=True)
    audit = grouped.agg(
        window_membership_count=("physical_acquisition_id", "size"),
        qualifying_acquisition_count=("included_in_composite", "sum"),
        minimum_lag_days=("lag_days", "min"),
        maximum_lag_days=("lag_days", "max"),
        median_acquisition_coverage=("acquisition_coverage_fraction", "median"),
        newest_source_end_date=("source_end_date", "max"),
        oldest_source_end_date=("source_end_date", "min"),
    ).reset_index()
    audit = base.merge(audit, on=["target_date", "tract_geoid"], how="left", validate="one_to_one")
    audit["window_membership_count"] = audit["window_membership_count"].fillna(0).astype(int)
    audit["qualifying_acquisition_count"] = (
        audit["qualifying_acquisition_count"].fillna(0).astype(int)
    )
    audit["sentinel_feature_available"] = (
        audit["qualifying_acquisition_count"] >= minimum_acquisitions
    )

    included = lineage.loc[lineage["included_in_composite"]]
    medians = (
        included.groupby(["target_date", "tract_geoid"], sort=False, observed=True)[
            list(INDEX_COLUMNS)
        ]
        .median()
        .reset_index()
    )
    features = base.merge(
        medians, on=["target_date", "tract_geoid"], how="left", validate="one_to_one"
    )
    available = audit.set_index(["target_date", "tract_geoid"])[
        "sentinel_feature_available"
    ]
    key = pd.MultiIndex.from_frame(features[["target_date", "tract_geoid"]])
    unavailable = ~available.reindex(key).to_numpy(dtype=bool)
    features.loc[unavailable, list(INDEX_COLUMNS)] = np.nan

    for frame in (features, audit, lineage):
        frame["target_date"] = pd.to_datetime(frame["target_date"]).dt.strftime("%Y-%m-%d")
    lineage["acquisition_local_date"] = pd.to_datetime(
        lineage["acquisition_local_date"]
    ).dt.strftime("%Y-%m-%d")
    lineage["source_end_date"] = pd.to_datetime(lineage["source_end_date"]).dt.strftime(
        "%Y-%m-%d"
    )
    if features.duplicated(["tract_geoid", "target_date"]).any():
        raise AssertionError("Sentinel composite produced duplicate model keys.")
    if len(features) != len(normalized_targets) * len(geoids):
        raise AssertionError("Sentinel composite failed to emit the full key grid.")
    return CompositeArtifacts(features=features, audit=audit, lineage=lineage)
