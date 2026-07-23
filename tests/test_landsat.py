import numpy as np

from la_heat.landsat import (
    landsat_st_dn_to_celsius,
    qa_pixel_clear_land_mask,
    zonal_mask_identity_hashes,
)


def test_landsat_temperature_scaling() -> None:
    # DN lands near 26.85 °C after the official scale and offset.
    result = landsat_st_dn_to_celsius(np.array([44177], dtype=np.uint16))
    assert np.isclose(result[0], 26.84786954, atol=1e-6)


def test_qa_mask_accepts_clear_land_and_rejects_cloud_water_fill() -> None:
    qa = np.array(
        [
            1 << 6,  # clear bit only
            (1 << 6) | (1 << 3),  # cloud
            (1 << 6) | (1 << 4),  # cloud shadow
            (1 << 6) | (1 << 7),  # water
            1 << 0,  # fill
        ],
        dtype=np.uint16,
    )
    assert qa_pixel_clear_land_mask(qa).tolist() == [True, False, False, False, False]


def test_zonal_mask_hash_changes_when_pixel_identity_changes() -> None:
    zones = np.array([[1, 1, 2], [1, 2, 2]], dtype=np.int16)
    first = zonal_mask_identity_hashes(
        zones,
        np.array([[True, False, True], [False, True, False]]),
        zone_count=2,
        grid_identity="grid-a",
    )
    second = zonal_mask_identity_hashes(
        zones,
        np.array([[False, True, True], [False, True, False]]),
        zone_count=2,
        grid_identity="grid-a",
    )
    assert first[0] != second[0]
    assert first[1] == second[1]
