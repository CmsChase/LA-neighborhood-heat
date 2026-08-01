from __future__ import annotations

import ast
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

import la_heat.multicity.plan_predictor_contract_transition_v8 as plan_v8
from la_heat.multicity.plan_predictor_contract_transition_v8 import (
    ALGORITHM_VERSION,
    AUTHORIZED_NOW,
    BLOCKERS_BEFORE_PREDICTOR_BUILD,
    EXPECTED_TRANSITION_DIFF_PATHS,
    LOCKS,
    NEXT_SAFE_STAGE,
    PLAN_PATH,
    PLANNING_STAGE,
    TRANSITION_ACCESS_CONTRACT,
    TRANSITION_CODE_PATHS,
    V2_FILE_SHA256,
    V2_INTERNAL_COMMIT_SHA256,
    V2_PUBLICATION_COMMIT,
    V2_TERMINAL_PATH,
    V7_PUBLICATION_COMMIT,
    MulticityPlanPredictorContractTransitionV8Error,
    _build_v8_payload,
    _git_preflight,
    _git_regular_blob,
    _recursive_diff_paths,
    _require_frozen_v2_runtime_history,
    _require_publication_code_files,
    _require_v2_terminal_history,
    _require_v8_plan_history,
    _validate_exact_v8_payload,
    _validate_v2_terminal,
    _validate_v7_predecessor,
    authorize_multicity_predictor_contract_freeze,
)
from la_heat.multicity.portable_predictor_contract_freeze_v1 import (
    expected_plan_authorization_scope,
)

ROOT = Path(__file__).parents[1]
MODULE = (
    ROOT
    / "src"
    / "la_heat"
    / "multicity"
    / "plan_predictor_contract_transition_v8.py"
)


def _historical_json(commit: str, path: str) -> tuple[dict[str, object], bytes]:
    raw, _, _ = _git_regular_blob(ROOT, commit=commit, relative_path=path)
    payload = json.loads(raw)
    assert isinstance(payload, dict)
    return payload, raw


def _synthetic_code_files(
    terminal: dict[str, object],
) -> dict[str, dict[str, object]]:
    runtime = terminal["code_runtime"]
    assert isinstance(runtime, dict)
    runtime_files = runtime["files"]
    assert isinstance(runtime_files, dict)
    records: dict[str, dict[str, object]] = {}
    for path in TRANSITION_CODE_PATHS:
        raw = (ROOT / path).read_bytes()
        records[path] = {
            "sha256": runtime_files.get(path, hashlib.sha256(raw).hexdigest()),
            "bytes": len(raw),
            "git_blob_oid": "1" * 40,
            "git_mode": "100644",
        }
    return records


def _payload() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    predecessor, predecessor_raw = _historical_json(
        V7_PUBLICATION_COMMIT,
        PLAN_PATH,
    )
    terminal, terminal_raw = _historical_json(
        V2_PUBLICATION_COMMIT,
        V2_TERMINAL_PATH,
    )
    _validate_v7_predecessor(predecessor, predecessor_raw)
    _validate_v2_terminal(terminal, terminal_raw)
    payload = _build_v8_payload(
        predecessor,
        predecessor_bytes=len(predecessor_raw),
        terminal=terminal,
        terminal_bytes=len(terminal_raw),
        precondition_commit="a" * 40,
        code_files=_synthetic_code_files(terminal),
    )
    return predecessor, terminal, payload


def test_v8_changes_only_two_permissions_and_two_locks() -> None:
    predecessor, _, payload = _payload()

    changes = _recursive_diff_paths(
        {
            "authorized_now": predecessor["authorized_now"],
            "locks": predecessor["locks"],
        },
        {
            "authorized_now": payload["authorized_now"],
            "locks": payload["locks"],
        },
    )

    assert changes == EXPECTED_TRANSITION_DIFF_PATHS
    assert payload["authorized_now"] == AUTHORIZED_NOW
    assert payload["locks"] == LOCKS
    assert AUTHORIZED_NOW["portable_predictor_source_freeze"] is False
    assert (
        AUTHORIZED_NOW[
            "portable_predictor_source_and_calibration_contract_freeze"
        ]
        is True
    )


def test_v8_canonicalizes_only_source_and_algorithm_locks() -> None:
    _, _, payload = _payload()

    assert LOCKS["portable_water_distance_source_locked"] is True
    assert LOCKS["portable_water_distance_algorithm_locked"] is True
    assert LOCKS["portable_water_distance_feature_names_frozen"] is False
    assert LOCKS["predictor_build_authorized"] is False
    assert LOCKS["protocol_locked"] is False
    assert LOCKS["protocol_lock_created"] is False
    assert LOCKS["external_targets_unlocked"] is False
    assert payload["algorithm_version"] == ALGORITHM_VERSION
    assert payload["planning_stage"] == PLANNING_STAGE
    assert payload["next_safe_stage"] == NEXT_SAFE_STAGE


