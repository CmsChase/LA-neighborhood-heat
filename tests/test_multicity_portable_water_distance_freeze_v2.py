from __future__ import annotations

import ast
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

import la_heat.multicity.portable_water_distance_freeze_v2 as freeze_v2
from la_heat.multicity.portable_water_distance_freeze_v2 import (
    ALGORITHM_VERSION,
    CODE_PATHS,
    COMPLETE_STATE,
    CONFIG_SHA256,
    DEFAULT_CONFIG,
    DEFAULT_MANIFEST,
    EXPECTED_ACCESS_CONTRACT,
    EXPECTED_AUTHORIZED_NOW,
    EXPECTED_DECISION_LOCKS,
    EXPECTED_PLAN_LOCKS,
    NEXT_SAFE_STAGE,
    OUTCOME,
    V1_MANIFEST,
    PortableWaterDistanceFreezeV2Error,
    _authenticate_terminal_publication,
    _compose_payload,
    _diagnostic_evidence,
    _git_preflight,
    _read_committed_json,
    _read_config,
    _strict_equal,
    _validate_l3_success,
    _validate_plan_payload,
    _validate_runtime_against_v7_plan,
    audit_portable_water_distance_freeze_v2,
    expected_plan_authorization_scope,
)
from la_heat.provenance import canonical_sha256, sha256_file

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / DEFAULT_CONFIG
SUCCESS = (
    ROOT
    / "manifests"
    / "multicity"
    / "reviews"
    / "portable_water_distance"
    / "GSHHG_L3_HIERARCHY_AUDIT.json"
)
MODULE = ROOT / "src" / "la_heat" / "multicity" / "portable_water_distance_freeze_v2.py"


def _plan_payload() -> dict[str, object]:
    return {
        "schema_version": 7,
        "algorithm_version": "multicity-planning-readiness-v7",
        "state": "planning_ready",
        "planning_stage": (
            "gshhg_l3_hierarchy_audit_complete_freeze_decision_authorized"
        ),
        "next_safe_stage": (
            "separate_portable_water_distance_source_and_algorithm_freeze_decision"
        ),
        "experiment_id": "la_to_three_city_zero_shot_v1",
        "config_semantic_sha256": (
            "6a52eb39486dc9e992927e5506b4071aaba6ec31dd910eeb0f9bcc51dea1736c"
        ),
        "authorized_now": deepcopy(EXPECTED_AUTHORIZED_NOW),
        "locks": deepcopy(EXPECTED_PLAN_LOCKS),
        "freeze_decision_authorization_scope": expected_plan_authorization_scope(),
    }


def test_v2_config_is_exact_and_freezes_only_source_and_algorithm() -> None:
    _, payload = _read_config(CONFIG)

    assert sha256_file(CONFIG) == CONFIG_SHA256
    assert payload["decision"]["algorithm_version"] == ALGORITHM_VERSION
    assert payload["decision"]["decision_date"] == "2026-08-01"
    assert payload["decision"]["state"] == COMPLETE_STATE
    assert payload["decision"]["outcome"] == OUTCOME
    assert payload["locks"] == EXPECTED_DECISION_LOCKS
    assert payload["access_contract"] == EXPECTED_ACCESS_CONTRACT
    assert payload["next_gate"]["stage_id"] == NEXT_SAFE_STAGE
    assert payload["next_gate"]["v8_transition_required"] is True
    assert payload["next_gate"][
        "canonical_plan_source_and_algorithm_locks_remain_false_until_v8"
    ] is True
    assert payload["algorithm_lock"]["tract_aggregation_frozen"] is False
    assert payload["algorithm_lock"]["feature_names_frozen"] is False
    assert payload["algorithm_lock"]["dateline_tolerance_degrees"] == 1e-9
    assert payload["algorithm_lock"]["l1_repair_source_id"] == "2380"
    assert payload["algorithm_lock"]["projected_crs_by_city"] == {
        "los_angeles_ca": "EPSG:32611",
        "phoenix_az": "EPSG:32612",
        "houston_tx": "EPSG:32615",
        "chicago_il": "EPSG:32616",
    }
    assert payload["algorithm_lock"]["audited_executor_code_runtime_sha256"] == (
        "b049aca7207d282ad5b353fd6e7d80fa1c4f431983d23ebed30459ef8f0e204f"
    )


