"""Landsat Collection 2 Level-2 temperature and QA utilities."""

from __future__ import annotations

import hashlib

import numpy as np

DEFAULT_ST_SCALE_KELVIN = 0.00341802
DEFAULT_ST_OFFSET_KELVIN = 149.0
KELVIN_TO_CELSIUS = 273.15


def qa_pixel_clear_land_mask(
    qa_pixel: np.ndarray,
    *,
    excluded_bits: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 7),
) -> np.ndarray:
    """Return valid clear-land pixels from Landsat 8/9 QA_PIXEL bit flags.

    Defaults exclude fill, dilated cloud, cirrus, cloud, cloud shadow, snow,
    and water. Unsigned integer QA values are required.
    """

    qa = np.asarray(qa_pixel)
    if not np.issubdtype(qa.dtype, np.integer):
        raise TypeError("QA_PIXEL must contain integer bit flags.")
    rejected_mask = sum(1 << bit for bit in excluded_bits)
    return (qa.astype(np.uint32) & rejected_mask) == 0


def landsat_st_dn_to_celsius(
    digital_number: np.ndarray,
    *,
    scale_kelvin: float = DEFAULT_ST_SCALE_KELVIN,
    offset_kelvin: float = DEFAULT_ST_OFFSET_KELVIN,
) -> np.ndarray:
    """Convert Landsat Collection 2 Level-2 ST digital numbers to °C."""

    dn = np.asarray(digital_number, dtype=np.float64)
    return dn * scale_kelvin + offset_kelvin - KELVIN_TO_CELSIUS


def physically_plausible_lst_mask(
    lst_celsius: np.ndarray,
    *,
    minimum_celsius: float = -30.0,
    maximum_celsius: float = 80.0,
) -> np.ndarray:
    """Flag broad physical plausibility; this is a diagnostic, not a QA substitute."""

    values = np.asarray(lst_celsius, dtype=np.float64)
    return np.isfinite(values) & (values >= minimum_celsius) & (values <= maximum_celsius)


def zonal_mask_identity_hashes(
    zones: np.ndarray,
    mask: np.ndarray,
    *,
    zone_count: int,
    grid_identity: str,
) -> list[str]:
    """Hash each zone's exact selected flat-pixel identities on a named grid."""

    zone_values = np.asarray(zones)
    selected = np.asarray(mask)
    if zone_values.shape != selected.shape or selected.dtype != np.bool_:
        raise ValueError("Zone raster and boolean mask must have the same shape.")
    flat_indices = np.flatnonzero(selected).astype("<i8", copy=False)
    selected_zones = zone_values.ravel()[flat_indices]
    if np.any((selected_zones < 1) | (selected_zones > zone_count)):
        raise ValueError("Selected pixels contain zone IDs outside 1..zone_count.")
    order = np.argsort(selected_zones, kind="stable")
    sorted_indices = flat_indices[order]
    counts = np.bincount(selected_zones, minlength=zone_count + 1)[1:]
    offsets = np.concatenate(([0], np.cumsum(counts)))
    prefix = grid_identity.encode("utf-8") + b"\0"
    return [
        hashlib.sha256(
            prefix + sorted_indices[offsets[index] : offsets[index + 1]].tobytes()
        ).hexdigest()
        for index in range(zone_count)
    ]
