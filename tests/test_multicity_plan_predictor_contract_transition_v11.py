from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from la_heat.multicity import plan_predictor_contract_transition_v11 as transition

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / transition.V11_MODULE_PATH
SCRIPT = ROOT / transition.V11_SCRIPT_PATH


def _decision_contract() -> tuple[
    tuple[str, ...],
    dict[str, dict[str, object]],
    dict[str, Any],
    str,
    str,
]:
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            transition,
            "_preflight_decision_contract_import",
            lambda _root: "c" * 40,
        )
        return transition._decision_contract(ROOT)


def _payload() -> tuple[dict[str, Any], dict[str, Any]]:
    predecessor, raw = transition._v10_predecessor(ROOT)
    code_paths, source_records, scope, _, _ = _decision_contract()
    terminal, authenticated_records = transition._validate_source_records(
        ROOT,
        source_records,
    )
    assert authenticated_records == source_records
    code_files = {
        path: {
            "sha256": "a" * 64,
            "bytes": index + 1,
            "git_blob_oid": "b" * 40,
            "git_mode": "100644",
        }
        for index, path in enumerate(code_paths)
    }
    payload = transition._build_v11_payload(
        predecessor,
        predecessor_bytes=len(raw),
        terminal=terminal,
        source_records=source_records,
        implementation_commit="c" * 40,
        code_files=code_files,
        authorization_scope=scope,
    )
    return predecessor, payload


def test_v11_opens_only_the_deferred_v2_decision_permission() -> None:
    predecessor, payload = _payload()

    assert payload["schema_version"] == 11
    assert payload["algorithm_version"] == transition.ALGORITHM_VERSION
    assert payload["state"] == "planning_ready"
    assert payload["planning_stage"] == transition.PLANNING_STAGE
    assert payload["next_safe_stage"] == transition.NEXT_SAFE_STAGE
    assert payload["authorized_now"] == transition.AUTHORIZED_NOW
    assert payload["locks"] == predecessor["locks"] == transition.LOCKS
    assert payload["authorized_now"][
        "portable_predictor_source_and_calibration_contract_freeze_v2"
    ] is True
    assert sum(payload["authorized_now"].values()) == 1
    assert payload["authorized_now"]["predictor_construction"] is False
    assert payload["authorized_now"]["model_fitting"] is False
    assert payload["authorized_now"]["external_target_or_qa_value_access"] is False
    assert payload["authorized_now"]["one_time_external_evaluation"] is False
    transition._validate_transition_boundary(predecessor, payload)


def test_v11_binds_the_exact_target_blind_v2_scope_and_source_publication() -> None:
    _, payload = _payload()
    _, source_records, scope, output_path, _ = _decision_contract()

    assert payload[
        "portable_predictor_contract_freeze_v2_authorization_scope"
    ] == scope
    assert scope["source_output_records"] == source_records
    assert scope["decision_output_path"] == output_path
    assert scope["network_requests"] == 0
    assert scope["tracked_files_and_historical_git_blobs_only"] is True
    for key in (
        "untracked_file_contents_allowed",
        "source_payload_or_geometry_allowed",
        "predictor_or_model_value_allowed",
        "external_target_or_qa_value_allowed",
        "final_evaluation_output_allowed",
        "predictor_construction_allowed",
        "model_fitting_allowed",
        "protocol_promotion_allowed",
    ):
        assert scope[key] is False, key
    evidence = payload["portable_predictor_source_evidence_v1"]
    assert evidence["publication_git_commit"] == transition.SOURCE_PUBLICATION_COMMIT
    assert evidence["tracked_output_paths"] == list(
        transition.SOURCE_TRACKED_OUTPUT_PATHS
    )
    assert evidence["tracked_output_records"] == source_records
    assert evidence["source_evidence_complete"] is True
    assert evidence["predictor_values_computed"] is False