def test_v8_consumes_v2_and_keeps_predictor_build_blocked() -> None:
    _, _, payload = _payload()
    consumed = payload["consumed_portable_water_distance_freeze_authorization"]

    assert consumed["status"] == "consumed_and_closed"
    assert consumed["completion_manifest_file_sha256"] == V2_FILE_SHA256
    assert consumed["completion_manifest_commit_sha256"] == (
        V2_INTERNAL_COMMIT_SHA256
    )
    assert consumed["decision_permission_now"] is False
    assert payload["blockers_before_predictor_build"] == list(
        BLOCKERS_BEFORE_PREDICTOR_BUILD
    )
    assert "freeze_portable_water_distance_source_and_algorithm" not in payload[
        "blockers_before_predictor_build"
    ]
    assert "freeze_decision_authorization_scope" not in payload
    contract_lock = payload["portable_water_distance_contract_lock"]
    assert contract_lock["source_locked"] is True
    assert contract_lock["point_distance_algorithm_locked"] is True
    assert contract_lock["tract_aggregation_frozen"] is False
    assert contract_lock["feature_names_frozen"] is False


def test_v8_future_scope_is_narrow_and_requires_another_transition() -> None:
    _, _, payload = _payload()
    scope = payload["predictor_contract_freeze_authorization_scope"]

    assert scope == expected_plan_authorization_scope()
    assert scope["append_only_output"] is True
    assert scope["network_or_download_allowed"] is False
    assert scope["eligible_land_or_predictor_value_read_allowed"] is False
    assert scope["predictor_construction_allowed"] is False
    assert scope["model_target_or_result_read_allowed"] is False
    assert scope["protocol_promotion_allowed"] is False
    assert scope["decision_runtime_paths"] == list(TRANSITION_CODE_PATHS)
    assert "*" not in json.dumps(scope, sort_keys=True)


def test_v8_transition_access_is_data_blind() -> None:
    _, _, payload = _payload()

    assert payload["transition_access_contract"] == TRANSITION_ACCESS_CONTRACT
    assert TRANSITION_ACCESS_CONTRACT["network_requests"] == 0
    for key, value in TRANSITION_ACCESS_CONTRACT.items():
        if key.endswith("opened") or key.endswith("computed") or key.endswith(
            "performed"
        ):
            assert value is False


def test_v8_authenticates_exact_published_v2_terminal() -> None:
    _, terminal_raw = _historical_json(
        V2_PUBLICATION_COMMIT,
        V2_TERMINAL_PATH,
    )
    terminal = json.loads(terminal_raw)

    _validate_v2_terminal(terminal, terminal_raw)


def test_v8_rejects_resigned_terminal_tamper() -> None:
    terminal, _ = _historical_json(
        V2_PUBLICATION_COMMIT,
        V2_TERMINAL_PATH,
    )
    changed = deepcopy(terminal)
    changed["locks"]["predictor_build_authorized"] = True
    changed["commit_sha256"] = plan_v8._canonical_sha256(
        {key: value for key, value in changed.items() if key != "commit_sha256"}
    )
    raw = json.dumps(changed, indent=2).encode("utf-8")

    with pytest.raises(
        MulticityPlanPredictorContractTransitionV8Error,
        match="terminal bytes changed",
    ):
        _validate_v2_terminal(changed, raw)


def test_v8_rejects_resigned_successor_tamper() -> None:
    _, _, payload = _payload()
    changed = deepcopy(payload)
    changed["authorized_now"]["predictor_construction"] = True
    changed["commit_sha256"] = plan_v8._canonical_sha256(
        {key: value for key, value in changed.items() if key != "commit_sha256"}
    )

    with pytest.raises(
        MulticityPlanPredictorContractTransitionV8Error,
        match="complete reconstructed payload",
    ):
        _validate_exact_v8_payload(changed, payload)


def test_v8_rejects_terminal_tamper_then_restore_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        plan_v8,
        "_run_git",
        lambda *args, **kwargs: f"{V2_PUBLICATION_COMMIT}\n{'b' * 40}\n",
    )

    with pytest.raises(
        MulticityPlanPredictorContractTransitionV8Error,
        match="one unique append-only publication",
    ):
        _require_v2_terminal_history(
            ROOT,
            terminal_raw=b"terminal",
            current_head="c" * 40,
        )