def test_v2_paths_are_append_only_and_do_not_replace_v1() -> None:
    assert DEFAULT_MANIFEST != V1_MANIFEST
    assert (ROOT / V1_MANIFEST).is_file()

    with pytest.raises(
        PortableWaterDistanceFreezeV2Error,
        match="never overwrite",
    ):
        audit_portable_water_distance_freeze_v2(
            CONFIG,
            output_path=ROOT / V1_MANIFEST,
        )


def test_v2_rejects_noncanonical_output(tmp_path: Path) -> None:
    with pytest.raises(
        PortableWaterDistanceFreezeV2Error,
        match="canonical append-only output",
    ):
        audit_portable_water_distance_freeze_v2(
            CONFIG,
            output_path=tmp_path / "alternate.json",
        )


def test_v2_authorization_scope_lists_every_nonplan_input() -> None:
    scope = expected_plan_authorization_scope()
    assert scope["experiment_id"] == "la_to_three_city_zero_shot_v1"
    assert scope["experiment_semantic_sha256"] == (
        "6a52eb39486dc9e992927e5506b4071aaba6ec31dd910eeb0f9bcc51dea1736c"
    )
    assert set(scope["tracked_read_set"]) == {
        "deferred_v1_decision",
        "l3_preregistration",
        "l3_v1_failure",
        "l3_v2_amendment",
        "l3_v2_success",
    }
    assert scope["source_only_diagnostic_values_may_be_read_from_l3_success"] is True
    assert set(scope["planning_authentication_read_set"]) == {
        "v6_predecessor",
        "v6_experiment_config_files",
        "v7_plan_publication",
    }
    assert scope["archive_or_member_read_allowed"] is False


def test_v2_plan_authorization_opens_only_freeze_decision() -> None:
    _validate_plan_payload(_plan_payload())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "target_blind_gshhg_l3_hierarchy_geometry_read",
            True,
            "wrong permission",
        ),
        ("predictor_construction", True, "wrong permission"),
        ("model_fitting", True, "wrong permission"),
        ("external_target_or_qa_value_access", True, "wrong permission"),
    ],
)
def test_v2_plan_rejects_extra_permission(
    field: str,
    value: bool,
    message: str,
) -> None:
    plan = _plan_payload()
    plan["authorized_now"][field] = value  # type: ignore[index]

    with pytest.raises(PortableWaterDistanceFreezeV2Error, match=message):
        _validate_plan_payload(plan)


def test_v2_plan_rejects_changed_scope_or_experiment_lock() -> None:
    changed_scope = _plan_payload()
    changed_scope["freeze_decision_authorization_scope"][
        "archive_or_member_read_allowed"
    ] = True  # type: ignore[index]
    with pytest.raises(PortableWaterDistanceFreezeV2Error, match="scope changed"):
        _validate_plan_payload(changed_scope)

    changed_lock = _plan_payload()
    changed_lock["locks"]["predictor_build_authorized"] = True  # type: ignore[index]
    with pytest.raises(PortableWaterDistanceFreezeV2Error, match="experiment locks"):
        _validate_plan_payload(changed_lock)


def test_v2_authenticates_completed_l3_source_and_numerical_gates() -> None:
    _, config = _read_config(CONFIG)
    success, _ = _read_committed_json(SUCCESS)

    _validate_l3_success(success, config)


def test_v2_rejects_resigned_l3_success_with_failed_gate() -> None:
    _, config = _read_config(CONFIG)
    success, _ = _read_committed_json(SUCCESS)
    changed = deepcopy(success)
    changed["numerical_audit"]["all_numerical_gates_passed"] = False

    with pytest.raises(PortableWaterDistanceFreezeV2Error, match="every completed gate"):
        _validate_l3_success(changed, config)


def test_v2_rejects_resigned_l3_success_with_source_substitution() -> None:
    _, config = _read_config(CONFIG)
    success, _ = _read_committed_json(SUCCESS)
    changed = deepcopy(success)
    changed["source_archive"]["sha256"] = "0" * 64

    with pytest.raises(
        PortableWaterDistanceFreezeV2Error,
        match=r"source_archive\.sha256",
    ):
        _validate_l3_success(changed, config)


def test_v2_authenticates_exact_seven_row_diagnostic_table() -> None:
    _, config = _read_config(CONFIG)
    success, _ = _read_committed_json(SUCCESS)
    record = _diagnostic_evidence(success, config)

    assert record["row_count"] == 7
    assert len(record["rows"]) == 7
    assert record["source"]["file_sha256"] == (
        "9b206f449d71f23ff0f13d0adca436a2d433140560fef92646d48a7e5c522070"
    )
    assert record["ignored_local_csv_opened"] is False