def test_v11_lists_every_remaining_scientific_gate_before_build() -> None:
    _, payload = _payload()
    assert payload["blockers_before_predictor_build"] == [
        "complete_deferred_portable_predictor_contract_v2_decision",
        "complete_four_city_geography_contract_and_los_angeles_parity_evidence",
        "complete_four_city_worldcover_item_mosaic_and_eligible_support_evidence",
        "complete_external_city_sentinel_asset_calibration_smoke_evidence",
        "complete_separate_portable_predictor_contract_v3_decision",
        "authorize_predictor_construction_with_separate_tracked_only_transition",
        "promote_protocol_from_draft_with_separate_lock",
    ]


def test_v11_authenticates_fixed_v10_and_source_history() -> None:
    predecessor, raw = transition._v10_predecessor(ROOT)
    assert len(raw) == transition.V10_BYTES
    assert predecessor["commit_sha256"] == transition.V10_INTERNAL_COMMIT_SHA256
    transition._require_fixed_history(
        ROOT,
        current_head=transition.IMPLEMENTATION_BASE_COMMIT,
    )
    _, source_records, _, _, _ = _decision_contract()
    terminal, observed = transition._validate_source_records(ROOT, source_records)
    assert terminal["state"] == (
        "complete_target_blind_portable_predictor_source_evidence"
    )
    assert observed == source_records


def test_v11_implementation_delta_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    implementation = "d" * 40
    mode = {"extra": False}

    def fake_git(_root: Path, *args: str, **kwargs: Any) -> str | bytes:
        del kwargs
        if args[:4] == ("rev-list", "--parents", "-n", "1"):
            return f"{implementation} {transition.IMPLEMENTATION_BASE_COMMIT}\n"
        if args and args[0] == "diff-tree":
            pairs = list(transition.EXPECTED_IMPLEMENTATION_DELTA)
            if mode["extra"]:
                pairs.append(("A", "unexpected.txt"))
            return b"".join(
                status.encode("ascii") + b"\0" + path.encode("utf-8") + b"\0"
                for status, path in pairs
            )
        raise AssertionError(args)

    monkeypatch.setattr(transition, "_run_git", fake_git)
    transition._implementation_delta(ROOT, implementation)
    mode["extra"] = True
    with pytest.raises(
        transition.MulticityPlanPredictorContractTransitionV11Error,
        match="outside its exact allowlist",
    ):
        transition._implementation_delta(ROOT, implementation)


def test_v11_rejects_dirty_module_before_dynamic_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head = "a" * 40

    def fake_git(_root: Path, *args: str, **_kwargs: Any) -> str | bytes:
        if args == ("branch", "--show-current"):
            return "main\n"
        if args == ("rev-parse", "HEAD") or args == (
            "rev-parse",
            "origin/main",
        ):
            return f"{head}\n"
        if args and args[0] == "status":
            return b"?? src/la_heat/multicity/portable_predictor_contract_freeze_v2.py\0"
        raise AssertionError(args)

    imported = {"called": False}

    def forbidden_import(_name: str) -> object:
        imported["called"] = True
        raise AssertionError("dirty contract module was imported")

    monkeypatch.setattr(transition, "_run_git", fake_git)
    monkeypatch.setattr(transition.importlib, "import_module", forbidden_import)
    with pytest.raises(
        transition.MulticityPlanPredictorContractTransitionV11Error,
        match="clean synchronized main",
    ):
        transition._decision_contract(ROOT)
    assert imported["called"] is False


def test_v11_exact_payload_rejects_tampering() -> None:
    _, payload = _payload()
    transition._validate_exact_v11_payload(payload, payload)
    tampered = deepcopy(payload)
    tampered["next_safe_stage"] = "skip_ahead"
    with pytest.raises(
        transition.MulticityPlanPredictorContractTransitionV11Error,
        match="internal commit",
    ):
        transition._validate_exact_v11_payload(tampered, payload)


@pytest.mark.parametrize("path", [MODULE, SCRIPT])
def test_v11_program_has_no_data_network_geometry_or_model_reader_imports(
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