def test_v8_rejects_frozen_runtime_tamper_then_restore_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, terminal, _ = _payload()
    monkeypatch.setattr(plan_v8, "V2_RUNTIME_PATHS", ("runtime.py",))
    monkeypatch.setattr(
        plan_v8,
        "_run_git",
        lambda *args, **kwargs: "b" * 40 + "\n",
    )

    with pytest.raises(
        MulticityPlanPredictorContractTransitionV8Error,
        match="modified after its implementation",
    ):
        _require_frozen_v2_runtime_history(
            ROOT,
            terminal=terminal,
            current_head="c" * 40,
        )


def test_v8_rejects_plan_tamper_then_restore_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = "b" * 40
    precondition = "a" * 40

    def fake_git(_root: Path, *args: str, **_kwargs: object) -> str | bytes:
        if args[0] == "rev-list":
            return f"{publication} {precondition}\n"
        if args[0] == "diff-tree":
            return f"M\0{PLAN_PATH}\0".encode()
        if args[0] == "log" and V7_PUBLICATION_COMMIT in args[2]:
            return f"{publication}\n{'c' * 40}\n"
        return ""

    monkeypatch.setattr(plan_v8, "_run_git", fake_git)
    monkeypatch.setattr(plan_v8, "_is_ancestor", lambda *args: True)

    with pytest.raises(
        MulticityPlanPredictorContractTransitionV8Error,
        match="outside the one v7-to-v8 publication",
    ):
        _require_v8_plan_history(
            ROOT,
            publication_commit=publication,
            precondition_commit=precondition,
            published_raw=b"v8",
            current_head=publication,
        )


def test_v8_rejects_publication_commit_with_any_extra_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = "b" * 40
    precondition = "a" * 40

    def fake_git(_root: Path, *args: str, **_kwargs: object) -> str | bytes:
        if args[0] == "rev-list":
            return f"{publication} {precondition}\n"
        if args[0] == "diff-tree":
            return (
                f"M\0{PLAN_PATH}\0A\0data/hidden-target.parquet\0".encode()
            )
        raise AssertionError(args)

    monkeypatch.setattr(plan_v8, "_run_git", fake_git)

    with pytest.raises(
        MulticityPlanPredictorContractTransitionV8Error,
        match="modify only canonical",
    ):
        _require_v8_plan_history(
            ROOT,
            publication_commit=publication,
            precondition_commit=precondition,
            published_raw=b"v8",
            current_head=publication,
        )


def test_v8_rejects_publication_code_substitution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        plan_v8,
        "_code_records_at_commit",
        lambda *args, **kwargs: {"runtime.py": {"sha256": "changed"}},
    )

    with pytest.raises(
        MulticityPlanPredictorContractTransitionV8Error,
        match="changed a frozen transition code blob",
    ):
        _require_publication_code_files(
            ROOT,
            publication_commit="b" * 40,
            expected_code_files={"runtime.py": {"sha256": "expected"}},
        )


def test_v8_preflight_detects_hidden_worktree_modification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_git(_root: Path, *args: str, **_kwargs: object) -> str:
        if args[:2] == ("branch", "--show-current"):
            return "main\n"
        if args[:2] == ("rev-parse", "HEAD"):
            return "a" * 40 + "\n"
        if args[:2] == ("rev-parse", "origin/main"):
            return "a" * 40 + "\n"
        if args[0] == "status":
            return ""
        if args[0] == "hash-object":
            return "2" * 40 + "\n"
        raise AssertionError(args)

    monkeypatch.setattr(plan_v8, "_run_git", fake_git)
    monkeypatch.setattr(plan_v8, "_is_ancestor", lambda *args: True)
    monkeypatch.setattr(
        plan_v8,
        "_git_regular_blob",
        lambda *args, **kwargs: (b"tracked", "1" * 40, "100644"),
    )

    with pytest.raises(
        MulticityPlanPredictorContractTransitionV8Error,
        match="visibility flag",
    ):
        _git_preflight(ROOT, required_paths=("runtime.py",))


def test_v8_rejects_noncanonical_destination(tmp_path: Path) -> None:
    with pytest.raises(
        MulticityPlanPredictorContractTransitionV8Error,
        match="only replace canonical",
    ):
        authorize_multicity_predictor_contract_freeze(
            project_root=ROOT,
            output_path=tmp_path / "PLAN_READINESS.json",
        )


def test_v8_module_has_no_data_or_target_reader_imports() -> None:
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
