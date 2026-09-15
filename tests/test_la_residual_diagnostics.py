from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RUN_PATH = ROOT / "experiments" / "la_residual_diagnostics" / "run.py"
SPEC = importlib.util.spec_from_file_location("la_residual_diagnostics_run", RUN_PATH)
assert SPEC is not None and SPEC.loader is not None
RUN = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUN
SPEC.loader.exec_module(RUN)


def test_neighbor_topology_excludes_self_and_duplicates() -> None:
    import tomllib

    with RUN.CONFIG_PATH.open("rb") as handle:
        config = tomllib.load(handle)
    geoids, neighbors, metadata = RUN.load_neighbors(config)
    assert len(geoids) == 1096
    assert neighbors.shape == (1096, 6)
    assert metadata["self_memberships"] == 0
    assert metadata["duplicate_memberships_within_focal_tract"] == 0


def test_date_neighbor_statistic_compares_own_with_other_neighbor_mean() -> None:
    geoids = ["a", "b", "c"]
    neighbors = np.asarray([[1], [0], [1]])
    rows = pd.DataFrame(
        {
            "target_date": ["2022-01-01"] * 3,
            "tract_geoid": geoids,
            RUN.SIGNED: [-1.0, 0.0, 1.0],
        }
    )
    observed, detail, arrays = RUN.date_neighbor_statistic(rows, geoids, neighbors)
    assert observed == 0.0
    assert detail[0]["tracts"] == 3
    assert arrays[0].tolist() == [-1.0, 0.0, 1.0]


def test_scene_signature_uses_only_landsat_path_row() -> None:
    value = "LC08_L2SP_041036_20220501_02_T1|LC08_L2SP_041037_20220501_02_T1"
    assert RUN.scene_signature(value) == "041036+041037"
