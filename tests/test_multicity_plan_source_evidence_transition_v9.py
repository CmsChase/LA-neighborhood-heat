from __future__ import annotations

import ast
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

import la_heat.multicity.plan_source_evidence_transition_v9 as plan_v9
from la_heat.multicity.plan_predictor_contract_transition_v8 import (
    authenticate_historical_v8_payload,
)
from la_heat.multicity.plan_source_evidence_transition_v9 import (
    AUTHORIZED_NOW,
    BLOCKERS_BEFORE_PREDICTOR_BUILD,
    EXPECTED_TRANSITION_DIFF_PATHS,
    LOCKS,
    NEXT_SAFE_STAGE,
    PLAN_PATH,
    PLANNING_STAGE,
    V1_FILE_SHA256,
    V1_INTERNAL_COMMIT_SHA256,
    V1_PUBLICATION_COMMIT,
    V1_TERMINAL_PATH,
    V8_FILE_SHA256,
    V8_INTERNAL_COMMIT_SHA256,
    V8_PUBLICATION_COMMIT,
    MulticityPlanSourceEvidenceTransitionV9Error,
    _build_v9_payload,
    _git_preflight,
    _git_regular_blob,
    _recursive_diff_paths,
    _require_v1_history,
    _require_v1_runtime_history,
    _require_v9_history,
    _validate_exact_v9_payload,
    _validate_v1_terminal,
    _validate_v8,
    authorize_multicity_source_evidence_stage,
)
from la_heat.multicity.portable_predictor_contract_freeze_v1 import (
    EXPECTED_BLOCKERS as V1_EXPECTED_BLOCKERS,
)
from la_heat.multicity.portable_predictor_source_evidence_v1 import (
    expected_plan_authorization_scope,
)

ROOT = Path(__file__).parents[1]
MODULE = ROOT / "src" / "la_heat" / "multicity" / "plan_source_evidence_transition_v9.py"


def _historical_json(commit: str, path: str) -> tuple[dict[str, object], bytes]:
    raw, _, _ = _git_regular_blob(ROOT, commit=commit, relative_path=path)
    payload = json.loads(raw)
    assert isinstance(payload, dict)
    return payload, raw


def _synthetic_code_files() -> dict[str, dict[str, object]]:
    scope = expected_plan_authorization_scope()
    configuration = scope["configuration"]
    assert isinstance(configuration, dict)
    config_path = configuration["path"]
    records: dict[str, dict[str, object]] = {}
    for path in plan_v9.transition_code_paths():
        raw = (ROOT / path).read_bytes()
        records[path] = {
            "sha256": (
                configuration["sha256"] if path == config_path else hashlib.sha256(raw).hexdigest()
            ),
            "bytes": len(raw),
            "git_blob_oid": "1" * 40,
            "git_mode": "100644",
        }
    return records


def _payload() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    predecessor, predecessor_raw = _historical_json(V8_PUBLICATION_COMMIT, PLAN_PATH)
    terminal, terminal_raw = _historical_json(V1_PUBLICATION_COMMIT, V1_TERMINAL_PATH)
    _validate_v8(predecessor, predecessor_raw)
    _validate_v1_terminal(terminal, terminal_raw)
    payload = _build_v9_payload(
        predecessor,
        predecessor_bytes=len(predecessor_raw),
        terminal=terminal,
        terminal_bytes=len(terminal_raw),
        precondition_commit="a" * 40,
        code_files=_synthetic_code_files(),
    )
    return predecessor, terminal, payload


def test_v9_changes_only_three_permissions_and_no_locks() -> None:
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
    assert payload["locks"] == LOCKS == predecessor["locks"]
    assert AUTHORIZED_NOW["boundary_and_public_metadata_staging"] is False
    assert AUTHORIZED_NOW["portable_predictor_source_and_calibration_contract_freeze"] is False
    assert AUTHORIZED_NOW["portable_predictor_missing_source_evidence_staging"] is True
    assert AUTHORIZED_NOW["predictor_construction"] is False


