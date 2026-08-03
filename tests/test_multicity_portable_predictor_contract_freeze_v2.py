from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path

import pytest

import la_heat.multicity.portable_predictor_contract_freeze_v2 as contract_v2
from la_heat.provenance import sha256_file

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / contract_v2.CONFIG_PATH
MODULE = ROOT / contract_v2.MODULE_PATH
SCRIPT = ROOT / contract_v2.SCRIPT_PATH


def _config() -> tuple[dict[str, object], bytes]:
    payload, raw = contract_v2._read_config(CONFIG)
    contract_v2._validate_config(payload)
    return payload, raw


def _payload() -> dict[str, object]:
    config, raw = _config()
    plan = {
        "schema_version": 11,
        "algorithm_version": "multicity-planning-readiness-v11",
        "commit_sha256": "a" * 64,
    }
    return contract_v2._build_payload(
        config=config,
        config_raw=raw,
        plan=plan,
        plan_publication="b" * 40,
        precondition_head="b" * 40,
        source_evidence={},
        prerequisites={},
        code_files={},
        generated_at_utc="2026-08-03T00:00:00Z",
    )


def test_v2_config_binds_candidate_registry_but_defers_formal_contract() -> None:
    config, _ = _config()
    decision = config["decision"]
    locks = config["locks"]

    assert sha256_file(CONFIG) == contract_v2.CONFIG_SHA256
    assert decision["state"] == contract_v2.STATE
    assert decision["outcome"] == contract_v2.OUTCOME
    assert locks == {
        "portable_water_distance_source_locked": True,
        "portable_water_distance_algorithm_locked": True,
        "portable_predictor_source_and_calibration_contract_locked": False,
        "portable_feature_names_frozen": False,
        "portable_water_distance_feature_names_frozen": False,
        "candidate_46_feature_registry_bound_for_evidence_stage": True,
        "predictor_build_authorized": False,
        "model_fit_authorized": False,
        "protocol_lock_created": False,
        "external_targets_unlocked": False,
        "external_target_values_read": False,
        "external_prediction_commit_exists": False,
    }


def test_v2_candidate_registry_is_exactly_46_unique_features_and_b1_is_23() -> None:
    config, _ = _config()
    registry = config["feature_registry"]
    names = tuple(registry["static"]["names"])
    names += tuple(registry["calendar"]["names"])
    names += tuple(registry["daymet"]["names"])
    names += tuple(registry["sentinel"]["names"])

    assert names == contract_v2.M2_FEATURES
    assert len(names) == len(set(names)) == 46
    assert len(contract_v2.B1_FEATURES) == 23
    assert contract_v2.B1_FEATURES == (
        *contract_v2.CALENDAR_FEATURES,
        *contract_v2.DAYMET_FEATURES,
    )
    assert {
        "gshhg_ocean_great_lakes_shore_distance_mean_km",
        "gshhg_ocean_great_lakes_shore_distance_p10_km",
    }.issubset(names)
    assert not any("pacific_coast" in name for name in names)


def test_v2_records_three_new_target_blind_blockers_and_keeps_build_closed() -> None:
    config, raw = _config()
    payload = _payload()
    blockers = config["unresolved_evidence"]["required_blockers"]
    decision = payload["decision"]

    assert blockers == [
        "four_city_geography_contract_and_los_angeles_parity_evidence_absent",
        "four_city_worldcover_item_mosaic_and_eligible_support_evidence_absent",
        "external_city_sentinel_asset_calibration_smoke_evidence_absent",
    ]
    assert payload["unresolved_evidence"] == config["unresolved_evidence"]
    assert decision["contract_freeze_passed"] is False
    assert decision["all_four_v1_evidence_gaps_closed"] is True
    assert decision["new_v2_blockers_observed"] == blockers
    assert decision["candidate_rules_and_registry_bound_for_evidence_stage"] is True
    assert decision["portable_predictor_contract_locked"] is False
    assert decision["portable_feature_names_frozen"] is False
    assert decision["portable_water_distance_feature_names_frozen"] is False
    assert decision["predictor_build_recommended_for_separate_authorization"] is False
    assert decision["predictor_build_authorized_now"] is False
    assert decision["model_fit_authorized_now"] is False
    assert decision["external_targets_unlocked"] is False
    assert decision["next_safe_stage"] == (
        "publish_tracked_only_plan_v12_for_missing_support_and_calibration_evidence"
    )
    contract_v2._validate_terminal(payload, config=config, config_raw=raw)