def test_v2_committed_json_rejects_resigned_body_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    payload = {"state": "expected"}
    payload["commit_sha256"] = canonical_sha256(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    parsed, _ = _read_committed_json(path)
    assert parsed["state"] == "expected"

    parsed["state"] = "changed"
    path.write_text(json.dumps(parsed), encoding="utf-8")
    with pytest.raises(PortableWaterDistanceFreezeV2Error, match="internal commit"):
        _read_committed_json(path)


def test_v2_exact_terminal_reconstruction_rejects_resigned_core_change() -> None:
    _, config = _read_config(CONFIG)
    plan = _plan_payload()
    plan["commit_sha256"] = canonical_sha256(plan)
    expected = _compose_payload(
        config_path=CONFIG,
        config=config,
        repository={
            "branch": "main",
            "head": "1" * 40,
            "origin_main": "1" * 40,
            "head_equals_origin_main": True,
            "working_tree_clean_before_decision": True,
        },
        plan=plan,
        plan_file_sha256="2" * 64,
        plan_bytes=123,
        prerequisites={"authenticated": True},
        diagnostics={"rows": 7},
        code_runtime={"sha256": "3" * 64},
    )
    changed = deepcopy(expected)
    changed["algorithm_lock"]["canonical_worker_count"] = 8
    changed["commit_sha256"] = canonical_sha256(
        {key: value for key, value in changed.items() if key != "commit_sha256"}
    )

    assert not _strict_equal(changed, expected)


def test_v2_git_preflight_rejects_hidden_worktree_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    head = "a" * 40

    def fake_git(project_root: Path, *args: str, text: bool = True) -> str:
        del project_root, text
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("rev-parse", "HEAD") or args == (
            "rev-parse",
            "origin/main",
        ):
            return head
        if args[:2] == ("status", "--porcelain=v1"):
            return ""
        if args[0] == "ls-tree":
            return f"100644 blob {'1' * 40}\ttracked.txt"
        if args[0] == "hash-object":
            return "2" * 40
        raise AssertionError(args)

    monkeypatch.setattr(freeze_v2, "_run_git", fake_git)
    with pytest.raises(
        PortableWaterDistanceFreezeV2Error,
        match="index visibility flag",
    ):
        _git_preflight(tmp_path, required_paths=("tracked.txt",))


def test_v2_rejects_runtime_not_frozen_by_v7() -> None:
    code_files = {
        path: {"sha256": "1" * 64}
        for path in CODE_PATHS
    }
    runtime_files = {path: "1" * 64 for path in CODE_PATHS}
    runtime_files[CODE_PATHS[0]] = "2" * 64

    with pytest.raises(
        PortableWaterDistanceFreezeV2Error,
        match="differ from the v7 frozen code files",
    ):
        _validate_runtime_against_v7_plan(
            {"code_files": code_files},
            {
                "files": runtime_files,
                "relative_paths": list(CODE_PATHS),
            },
        )


def test_v2_rejects_terminal_tamper_then_restore_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = "b" * 40
    parent = "a" * 40
    current = "d" * 40

    def fake_git(project_root: Path, *args: str, text: bool = True) -> str:
        del project_root, text
        if args[0] == "log" and "--diff-filter=A" in args:
            return publication
        if args[0] == "rev-list":
            return f"{publication} {parent}"
        if args[0] == "log":
            return f"{'c' * 40}\n{current}"
        raise AssertionError(args)

    monkeypatch.setattr(freeze_v2, "_run_git", fake_git)
    monkeypatch.setattr(
        freeze_v2,
        "_git_blob_sha256",
        lambda *args, **kwargs: "1" * 64,
    )
    monkeypatch.setattr(
        freeze_v2.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )

    with pytest.raises(
        PortableWaterDistanceFreezeV2Error,
        match="changed after its first publication",
    ):
        _authenticate_terminal_publication(
            ROOT,
            terminal_file_sha256="1" * 64,
            expected_parent_commit=parent,
            current_head=current,
        )


def test_v2_module_has_no_source_or_target_reader_imports() -> None:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])

    assert imported.isdisjoint(
        {
            "geopandas",
            "numpy",
            "pandas",
            "pyarrow",
            "rasterio",
            "requests",
            "shapely",
            "urllib",
            "zipfile",
        }
    )
    source = MODULE.read_text(encoding="utf-8")
    assert "ZipFile(" not in source
    assert "read_parquet(" not in source
    assert "read_file(" not in source
