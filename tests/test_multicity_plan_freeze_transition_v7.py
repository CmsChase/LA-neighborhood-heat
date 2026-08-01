from __future__ import annotations

import ast
import json
from copy import deepcopy
from pathlib import Path

import pytest

import la_heat.multicity.plan_freeze_transition_v7 as plan_v7
from la_heat.multicity.plan_freeze_transition_v7 import (
    ALGORITHM_VERSION,
    AUTHORIZED_NOW,
    BLOCKERS_BEFORE_PREDICTOR_BUILD,
    EXPECTED_AUTHORIZATION_DIFF_PATHS,
    L3_SUCCESS_FILE_SHA256,
    LOCKS,
    NEXT_SAFE_STAGE,
    PLAN_PATH,
    PLANNING_STAGE,
    TRANSITION_ACCESS_CONTRACT,
    TRANSITION_CODE_PATHS,
    V2_CONFIG_PATH,
    V6_BYTES,
    V6_PUBLICATION_COMMIT,
    MulticityPlanFreezeTransitionV7Error,
    _build_v7_payload,
    _git_regular_blob,
    _recursive_diff_paths,
    _require_exact_precondition_plan,
    _require_publication_code_files,
    _require_v7_plan_history,
    _validate_exact_v7_payload,
    _validate_l3_success,
    _validate_v6_predecessor,
    authorize_multicity_water_distance_freeze,
)
from la_heat.multicity.portable_water_distance_freeze_v2 import (
    CONFIG_SHA256,
    expected_plan_authorization_scope,
)
from la_heat.provenance import canonical_sha256

ROOT = Path(__file__).parents[1]
MODULE = ROOT / "src" / "la_heat" / "multicity" / "plan_freeze_transition_v7.py"
PLAN = ROOT / PLAN_PATH
SUCCESS = (
    ROOT
    / "manifests"
    / "multicity"
    / "reviews"
    / "portable_water_distance"
    / "GSHHG_L3_HIERARCHY_AUDIT.json"
)


def _read_json_bytes(path: Path) -> tuple[dict[str, object], bytes]:
    raw = path.read_bytes()
    payload = json.loads(raw)
    assert isinstance(payload, dict)
    return payload, raw


def _synthetic_code_files() -> dict[str, dict[str, object]]:
    records = {
        path: {
            "sha256": "0" * 64,
            "bytes": 1,
            "git_blob_oid": "1" * 40,
            "git_mode": "100644",
        }
        for path in TRANSITION_CODE_PATHS
    }
    records[V2_CONFIG_PATH]["sha256"] = CONFIG_SHA256
    return records


def _payload() -> tuple[dict[str, object], dict[str, object]]:
    predecessor_raw, _, _ = _git_regular_blob(
        ROOT,
        commit=V6_PUBLICATION_COMMIT,
        relative_path=PLAN_PATH,
    )
    predecessor = json.loads(predecessor_raw)
    assert isinstance(predecessor, dict)
    success, success_raw = _read_json_bytes(SUCCESS)
    _validate_v6_predecessor(predecessor, predecessor_raw)
    _validate_l3_success(success, success_raw)
    payload = _build_v7_payload(
        predecessor,
        predecessor_bytes=len(predecessor_raw),
        l3_success=success,
        l3_success_bytes=len(success_raw),
        precondition_commit="a" * 40,
        code_files=_synthetic_code_files(),
        authorization_scope=expected_plan_authorization_scope(),
    )
    return predecessor, payload


def test_v7_changes_exactly_two_authorization_leaves() -> None:
    predecessor, payload = _payload()
    changes = _recursive_diff_paths(
        {"authorized_now": predecessor["authorized_now"]},
        {"authorized_now": payload["authorized_now"]},
    )

    assert changes == EXPECTED_AUTHORIZATION_DIFF_PATHS
    assert payload["authorized_now"] == AUTHORIZED_NOW
    assert payload["locks"] == LOCKS


def test_v7_opens_only_the_evidence_freeze_decision() -> None:
    _, payload = _payload()

    assert payload["algorithm_version"] == ALGORITHM_VERSION
    assert payload["planning_stage"] == PLANNING_STAGE
    assert payload["next_safe_stage"] == NEXT_SAFE_STAGE
    assert payload["blockers_before_predictor_build"] == list(
        BLOCKERS_BEFORE_PREDICTOR_BUILD
    )
    assert payload["gshhg_l3_hierarchy_audit"]["file_sha256"] == (
        L3_SUCCESS_FILE_SHA256
    )


