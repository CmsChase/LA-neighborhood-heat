import numpy as np
import pytest

from experiments.source_city_target_availability import (
    ExploratoryTargetError,
    _check_thermal_metadata,
    _target_config,
    summarize_target_support,
)
from la_heat.aligned_landsat import COVERAGE_KEY, decode_aligned_scene_arrays


def test_target_support_keeps_static_denominator_after_thermal_missing():
    zones = np.ones((1, 30), dtype=np.int32)
    eligible = np.ones_like(zones, dtype=bool)
    qa = np.ones_like(zones, dtype=bool)
    valid = np.zeros_like(zones, dtype=bool)
    valid[:, :20] = True
    temperature = np.full(zones.shape, 28.0)
    rows, counts = summarize_target_support(
        zones=zones,
        eligible=eligible,
        qa_mask=qa,
        valid=valid,
        st_c=temperature,
        static_counts=np.array([30]),
        footprint_fraction=np.array([1.0]),
    )
    assert rows[0]["eligible_land_pixels_static"] == 30
    assert rows[0]["thermal_valid_fraction_fixed_denominator"] == 20 / 30
    assert rows[0]["target_available_exploratory"] is True
    assert rows[0]["target_lst_c_development_only"] == 28.0
    assert counts["thermal_invalid_among_qa_pixels"] == 10
    valid[:, :3] = False
    rows, counts = summarize_target_support(
        zones=zones,
        eligible=eligible,
        qa_mask=qa,
        valid=valid,
        st_c=temperature,
        static_counts=np.array([30]),
        footprint_fraction=np.array([1.0]),
    )
    assert rows[0]["eligible_land_pixels_static"] == 30
    assert rows[0]["target_available_exploratory"] is False
    assert rows[0]["target_lst_c_development_only"] is None
    assert counts["exploratory_label_count"] == 0


def test_thermal_scale_metadata_must_match_locked_c2_l2_st_product():
    scene = "LC08_L2SP_033033_20201031_02_T1"
    band = {
        "unit": "kelvin",
        "scale": 0.00341802,
        "offset": 149.0,
        "nodata": 0,
        "data_type": "uint16",
        "spatial_resolution": 30,
    }
    item = {
        "id": scene,
        "properties": {"landsat:correction": "L2SP"},
        "assets": {"lwir11": {"raster:bands": [band]}},
    }
    assert _check_thermal_metadata(item)["scale"] == 0.00341802
    item["assets"]["lwir11"]["raster:bands"][0]["offset"] = 150.0
    with pytest.raises(ExploratoryTargetError, match="scale"):
        _check_thermal_metadata(item)


def test_existing_decoder_scales_st_dn_and_applies_fill_and_4k_mask():
    arrays = {
        "lwir11": np.array([[43000, 0, 43000]], dtype=np.uint16),
        "qa_pixel": np.zeros((1, 3), dtype=np.uint16),
        "qa_radsat": np.zeros((1, 3), dtype=np.uint16),
        "qa": np.array([[400, 400, 401]], dtype=np.int16),
        "cdist": np.full((1, 3), 100, dtype=np.int16),
        COVERAGE_KEY: np.ones((1, 3), dtype=bool),
    }
    scene = decode_aligned_scene_arrays(
        scene_id="synthetic", arrays=arrays, config=_target_config()
    )
    assert scene.lst_c[0, 0] == pytest.approx(43000 * 0.00341802 + 149 - 273.15)
    assert scene.valid.tolist() == [[True, False, False]]
