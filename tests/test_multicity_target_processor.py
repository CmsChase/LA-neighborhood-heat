from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from la_heat.aligned_landsat import REQUIRED_ASSETS, AlignedScene
from la_heat.multicity.target_processor import (
    TargetProcessorError,
    read_authorized_scenes,
)


class _Gate:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def before_first_value_access(self) -> None:
        if "gate" not in self.events:
            self.events.append("gate")


def _scene(scene_id: str) -> AlignedScene:
    shape = (1, 1)
    return AlignedScene(
        scene_id=scene_id,
        lst_c=np.zeros(shape),
        valid=np.ones(shape, dtype=bool),
        st_uncertainty_k=np.zeros(shape),
        cloud_distance_km=np.ones(shape),
        footprint=np.ones(shape, dtype=bool),
    )


def test_opens_gate_before_hydration_and_never_persists_hrefs() -> None:
    events: list[str] = []

    def hydrate(scene_id: str) -> dict[str, str]:
        events.append(f"hydrate:{scene_id}")
        return {asset: f"https://example.test/{asset}.tif" for asset in REQUIRED_ASSETS}

    def read(**kwargs: Any) -> AlignedScene:
        events.append(f"read:{kwargs['scene_id']}")
        return _scene(str(kwargs["scene_id"]))

    scenes = read_authorized_scenes(
        ["scene-a"],
        gate=_Gate(events),  # type: ignore[arg-type]
        hydrator=hydrate,
        reader=read,
        grid=object(),  # type: ignore[arg-type]
        config=object(),  # type: ignore[arg-type]
    )

    assert events == ["gate", "hydrate:scene-a", "read:scene-a"]
    assert [scene.scene_id for scene in scenes] == ["scene-a"]


def test_rejects_hydrator_asset_contract_drift() -> None:
    events: list[str] = []

    with pytest.raises(TargetProcessorError, match="exact asset contract"):
        read_authorized_scenes(
            ["scene-a"],
            gate=_Gate(events),  # type: ignore[arg-type]
            hydrator=lambda _scene_id: {"lwir11": "https://example.test/lwir11.tif"},
            reader=lambda **_kwargs: _scene("scene-a"),
            grid=object(),  # type: ignore[arg-type]
            config=object(),  # type: ignore[arg-type]
        )

    assert events[0] == "gate"