def test_v9_identity_scope_and_three_completion_blockers_are_exact() -> None:
    _, _, payload = _payload()

    assert payload["schema_version"] == 9
    assert payload["planning_stage"] == PLANNING_STAGE
    assert payload["next_safe_stage"] == NEXT_SAFE_STAGE
    assert payload["blockers_before_predictor_build"] == list(BLOCKERS_BEFORE_PREDICTOR_BUILD)
    assert len(BLOCKERS_BEFORE_PREDICTOR_BUILD) == 3
    assert BLOCKERS_BEFORE_PREDICTOR_BUILD[0].startswith("complete_missing_")
    assert "predictor_contract_freeze_authorization_scope" not in payload
    assert (
        payload["portable_predictor_source_evidence_stage_authorization_scope"]
        == expected_plan_authorization_scope()
    )
    consumed = payload["consumed_predictor_contract_freeze_v1_authorization"]
    assert consumed["status"] == "consumed_and_closed_after_deferred_v1"
    assert consumed["completion_manifest_file_sha256"] == V1_FILE_SHA256
    assert consumed["completion_manifest_commit_sha256"] == (V1_INTERNAL_COMMIT_SHA256)
    assert consumed["observed_blockers"] == list(V1_EXPECTED_BLOCKERS)


def test_v9_binds_exact_historical_v8_and_v1_identities() -> None:
    predecessor, predecessor_raw = _historical_json(V8_PUBLICATION_COMMIT, PLAN_PATH)
    terminal, terminal_raw = _historical_json(V1_PUBLICATION_COMMIT, V1_TERMINAL_PATH)

    assert hashlib.sha256(predecessor_raw).hexdigest() == V8_FILE_SHA256
    assert predecessor["commit_sha256"] == V8_INTERNAL_COMMIT_SHA256
    assert hashlib.sha256(terminal_raw).hexdigest() == V1_FILE_SHA256
    assert terminal["commit_sha256"] == V1_INTERNAL_COMMIT_SHA256
    _validate_v8(predecessor, predecessor_raw)
    _validate_v1_terminal(terminal, terminal_raw)
    authenticate_historical_v8_payload(
        ROOT,
        predecessor,
        publication_commit=V8_PUBLICATION_COMMIT,
        current_head=ROOT_HEAD,
    )
    _require_v1_history(
        ROOT,
        terminal_raw=terminal_raw,
        current_head=ROOT_HEAD,
    )
    _require_v1_runtime_history(
        ROOT,
        terminal=terminal,
        current_head=ROOT_HEAD,
    )


def test_v9_rejects_resigned_v1_terminal_tamper() -> None:
    terminal, _ = _historical_json(V1_PUBLICATION_COMMIT, V1_TERMINAL_PATH)
    changed = deepcopy(terminal)
    changed["evidence_gaps"]["observed_blockers"] = []
    changed["commit_sha256"] = plan_v9._canonical_sha256(
        {key: value for key, value in changed.items() if key != "commit_sha256"}
    )
    raw = json.dumps(changed, indent=2).encode("utf-8")

    with pytest.raises(
        MulticityPlanSourceEvidenceTransitionV9Error,
        match="terminal bytes changed",
    ):
        _validate_v1_terminal(changed, raw)


def test_v9_rejects_resigned_successor_tamper() -> None:
    _, _, payload = _payload()
    changed = deepcopy(payload)
    changed["authorized_now"]["model_fitting"] = True
    changed["commit_sha256"] = plan_v9._canonical_sha256(
        {key: value for key, value in changed.items() if key != "commit_sha256"}
    )

    with pytest.raises(
        MulticityPlanSourceEvidenceTransitionV9Error,
        match="complete reconstructed payload",
    ):
        _validate_exact_v9_payload(changed, payload)


