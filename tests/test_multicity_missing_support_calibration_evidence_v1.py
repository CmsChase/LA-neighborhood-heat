from __future__ import annotations

import ast
from pathlib import Path

import pytest

from la_heat.multicity import missing_support_calibration_evidence_v1 as evidence

ROOT = Path(__file__).resolve().parents[1]


def test_config_binds_exact_three_task_scope_and_fifteen_outputs() -> None:
    config = evidence.read_evidence_config(ROOT / evidence.CONFIG_PATH)
    scope = evidence.expected_plan_authorization_scope()

    assert config.raw["stage"]["target_blind"] is True
    assert scope["cities"] == list(evidence.CITY_IDS)
    assert scope["external_sentinel_cities"] == list(evidence.EXTERNAL_CITY_IDS)
    assert set(scope["evidence_tasks"]) == {"geography", "worldcover", "sentinel"}
    assert scope["tracked_output_paths"] == list(evidence.TRACKED_OUTPUT_PATHS)
    assert len(evidence.TRACKED_OUTPUT_PATHS) == 15
    assert len(set(evidence.TRACKED_OUTPUT_PATHS)) == 15
    assert scope["write_contract"]["overall_terminal_written_last"] is True
    assert scope["write_contract"]["check_only_network_requests"] == 0


def test_exact_permission_map_opens_only_v12_evidence() -> None:
    permissions = evidence.expected_authorized_now()
    assert sum(permissions.values()) == 1
    assert permissions[
        "portable_predictor_missing_support_and_calibration_evidence_staging"
    ] is True
    for forbidden in (
        "predictor_construction",
        "model_fitting",
        "external_target_or_qa_value_access",
        "one_time_external_evaluation",
        "operational_forecast_claim",
    ):
        assert permissions[forbidden] is False


def test_signed_urls_and_secret_fields_are_rejected() -> None:
    with pytest.raises(
        evidence.MissingSupportCalibrationEvidenceV1Error,
        match="signed|credential",
    ):
        evidence.assert_no_secrets(
            {"asset": "https://example.test/a.tif?sv=1&sig=secret"}
        )
    with pytest.raises(
        evidence.MissingSupportCalibrationEvidenceV1Error,
        match="secret-like",
    ):
        evidence.assert_no_secrets({"bearer_token": "not-allowed"})
    assert (
        evidence.canonical_unsigned_url(
            "https://EXAMPLE.test/path/a.tif?sv=1&sig=removed#fragment"
        )
        == "https://example.test/path/a.tif"
    )


def test_no_clobber_accepts_identical_and_rejects_different(tmp_path: Path) -> None:
    path = tmp_path / "artifact.bin"
    evidence.atomic_bytes_no_clobber(b"first", path)
    evidence.atomic_bytes_no_clobber(b"first", path)
    with pytest.raises(
        evidence.MissingSupportCalibrationEvidenceV1Error,
        match="differs",
    ):
        evidence.atomic_bytes_no_clobber(b"second", path)
    assert path.read_bytes() == b"first"


def test_manifest_commit_is_internal_and_secret_free(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    evidence.write_manifest_no_clobber(
        {"state": "complete", "unsigned": "https://example.test/a.tif"}, path
    )
    payload = evidence.read_json_with_commit(path, label="test manifest")
    assert payload["state"] == "complete"
    assert len(payload["commit_sha256"]) == 64


@pytest.mark.parametrize(
    "relative",
    [
        "src/la_heat/multicity/missing_support_calibration_evidence_v1.py",
        "scripts/stage_multicity_missing_support_calibration_evidence_v1.py",
    ],
)
def test_orchestrator_has_no_target_model_or_final_reader_imports(relative: str) -> None:
    source = (ROOT / relative).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(
        name.startswith(
            (
                "la_heat.final_",
                "la_heat.model",
                "la_heat.target",
                "la_heat.feature_ablation",
            )
        )
        for name in imported_modules
    )
