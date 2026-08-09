import json
from pathlib import Path

from la_heat.multicity.portable_predictor_contract import _finalize_registry

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_final_registry_keeps_b1_as_diagnostic_and_m2_as_full_model() -> None:
    candidate = json.loads(
        (
            PROJECT_ROOT
            / "manifests/multicity/reviews/portable_predictor_contract/"
            "PORTABLE_PREDICTOR_CONTRACT_FREEZE_V2.json"
        ).read_text(encoding="utf-8")
    )

    registry = _finalize_registry(candidate)

    assert registry["feature_count"] == 46
    assert sum(row["b1_transfer"] for row in registry["features"]) == 23
    assert sum(row["m2_transfer"] for row in registry["features"]) == 46
    assert registry["formal_feature_names_frozen"] is True
    assert registry["worldcover_is_support_not_predictor"] is True
