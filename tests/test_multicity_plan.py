from pathlib import Path

import pytest

from la_heat.multicity.config import MulticityConfigError, load_multicity_plan
from la_heat.multicity.workspace import MulticityWorkspace

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "configs" / "multicity" / "experiment.toml"


def test_multicity_plan_declares_one_source_and_three_sealed_cities() -> None:
    plan = load_multicity_plan(CONFIG)

    assert plan.experiment_id == "la_to_three_city_zero_shot_v1"
    assert plan.source_city.id == "los_angeles_ca"
    assert plan.source_city.target_values_status == "known_phase1_anchor"
    assert [city.id for city in plan.external_cities] == [
        "phoenix_az",
        "houston_tx",
        "chicago_il",
    ]
    assert {city.target_values_status for city in plan.external_cities} == {"sealed"}


def test_multicity_plan_keeps_targets_and_forecast_claim_locked() -> None:
    plan = load_multicity_plan(CONFIG)

    locks = plan.raw["locks"]
    assert locks["protocol_locked"] is False
    assert locks["external_targets_unlocked"] is False
    assert locks["allow_external_target_access"] is False
    assert locks["allow_predictor_construction"] is False
    assert plan.raw["phase1_anchor"]["la_2025_allowed_in_new_confirmation"] is False
    assert plan.raw["prospective"]["operational_forecast_claim_allowed"] is False


def test_multicity_workspace_is_separate_from_phase1() -> None:
    plan = load_multicity_plan(CONFIG)
    workspace = MulticityWorkspace.from_plan(plan)

    workspace.assert_isolated()
    phoenix = workspace.city("phoenix_az")
    assert phoenix.interim == ROOT / "data" / "interim" / "multicity" / "phoenix_az"
    assert "final_test_2025" not in phoenix.interim.parts
    assert workspace.experiment_manifests == (
        ROOT
        / "manifests"
        / "multicity"
        / "experiments"
        / "la_to_three_city_zero_shot_v1"
    )


def test_multicity_plan_rejects_an_early_target_unlock(tmp_path: Path) -> None:
    source_dir = CONFIG.parent
    destination = tmp_path / "configs" / "multicity"
    city_destination = destination / "cities"
    city_destination.mkdir(parents=True)
    for city_path in (source_dir / "cities").glob("*.toml"):
        (city_destination / city_path.name).write_bytes(city_path.read_bytes())
    payload = CONFIG.read_text(encoding="utf-8")
    assert payload.count("external_targets_unlocked = false") == 1
    (destination / "experiment.toml").write_text(
        payload.replace(
            "external_targets_unlocked = false",
            "external_targets_unlocked = true",
        ),
        encoding="utf-8",
    )

    with pytest.raises(MulticityConfigError, match="unlock computation or targets"):
        load_multicity_plan(destination / "experiment.toml")