def test_v7_scope_binds_the_complete_v2_read_set_and_runtime() -> None:
    _, payload = _payload()
    scope = payload["freeze_decision_authorization_scope"]

    assert scope == expected_plan_authorization_scope()
    assert scope["decision_runtime_paths"] == list(TRANSITION_CODE_PATHS)
    assert set(scope["tracked_read_set"]) == {
        "deferred_v1_decision",
        "l3_preregistration",
        "l3_v1_failure",
        "l3_v2_amendment",
        "l3_v2_success",
    }
    assert scope["archive_or_member_read_allowed"] is False
    assert scope["predictor_model_target_or_result_read_allowed"] is False


def test_v7_access_ledger_remains_target_and_geometry_blind() -> None:
    _, payload = _payload()

    assert payload["transition_access_contract"] == TRANSITION_ACCESS_CONTRACT
    assert TRANSITION_ACCESS_CONTRACT["network_requests"] == 0
    for key, value in TRANSITION_ACCESS_CONTRACT.items():
        if key.endswith("opened") or key.endswith("computed") or key.endswith(
            "performed"
        ):
            assert value is False


def test_v7_rejects_scope_with_one_omitted_prerequisite() -> None:
    predecessor, _ = _payload()
    success, success_raw = _read_json_bytes(SUCCESS)
    scope = expected_plan_authorization_scope()
    del scope["tracked_read_set"]["l3_v1_failure"]

    with pytest.raises(
        MulticityPlanFreezeTransitionV7Error,
        match="exact tracked read set",
    ):
        _build_v7_payload(
            predecessor,
            predecessor_bytes=V6_BYTES,
            l3_success=success,
            l3_success_bytes=len(success_raw),
            precondition_commit="a" * 40,
            code_files=_synthetic_code_files(),
            authorization_scope=scope,
        )


def test_v7_rejects_resigned_successor_tamper() -> None:
    _, payload = _payload()
    changed = deepcopy(payload)
    changed["authorized_now"]["predictor_construction"] = True
    changed["commit_sha256"] = canonical_sha256(
        {key: value for key, value in changed.items() if key != "commit_sha256"}
    )

    with pytest.raises(
        MulticityPlanFreezeTransitionV7Error,
        match="complete reconstructed payload",
    ):
        _validate_exact_v7_payload(changed, payload)


def test_v7_rejects_precondition_where_plan_was_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        plan_v7,
        "_git_regular_blob",
        lambda *args, **kwargs: (b"changed-plan", "1" * 40, "100644"),
    )

    with pytest.raises(
        MulticityPlanFreezeTransitionV7Error,
        match="not the exact v6 predecessor",
    ):
        _require_exact_precondition_plan(
            ROOT,
            precondition_commit="a" * 40,
            predecessor_raw=b"exact-v6",
        )


def test_v7_rejects_plan_tamper_then_restore_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = "b" * 40
    monkeypatch.setattr(
        plan_v7,
        "_run_git",
        lambda *args, **kwargs: f"{publication}\n{'c' * 40}\n",
    )

    with pytest.raises(
        MulticityPlanFreezeTransitionV7Error,
        match="outside the one v6-to-v7 publication",
    ):
        _require_v7_plan_history(
            ROOT,
            publication_commit=publication,
            published_raw=b"v7",
            current_head=None,
        )


def test_v7_rejects_publication_code_substitution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        plan_v7,
        "_code_records_at_commit",
        lambda *args, **kwargs: {"runtime.py": {"sha256": "changed"}},
    )

    with pytest.raises(
        MulticityPlanFreezeTransitionV7Error,
        match="changed a frozen transition code blob",
    ):
        _require_publication_code_files(
            ROOT,
            publication_commit="b" * 40,
            expected_code_files={"runtime.py": {"sha256": "expected"}},
        )


def test_v7_rejects_noncanonical_destination(tmp_path: Path) -> None:
    with pytest.raises(
        MulticityPlanFreezeTransitionV7Error,
        match="only replace canonical",
    ):
        authorize_multicity_water_distance_freeze(
            project_root=ROOT,
            output_path=tmp_path / "PLAN_READINESS.json",
        )


def test_v7_module_has_no_geometry_source_or_target_reader_imports() -> None:
    source = MODULE.read_text(encoding="utf-8")
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
    assert "ZipFile(" not in source
    assert "read_parquet(" not in source
    assert "read_file(" not in source