def test_v9_rejects_v1_tamper_then_restore_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        plan_v9,
        "_run_git",
        lambda *args, **kwargs: (
            f"{V1_PUBLICATION_COMMIT} {'a' * 40}\n"
            if args[1] == "rev-list"
            else (
                f"A\0{V1_TERMINAL_PATH}\0".encode()
                if args[1] == "diff-tree"
                else f"{V1_PUBLICATION_COMMIT}\n{'b' * 40}\n"
            )
        ),
    )

    with pytest.raises(
        MulticityPlanSourceEvidenceTransitionV9Error,
        match="direct child|unique append-only",
    ):
        _require_v1_history(ROOT, terminal_raw=b"v1", current_head="c" * 40)


def test_v9_rejects_v1_runtime_tamper_then_restore_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal, _ = _historical_json(V1_PUBLICATION_COMMIT, V1_TERMINAL_PATH)
    runtime = terminal["code_runtime"]
    assert isinstance(runtime, dict)
    paths = runtime["relative_paths"]
    assert isinstance(paths, list)
    raw, oid, mode = _git_regular_blob(
        ROOT,
        commit=plan_v9.V1_IMPLEMENTATION_COMMIT,
        relative_path=paths[0],
    )
    monkeypatch.setattr(
        plan_v9,
        "_git_regular_blob",
        lambda *args, **kwargs: (raw, oid, mode),
    )
    monkeypatch.setattr(
        plan_v9,
        "_run_git",
        lambda *args, **kwargs: "b" * 40 + "\n",
    )

    with pytest.raises(
        MulticityPlanSourceEvidenceTransitionV9Error,
        match="modified after implementation",
    ):
        _require_v1_runtime_history(
            ROOT,
            terminal=terminal,
            current_head="c" * 40,
        )


def test_v9_rejects_publication_with_extra_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = "b" * 40
    precondition = "a" * 40

    def fake_git(_root: Path, *args: str, **_kwargs: object) -> str | bytes:
        if args[0] == "rev-list":
            return f"{publication} {precondition}\n"
        if args[0] == "diff-tree":
            return f"M\0{PLAN_PATH}\0A\0data/hidden-target.parquet\0".encode()
        raise AssertionError(args)

    monkeypatch.setattr(plan_v9, "_run_git", fake_git)

    with pytest.raises(
        MulticityPlanSourceEvidenceTransitionV9Error,
        match="outside its exact allowlist",
    ):
        _require_v9_history(
            ROOT,
            publication_commit=publication,
            precondition_commit=precondition,
            published_raw=b"v9",
            current_head=publication,
        )


def test_v9_preflight_detects_hidden_worktree_modification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_git(_root: Path, *args: str, **_kwargs: object) -> str:
        if args[:2] == ("branch", "--show-current"):
            return "main\n"
        if args[:2] in {("rev-parse", "HEAD"), ("rev-parse", "origin/main")}:
            return "a" * 40 + "\n"
        if args[0] == "status":
            return ""
        if args[0] == "hash-object":
            return "2" * 40 + "\n"
        raise AssertionError(args)

    monkeypatch.setattr(plan_v9, "_run_git", fake_git)
    monkeypatch.setattr(plan_v9, "_is_ancestor", lambda *args: True)
    monkeypatch.setattr(
        plan_v9,
        "_git_regular_blob",
        lambda *args, **kwargs: (b"tracked", "1" * 40, "100644"),
    )

    with pytest.raises(
        MulticityPlanSourceEvidenceTransitionV9Error,
        match="differs from HEAD",
    ):
        _git_preflight(ROOT, required_paths=(PLAN_PATH,))


def test_v9_rejects_noncanonical_destination(tmp_path: Path) -> None:
    with pytest.raises(
        MulticityPlanSourceEvidenceTransitionV9Error,
        match="only canonical",
    ):
        authorize_multicity_source_evidence_stage(
            project_root=ROOT,
            output_path=tmp_path / "PLAN_READINESS.json",
        )


def test_v9_transition_module_has_no_network_or_data_reader_imports() -> None:
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
    assert "requests." not in source
    assert "urlopen(" not in source
    assert "read_parquet(" not in source
    assert "read_file(" not in source


ROOT_HEAD = (
    plan_v9._run_git(ROOT, "rev-parse", "HEAD").strip()  # type: ignore[union-attr]
)
