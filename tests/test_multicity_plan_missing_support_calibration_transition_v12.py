from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from la_heat.multicity import (
    plan_missing_support_calibration_transition_v12 as transition,
)

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / transition.TRANSITION_MODULE_PATH
SCRIPT = ROOT / transition.AUTHORIZATION_SCRIPT_PATH


def _executor_contract() -> tuple[tuple[str, ...], dict[str, Any], str]:
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            transition,
            "_preflight_executor_import",
            lambda _root: ("c" * 40, "c" * 40),
        )
        return transition._executor_contract(ROOT)


def _payload() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    predecessor, terminal = transition._historical_inputs(ROOT)
    code_paths, scope, implementation = _executor_contract()
    code_files = {
        path: {
            "sha256": "a" * 64,
            "bytes": index + 1,
            "git_blob_oid": "b" * 40,
            "git_mode": "100644",
        }
        for index, path in enumerate(code_paths)
    }
    payload = transition._build_payload(
        predecessor,
        terminal,
        implementation=implementation,
        code_files=code_files,
        authorization_scope=scope,
    )
    return predecessor, terminal, payload


def test_v12_opens_only_target_blind_missing_evidence_permission() -> None:
    predecessor, _, payload = _payload()

    assert payload["schema_version"] == 12
    assert payload["algorithm_version"] == transition.ALGORITHM_VERSION
    assert payload["planning_stage"] == transition.PLANNING_STAGE
    assert payload["next_safe_stage"] == transition.NEXT_SAFE_STAGE
    assert payload["authorized_now"] == transition.AUTHORIZED_NOW
    assert sum(payload["authorized_now"].values()) == 1
    assert payload["authorized_now"][
        "portable_predictor_missing_support_and_calibration_evidence_staging"
    ] is True
    assert payload["locks"] == predecessor["locks"] == transition.LOCKS
    assert payload["authorized_now"]["predictor_construction"] is False
    assert payload["authorized_now"]["model_fitting"] is False
    assert payload["authorized_now"]["external_target_or_qa_value_access"] is False
    assert payload["authorized_now"]["one_time_external_evaluation"] is False
    transition._validate_boundary(predecessor, payload)


def test_v12_consumes_the_exact_deferred_v2_terminal() -> None:
    _, terminal, payload = _payload()

    consumed = payload["consumed_predictor_contract_freeze_v2_authorization"]
    assert consumed["terminal_path"] == transition.V2_TERMINAL_PATH
    assert consumed["terminal_file_sha256"] == transition.V2_FILE_SHA256
    assert consumed["terminal_commit_sha256"] == transition.V2_INTERNAL_COMMIT_SHA256
    assert consumed["outcome"] == terminal["outcome"]
    assert consumed["old_decision_permission_now"] is False
    assert consumed["predictor_model_target_or_result_access"] is False
    assert payload["transition"]["predictor_contract_v2_terminal"]["state"] == (
        "decision_complete_candidate_rules_frozen_contract_deferred_predictor_closed"
    )


def test_v12_binds_exact_executor_scope_and_remaining_gates() -> None:
    _, _, payload = _payload()
    _, scope, _ = _executor_contract()

    assert payload[
        "missing_support_calibration_evidence_v1_authorization_scope"
    ] == scope
    assert scope["configuration"] == {
        "path": transition.CONFIG_PATH,
        "sha256": __import__(
            "la_heat.multicity.missing_support_calibration_evidence_v1",
            fromlist=["CONFIG_SHA256"],
        ).CONFIG_SHA256,
    }
    assert len(scope["tracked_output_paths"]) == 15
    assert payload["blockers_before_predictor_build"] == list(
        transition.BLOCKERS_BEFORE_PREDICTOR_BUILD
    )
    assert payload["transition_access_contract"] == transition.TRANSITION_ACCESS_CONTRACT


def test_v12_implementation_delta_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
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
        transition.MulticityPlanMissingSupportCalibrationTransitionV12Error,
        match="outside its exact allowlist",
    ):
        transition._implementation_delta(ROOT, implementation)


def test_v12_rejects_dirty_executor_before_dynamic_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head = "a" * 40

    def fake_git(_root: Path, *args: str, **_kwargs: Any) -> str | bytes:
        if args == ("branch", "--show-current"):
            return "main\n"
        if args == ("rev-parse", "HEAD") or args == ("rev-parse", "origin/main"):
            return f"{head}\n"
        if args and args[0] == "status":
            return b"?? src/la_heat/multicity/missing_support_calibration_evidence_v1.py\0"
        raise AssertionError(args)

    imported = {"called": False}

    def forbidden_import(_name: str) -> object:
        imported["called"] = True
        raise AssertionError("dirty evidence module was imported")

    monkeypatch.setattr(transition, "_run_git", fake_git)
    monkeypatch.setattr(transition.importlib, "import_module", forbidden_import)
    with pytest.raises(
        transition.MulticityPlanMissingSupportCalibrationTransitionV12Error,
        match="clean synchronized main",
    ):
        transition._executor_contract(ROOT)
    assert imported["called"] is False


@pytest.mark.parametrize("path", [MODULE, SCRIPT])
def test_v12_transition_has_no_data_network_geometry_or_model_reader_imports(
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
