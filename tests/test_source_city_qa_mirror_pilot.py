import numpy as np

from experiments.source_city_qa_mirror_pilot import qa_stages, summarize_date


def test_qa_stages_are_nested_and_never_need_thermal_values():
    qa_pixel = np.array([[0, 8, 0, 0, 0, 0]], dtype=np.uint16)
    arrays = {
        "qa_pixel": qa_pixel,
        "qa_radsat": np.array([[0, 0, 1 << 11, 0, 0, 0]], dtype=np.uint16),
        "qa": np.array([[400, 400, 400, -9999, 400, 401]], dtype=np.int16),
        "cdist": np.array([[100, 100, 100, 100, 99, 100]], dtype=np.int16),
    }
    stages = qa_stages(arrays, np.ones(qa_pixel.shape, dtype=bool))
    assert [int(v.sum()) for v in stages.values()] == [6, 5, 4, 3, 2, 1]


def test_date_support_counts_are_fixed_denominator_and_conservative():
    zones = np.array([[1, 1, 2, 2]], dtype=np.int32)
    eligible = np.array([[True, True, True, True]])
    observed = np.ones(zones.shape, dtype=bool)
    qa = np.array([[True, True, False, True]])
    stage_masks = {"observed_footprint": observed, "st_qa_le_4k": qa}
    result = summarize_date(
        city_id="synthetic",
        local_date="2020-05-08",
        scene_ids=["one", "two"],
        stage_masks=stage_masks,
        zones=zones,
        eligible=eligible,
        place_mask=observed,
        static_counts=np.array([2, 2]),
        zone_counts=np.array([2, 2]),
        grid_sha256="fixed",
    )
    assert result["scene_count"] == 2
    assert result["static_zero_denominator_tracts"] == 0
    assert result["stage_retention"]["st_qa_le_4k"]["eligible_pixels_retained"] == 3
    assert result["final_qa4k_tract_count"] == 0  # original >=20-pixel gate
    assert result["status"] == "qa_support_insufficient"
    assert result["official_geometry_equivalence_verified"] is False


def test_zero_land_denominator_cannot_pass():
    zones = np.array([[1, 2]], dtype=np.int32)
    observed = np.ones(zones.shape, dtype=bool)
    result = summarize_date(
        city_id="synthetic",
        local_date="2020-05-08",
        scene_ids=["one"],
        stage_masks={"observed_footprint": observed, "st_qa_le_4k": observed},
        zones=zones,
        eligible=np.array([[True, False]]),
        place_mask=observed,
        static_counts=np.array([1, 0]),
        zone_counts=np.array([1, 1]),
        grid_sha256="fixed",
    )
    assert result["static_zero_denominator_tracts"] == 1
    assert result["status"] == "qa_support_insufficient"