def test_v2_phase1_water_names_are_replacements_not_aliases() -> None:
    payload = _payload()
    replacements = payload["feature_registry"]["phase1_water_name_replacements"]
    assert replacements == {
        "pacific_coast_distance_mean_km": (
            "gshhg_ocean_great_lakes_shore_distance_mean_km"
        ),
        "pacific_coast_distance_p10_km": (
            "gshhg_ocean_great_lakes_shore_distance_p10_km"
        ),
    }
    assert payload["contract"]["static"]["water_distance"][
        "phase1_pacific_feature_alias_allowed"
    ] is False


def test_v2_full_reconstruction_rejects_forged_unchecked_block() -> None:
    config, raw = _config()
    payload = _payload()
    plan = {
        "schema_version": 11,
        "algorithm_version": "multicity-planning-readiness-v11",
        "commit_sha256": "a" * 64,
    }
    contract_v2._validate_terminal_reconstruction(
        payload,
        config=config,
        config_raw=raw,
        plan=plan,
        plan_publication="b" * 40,
        source_evidence={},
        prerequisites={},
        code_files={},
    )

    forged = deepcopy(payload)
    forged["prerequisites"] = {"forged": True}
    body = {key: value for key, value in forged.items() if key != "commit_sha256"}
    forged["commit_sha256"] = contract_v2._canonical_sha256(body)
    contract_v2._validate_terminal(forged, config=config, config_raw=raw)
    with pytest.raises(
        contract_v2.PortablePredictorContractFreezeV2Error,
        match="full authenticated reconstruction",
    ):
        contract_v2._validate_terminal_reconstruction(
            forged,
            config=config,
            config_raw=raw,
            plan=plan,
            plan_publication="b" * 40,
            source_evidence={},
            prerequisites={},
            code_files={},
        )


def test_v2_generation_requires_exact_v11_publication_head() -> None:
    with pytest.raises(
        contract_v2.PortablePredictorContractFreezeV2Error,
        match="HEAD to equal the exact planning-v11 publication",
    ):
        contract_v2._require_terminal_generation_precondition(
            head="b" * 40,
            plan_publication="a" * 40,
            output_exists=False,
            check_only=False,
        )
    with pytest.raises(
        contract_v2.PortablePredictorContractFreezeV2Error,
        match="terminal is absent",
    ):
        contract_v2._require_terminal_generation_precondition(
            head="a" * 40,
            plan_publication="a" * 40,
            output_exists=False,
            check_only=True,
        )
    contract_v2._require_terminal_generation_precondition(
        head="b" * 40,
        plan_publication="a" * 40,
        output_exists=True,
        check_only=True,
    )


def test_v2_atomic_writer_never_overwrites_existing_terminal(tmp_path: Path) -> None:
    destination = tmp_path / "terminal.json"
    destination.write_bytes(b"preserve-me")
    with pytest.raises(
        contract_v2.PortablePredictorContractFreezeV2Error,
        match="Refusing to overwrite",
    ):
        contract_v2._atomic_write(destination, b"replacement")
    assert destination.read_bytes() == b"preserve-me"


def test_v2_rejects_formal_lock_or_removed_blocker() -> None:
    config, _ = _config()
    changed = deepcopy(config)
    changed["locks"][
        "portable_predictor_source_and_calibration_contract_locked"
    ] = True
    with pytest.raises(
        contract_v2.PortablePredictorContractFreezeV2Error,
        match="lock|contract",
    ):
        contract_v2._validate_config(changed)

    changed = deepcopy(config)
    changed["unresolved_evidence"]["required_blockers"].pop()
    with pytest.raises(
        contract_v2.PortablePredictorContractFreezeV2Error,
        match="blocker|evidence",
    ):
        contract_v2._validate_config(changed)


@pytest.mark.parametrize("path", [MODULE, SCRIPT])
def test_v2_program_has_no_data_network_geometry_or_model_reader_imports(
    path: Path,
) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])

    assert imported.isdisjoint(
        {
            "geopandas",
            "joblib",
            "numpy",
            "pandas",
            "pyarrow",
            "rasterio",
            "requests",
            "shapely",
            "sklearn",
            "urllib",
            "zipfile",
        }
    )
    for forbidden_call in (
        "ZipFile(",
        "read_parquet(",
        "read_file(",
        "urlopen(",
        ".fit(",
        ".predict(",
    ):
        assert forbidden_call not in source
    assert "data/" not in source
    assert "exports/" not in source
    assert "reports/" not in source
