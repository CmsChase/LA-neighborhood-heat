import numpy as np

from la_heat.mosaic import mosaic_aligned_scenes


def test_valid_scene_beats_cloudy_scene_regardless_of_quality_scores() -> None:
    result = mosaic_aligned_scenes(
        scene_ids=["cloudy", "clear"],
        st_values=np.array([[[10]], [[20]]], dtype=np.uint16),
        qa_valid=np.array([[[False]], [[True]]]),
        st_qa=np.array([[[0.1]], [[9.0]]]),
        cdist=np.array([[[100.0]], [[1.0]]]),
        footprint=np.ones((2, 1, 1), dtype=bool),
    )

    assert result.selected_scene_index.item() == 1
    assert result.selected_scene_id.item() == "clear"
    assert result.selected_st_value.item() == 20.0
    assert result.selected_valid.item()


def test_st_qa_then_cdist_then_lexical_scene_id_break_ties() -> None:
    result = mosaic_aligned_scenes(
        scene_ids=["z_scene", "a_scene"],
        st_values=np.array([[[10, 20, 30]], [[11, 21, 31]]]),
        qa_valid=np.ones((2, 1, 3), dtype=bool),
        st_qa=np.array([[[1.0, 2.0, 3.0]], [[2.0, 2.0, 3.0]]]),
        cdist=np.array([[[1.0, 1.0, 4.0]], [[9.0, 2.0, 4.0]]]),
        footprint=np.ones((2, 1, 3), dtype=bool),
    )

    assert result.selected_scene_index.tolist() == [[0, 1, 1]]
    assert result.selected_scene_id.tolist() == [["z_scene", "a_scene", "a_scene"]]
    assert result.selected_st_value.tolist() == [[10.0, 21.0, 31.0]]


def test_overlap_contributes_one_output_pixel_and_is_not_double_counted() -> None:
    result = mosaic_aligned_scenes(
        scene_ids=["scene_b", "scene_a"],
        st_values=np.array(
            [
                [[10, 11], [12, 13]],
                [[20, 21], [22, 23]],
            ]
        ),
        qa_valid=np.array(
            [
                [[True, True], [False, False]],
                [[False, True], [True, False]],
            ]
        ),
        st_qa=np.ones((2, 2, 2)),
        cdist=np.ones((2, 2, 2)),
        footprint=np.array(
            [
                [[True, True], [False, False]],
                [[False, True], [True, False]],
            ]
        ),
    )

    assert result.covered_pixel_count == 3
    assert result.valid_pixel_count == 3
    assert np.count_nonzero(result.selected_scene_index >= 0) == 3
    assert result.selected_scene_id.tolist() == [
        ["scene_b", "scene_a"],
        ["scene_a", ""],
    ]
    assert result.selected_st_value.tolist()[0] == [10.0, 21.0]
    assert result.selected_st_value[1, 0] == 22.0
    assert np.isnan(result.selected_st_value[1, 1])
