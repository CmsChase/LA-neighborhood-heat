import numpy as np

from la_heat.landmask import land_classes_to_mask


def test_static_land_mask_excludes_nodata_and_permanent_water_only() -> None:
    classes = np.array([[0, 10, 50, 80, 90]], dtype=np.uint8)
    mask = land_classes_to_mask(classes, nodata_class=0, water_classes=[80])
    assert mask.tolist() == [[False, True, True, False, True]]
