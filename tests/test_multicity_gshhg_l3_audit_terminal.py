from __future__ import annotations

import copy
import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pandas as pd
import pytest

import la_heat.multicity.gshhg_l3_hierarchy_audit as l3_audit

FALSE_LOCK_FIELDS = (
    "source_lock_created",
    "algorithm_lock_created",
    "feature_names_frozen",
    "predictor_build_authorized",
    "protocol_lock_created",
    "external_targets_unlocked",
    "external_target_values_read",
    "external_prediction_commit_exists",
)

FALSE_ACCESS_FIELDS = (
    "gshhg_l4_member_opened",
    "census_layer_opened",
    "other_public_source_geometry_opened",
    "eligible_land_grid_opened",
    "distance_feature_surface_computed",
    "tract_aggregation_performed",
    "predictor_values_computed",
    "predictor_construction_performed",
    "model_fit_performed",
    "model_predictions_computed",
    "landsat_thermal_values_read",
    "landsat_target_qa_values_read",
    "external_target_files_opened",
    "final_evaluation_outputs_opened",
    "geometry_exported_or_redistributed",
)


@dataclass
class TerminalHarness:
    root: Path
    config_path: Path
    config: dict[str, Any]
    success_path: Path
    failure_path: Path
    table_path: Path
    preregistration: dict[str, Any]
    plan: dict[str, Any]
    hash_overrides: dict[Path, str]
    code_hashes: dict[str, str]
    git_gate_mock: Mock
    plan_mock: Mock
    runtime_mock: Mock


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _blob_id(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8"), usedforsecurity=False).hexdigest()


def _internal_commit(payload: dict[str, Any]) -> dict[str, Any]:
    payload["commit_sha256"] = l3_audit.canonical_sha256(
        {key: value for key, value in payload.items() if key != "commit_sha256"}
    )
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _mutated_terminal(
    payload: dict[str, Any],
    dotted_path: str,
    replacement: object,
    *,
    rehash_code_runtime: bool = False,
) -> dict[str, Any]:
    changed = copy.deepcopy(payload)
    parts = dotted_path.split(".")
    target: dict[str, Any] = changed
    for part in parts[:-1]:
        nested = target[part]
        assert isinstance(nested, dict)
        target = nested
    target[parts[-1]] = replacement
    if rehash_code_runtime:
        runtime = changed["code_runtime"]
        assert isinstance(runtime, dict)
        runtime["sha256"] = l3_audit.canonical_sha256(
            {key: value for key, value in runtime.items() if key != "sha256"}
        )
    previous_commit = changed.get("commit_sha256")
    _internal_commit(changed)
    assert changed["commit_sha256"] != previous_commit
    return changed


def _terminal_commit_is_valid(payload: dict[str, Any]) -> bool:
    return payload.get("commit_sha256") == l3_audit.canonical_sha256(
        {key: value for key, value in payload.items() if key != "commit_sha256"}
    )


@pytest.fixture
def terminal_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TerminalHarness:
    root = tmp_path.resolve()
    config_path = (root / l3_audit.CODE_PATHS[0]).resolve()
    plan_path = (root / l3_audit.PLAN_PATH).resolve()
    preregistration_path = (root / l3_audit.PREREGISTRATION_PATH).resolve()
    success_path = (root / l3_audit.DEFAULT_MANIFEST).resolve()
    failure_path = (root / l3_audit.DEFAULT_FAILURE_MANIFEST).resolve()
    table_path = (root / l3_audit.DEFAULT_DIAGNOSTIC_TABLE).resolve()
    config: dict[str, Any] = {
        "source": {
            "expected_archive_sha256": "a" * 64,
            "expected_archive_bytes": 123_456,
        },
        "outputs": {
            "success_manifest": l3_audit.DEFAULT_MANIFEST.as_posix(),
            "v1_failure_manifest": l3_audit.DEFAULT_FAILURE_MANIFEST.as_posix(),
            "diagnostic_table": l3_audit.DEFAULT_DIAGNOSTIC_TABLE.as_posix(),
        },
    }
    preregistration = {
        "commit_sha256": l3_audit.EXPECTED_PREREGISTRATION_COMMIT_SHA256,
        "preregistration_id": "target_blind_gshhg_l3_hierarchy_audit_v1",
    }
    plan = {
        "schema_version": 6,
        "commit_sha256": l3_audit.EXPECTED_PLAN_COMMIT_SHA256,
        "next_safe_stage": "target_blind_gshhg_l3_hierarchy_geometry_audit",
        "authorized_now": {
            "target_blind_gshhg_l3_hierarchy_geometry_read": True,
        },
    }
    hash_overrides = {
        config_path: l3_audit.EXPECTED_CONFIG_SHA256,
        plan_path: l3_audit.EXPECTED_PLAN_FILE_SHA256,
        preregistration_path: l3_audit.EXPECTED_PREREGISTRATION_FILE_SHA256,
    }
    code_hashes = {
        relative: (
            l3_audit.EXPECTED_CONFIG_SHA256
            if relative == l3_audit.CODE_PATHS[0]
            else _digest_text(f"synthetic executor bytes: {relative}")
        )
        for relative in l3_audit.CODE_PATHS
    }

    monkeypatch.setattr(
        l3_audit,
        "_read_config",
        lambda _path: (root, config_path, config),
    )

    def fake_sha256(path: str | Path) -> str:
        resolved = Path(path).resolve()
        if resolved in hash_overrides:
            return hash_overrides[resolved]
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError:
            relative = ""
        if relative in code_hashes:
            return code_hashes[relative]
        if resolved.is_file():
            return hashlib.sha256(resolved.read_bytes()).hexdigest()
        raise AssertionError(f"Unexpected hash request in synthetic terminal test: {resolved}")

    monkeypatch.setattr(l3_audit, "sha256_file", fake_sha256)
    original_json_reader = l3_audit._read_json_object

    def fake_json_reader(
        path: Path,
        *,
        label: str,
    ) -> tuple[dict[str, Any], str]:
        resolved = path.resolve()
        if resolved == preregistration_path:
            return copy.deepcopy(preregistration), hash_overrides[preregistration_path]
        return original_json_reader(resolved, label=label)

    monkeypatch.setattr(l3_audit, "_read_json_object", fake_json_reader)
    plan_mock = Mock(side_effect=lambda *_args, **_kwargs: copy.deepcopy(plan))
    monkeypatch.setattr(l3_audit, "audit_multicity_plan", plan_mock)
    monkeypatch.setattr(
        l3_audit,
        "_required_git_paths",
        lambda _root: tuple(l3_audit.CODE_PATHS),
    )

    def fake_git_gate(
        _root: Path,
        *,
        required_paths: tuple[str, ...],
    ) -> l3_audit.GitGate:
        return l3_audit.GitGate(
            head="1" * 40,
            branch="main",
            origin_main="1" * 40,
            tracked_blob_sha1={
                relative: _blob_id(relative) for relative in required_paths
            },
        )

    git_gate_mock = Mock(side_effect=fake_git_gate)
    monkeypatch.setattr(l3_audit, "_git_blob_records", git_gate_mock)
    monkeypatch.setattr(
        l3_audit.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="",
        ),
    )

    def fake_runtime_fingerprint(
        *,
        project_root: Path,
        relative_paths: tuple[str, ...],
        algorithm_version: str,
    ) -> tuple[str, dict[str, Any]]:
        assert project_root == root
        assert relative_paths == l3_audit.CODE_PATHS
        runtime = {
            "algorithm_version": algorithm_version,
            "python": "3.synthetic",
            "packages": {
                "geopandas": "synthetic",
                "numpy": "synthetic",
                "pandas": "synthetic",
                "shapely": "synthetic",
            },
            "files": dict(code_hashes),
        }
        return l3_audit.canonical_sha256(runtime), runtime

    runtime_mock = Mock(side_effect=fake_runtime_fingerprint)
    monkeypatch.setattr(l3_audit, "code_runtime_fingerprint", runtime_mock)
    monkeypatch.setattr(
        l3_audit.importlib.metadata,
        "version",
        lambda package: f"synthetic-{package}",
    )

    return TerminalHarness(
        root=root,
        config_path=config_path,
        config=config,
        success_path=success_path,
        failure_path=failure_path,
        table_path=table_path,
        preregistration=preregistration,
        plan=plan,
        hash_overrides=hash_overrides,
        code_hashes=code_hashes,
        git_gate_mock=git_gate_mock,
        plan_mock=plan_mock,
        runtime_mock=runtime_mock,
    )


def _run_gate(harness: TerminalHarness) -> l3_audit.GitGate:
    return l3_audit.GitGate(
        head="1" * 40,
        branch="main",
        origin_main="1" * 40,
        tracked_blob_sha1={
            relative: _blob_id(relative) for relative in l3_audit.CODE_PATHS
        },
    )


def _success_payload(harness: TerminalHarness) -> dict[str, Any]:
    table = pd.DataFrame(
        [
            {
                "point_id": "fixed_a",
                "point_kind": "fixed_v2_replay",
                "distance_m": 120.25,
                "l1_l2_only_distance_m": 120.25,
            },
            {
                "point_id": "probe_180507",
                "point_kind": "selected_l3_probe",
                "distance_m": 12.5,
                "l1_l2_only_distance_m": 87.75,
            },
        ]
    )
    table_bytes = table.to_csv(index=False, lineterminator="\n").encode("utf-8")
    harness.table_path.parent.mkdir(parents=True, exist_ok=True)
    harness.table_path.write_bytes(table_bytes)
    bundle = SimpleNamespace(
        archive_audit={
            "sha256": harness.config["source"]["expected_archive_sha256"],
            "bytes": harness.config["source"]["expected_archive_bytes"],
            "authorized_member_count": len(l3_audit.AUTHORIZED_MEMBERS),
            "member_open_log": list(l3_audit.AUTHORIZED_MEMBERS),
            "unauthorized_member_open_count": 0,
            "zipfile_testzip_called": False,
        },
        layer_audit={
            "L1": {"all_layer_gates_passed": True},
            "L2": {"all_layer_gates_passed": True},
            "L3": {"all_layer_gates_passed": True},
        },
        hierarchy_audit={"all_structural_gates_passed": True},
    )
    return l3_audit._success_payload(
        project_root=harness.root,
        config_path=harness.config_path,
        config=harness.config,
        preregistration=harness.preregistration,
        git_gate=_run_gate(harness),
        bundle=bundle,
        numerical_audit={"all_numerical_gates_passed": True},
        table_path=harness.table_path,
        table_bytes=table_bytes,
        table=table,
    )


def _failure_payload(harness: TerminalHarness) -> dict[str, Any]:
    return l3_audit._failure_payload(
        l3_audit.StructuralAuditError(
            "synthetic_structure_gate",
            expected="synthetic expected structure",
            observed="synthetic observed structure",
        ),
        phase="phase_1_structure",
        project_root=harness.root,
        config_path=harness.config_path,
        git_gate=_run_gate(harness),
        preregistration=harness.preregistration,
        phase_evidence={
            "archive_path": (
                "data/raw/multicity/water_distance/gshhg-shp-2.3.7.zip"
            ),
            "phase_1_started": True,
        },
        probe_derived=False,
        distance_values_computed=False,
    )


def _publish_success(harness: TerminalHarness) -> dict[str, Any]:
    payload = _success_payload(harness)
    _write_json(harness.success_path, payload)
    return payload


def _publish_failure(harness: TerminalHarness) -> dict[str, Any]:
    payload = _failure_payload(harness)
    _write_json(harness.failure_path, payload)
    return payload


def test_authenticate_accepts_exact_success_and_calls_repository_gate(
    terminal_harness: TerminalHarness,
) -> None:
    payload = _publish_success(terminal_harness)
    terminal_harness.runtime_mock.reset_mock()

    authenticated = l3_audit.authenticate_l3_audit_terminal(
        terminal_harness.config_path
    )

    assert authenticated == payload
    assert terminal_harness.plan_mock.call_count == 1
    assert terminal_harness.plan_mock.call_args.kwargs == {
        "output_path": terminal_harness.root / l3_audit.PLAN_PATH,
        "write": False,
    }
    assert terminal_harness.git_gate_mock.call_count == 1
    required_paths = terminal_harness.git_gate_mock.call_args.kwargs["required_paths"]
    assert terminal_harness.success_path.relative_to(
        terminal_harness.root
    ).as_posix() in required_paths


def test_authenticate_accepts_exact_failure_and_calls_repository_gate(
    terminal_harness: TerminalHarness,
) -> None:
    payload = _publish_failure(terminal_harness)

    authenticated = l3_audit.authenticate_l3_audit_terminal(
        terminal_harness.config_path
    )

    assert authenticated == payload
    assert terminal_harness.git_gate_mock.call_count == 1
    required_paths = terminal_harness.git_gate_mock.call_args.kwargs["required_paths"]
    assert terminal_harness.failure_path.relative_to(
        terminal_harness.root
    ).as_posix() in required_paths


def test_authenticate_rejects_success_and_failure_dual_terminal(
    terminal_harness: TerminalHarness,
) -> None:
    _publish_success(terminal_harness)
    _publish_failure(terminal_harness)

    with pytest.raises(
        l3_audit.GshhgL3HierarchyAuditError,
        match="cannot both exist",
    ):
        l3_audit.authenticate_l3_audit_terminal(terminal_harness.config_path)

    assert terminal_harness.git_gate_mock.call_count == 0


def test_authenticate_rejects_failure_terminal_beside_diagnostic_table(
    terminal_harness: TerminalHarness,
) -> None:
    _publish_failure(terminal_harness)
    terminal_harness.table_path.parent.mkdir(parents=True, exist_ok=True)
    terminal_harness.table_path.write_text("orphaned,table\n", encoding="utf-8")

    with pytest.raises(
        l3_audit.GshhgL3HierarchyAuditError,
        match="failure terminal cannot coexist",
    ):
        l3_audit.authenticate_l3_audit_terminal(terminal_harness.config_path)


@pytest.mark.parametrize(
    ("identity_path", "replacement"),
    [
        ("config.path", "configs/multicity/not-the-config.toml"),
        ("config.sha256", "0" * 64),
        ("planning_authorization.path", "manifests/multicity/not-the-plan.json"),
        ("planning_authorization.file_sha256", "0" * 64),
        ("planning_authorization.commit_sha256", "0" * 64),
        ("planning_authorization.authorized_stage", "predictors_authorized"),
        (
            "preregistration.path",
            "manifests/multicity/not-the-preregistration.json",
        ),
        ("preregistration.file_sha256", "0" * 64),
        ("preregistration.commit_sha256", "0" * 64),
        ("preregistration.preregistration_id", "different_preregistration"),
    ],
)
def test_success_rejects_recommitted_input_identity_tampering(
    terminal_harness: TerminalHarness,
    identity_path: str,
    replacement: str,
) -> None:
    payload = _success_payload(terminal_harness)
    changed = _mutated_terminal(payload, identity_path, replacement)
    assert _terminal_commit_is_valid(changed)
    _write_json(terminal_harness.success_path, changed)

    with pytest.raises(l3_audit.GshhgL3HierarchyAuditError, match="identit|input"):
        l3_audit.authenticate_l3_audit_terminal(terminal_harness.config_path)


@pytest.mark.parametrize(
    "identity_path",
    [
        "config.path",
        "config.sha256",
        "preregistration.path",
        "preregistration.file_sha256",
        "preregistration.commit_sha256",
    ],
)
def test_failure_rejects_recommitted_input_identity_tampering(
    terminal_harness: TerminalHarness,
    identity_path: str,
) -> None:
    payload = _failure_payload(terminal_harness)
    changed = _mutated_terminal(payload, identity_path, "tampered-identity")
    assert _terminal_commit_is_valid(changed)
    _write_json(terminal_harness.failure_path, changed)

    with pytest.raises(l3_audit.GshhgL3HierarchyAuditError, match="identit|input"):
        l3_audit.authenticate_l3_audit_terminal(terminal_harness.config_path)


@pytest.mark.parametrize("terminal_kind", ["success", "failure"])
@pytest.mark.parametrize(
    ("identity_path", "replacement"),
    [
        ("schema_version", 999),
        ("algorithm_version", "different-terminal-algorithm"),
    ],
)
def test_terminal_rejects_recommitted_schema_or_algorithm_identity(
    terminal_harness: TerminalHarness,
    terminal_kind: str,
    identity_path: str,
    replacement: object,
) -> None:
    payload = (
        _success_payload(terminal_harness)
        if terminal_kind == "success"
        else _failure_payload(terminal_harness)
    )
    changed = _mutated_terminal(payload, identity_path, replacement)
    assert _terminal_commit_is_valid(changed)
    destination = (
        terminal_harness.success_path
        if terminal_kind == "success"
        else terminal_harness.failure_path
    )
    _write_json(destination, changed)

    with pytest.raises(l3_audit.GshhgL3HierarchyAuditError):
        l3_audit.authenticate_l3_audit_terminal(terminal_harness.config_path)


@pytest.mark.parametrize(
    "observed_identity",
    [
        "config_file",
        "plan_file",
        "plan_commit",
        "preregistration_file",
        "preregistration_commit",
    ],
)
def test_authenticate_rechecks_current_config_plan_and_preregistration_identity(
    terminal_harness: TerminalHarness,
    observed_identity: str,
) -> None:
    _publish_success(terminal_harness)
    if observed_identity == "config_file":
        terminal_harness.hash_overrides[
            terminal_harness.config_path
        ] = "0" * 64
    elif observed_identity == "plan_file":
        terminal_harness.hash_overrides[
            (terminal_harness.root / l3_audit.PLAN_PATH).resolve()
        ] = "0" * 64
    elif observed_identity == "plan_commit":
        terminal_harness.plan["commit_sha256"] = "0" * 64
    elif observed_identity == "preregistration_file":
        terminal_harness.hash_overrides[
            (terminal_harness.root / l3_audit.PREREGISTRATION_PATH).resolve()
        ] = "0" * 64
    else:
        terminal_harness.preregistration["commit_sha256"] = "0" * 64

    with pytest.raises(l3_audit.GshhgL3HierarchyAuditError):
        l3_audit.authenticate_l3_audit_terminal(terminal_harness.config_path)


@pytest.mark.parametrize("lock_field", FALSE_LOCK_FIELDS)
@pytest.mark.parametrize("terminal_kind", ["success", "failure"])
def test_terminal_rejects_every_recommitted_open_lock(
    terminal_harness: TerminalHarness,
    terminal_kind: str,
    lock_field: str,
) -> None:
    payload = (
        _success_payload(terminal_harness)
        if terminal_kind == "success"
        else _failure_payload(terminal_harness)
    )
    changed = _mutated_terminal(payload, f"locks.{lock_field}", True)
    assert _terminal_commit_is_valid(changed)
    destination = (
        terminal_harness.success_path
        if terminal_kind == "success"
        else terminal_harness.failure_path
    )
    _write_json(destination, changed)

    with pytest.raises(
        l3_audit.GshhgL3HierarchyAuditError,
        match="lock|forbidden access",
    ):
        l3_audit.authenticate_l3_audit_terminal(terminal_harness.config_path)


@pytest.mark.parametrize("access_field", FALSE_ACCESS_FIELDS)
@pytest.mark.parametrize("terminal_kind", ["success", "failure"])
def test_terminal_rejects_every_recommitted_forbidden_access_claim(
    terminal_harness: TerminalHarness,
    terminal_kind: str,
    access_field: str,
) -> None:
    payload = (
        _success_payload(terminal_harness)
        if terminal_kind == "success"
        else _failure_payload(terminal_harness)
    )
    changed = _mutated_terminal(
        payload,
        f"access_contract.{access_field}",
        True,
    )
    assert _terminal_commit_is_valid(changed)
    destination = (
        terminal_harness.success_path
        if terminal_kind == "success"
        else terminal_harness.failure_path
    )
    _write_json(destination, changed)

    with pytest.raises(
        l3_audit.GshhgL3HierarchyAuditError,
        match="lock|forbidden access",
    ):
        l3_audit.authenticate_l3_audit_terminal(terminal_harness.config_path)


@pytest.mark.parametrize(
    ("gate_path", "replacement"),
    [
        ("hierarchy_audit.all_structural_gates_passed", False),
        ("numerical_audit.all_numerical_gates_passed", False),
        ("decision.audit_passed", False),
        ("decision.source_frozen", True),
        ("decision.algorithm_frozen", True),
        ("decision.predictor_build_authorized", True),
        ("decision.next_safe_stage", "predictors_now_authorized"),
    ],
)
def test_success_rejects_recommitted_gate_or_decision_tampering(
    terminal_harness: TerminalHarness,
    gate_path: str,
    replacement: object,
) -> None:
    payload = _success_payload(terminal_harness)
    changed = _mutated_terminal(payload, gate_path, replacement)
    assert _terminal_commit_is_valid(changed)
    _write_json(terminal_harness.success_path, changed)

    with pytest.raises(l3_audit.GshhgL3HierarchyAuditError):
        l3_audit.authenticate_l3_audit_terminal(terminal_harness.config_path)


@pytest.mark.parametrize(
    ("archive_field", "replacement"),
    [
        ("sha256", "0" * 64),
        ("bytes", 123_455),
        ("authorized_member_count", len(l3_audit.AUTHORIZED_MEMBERS) - 1),
        ("member_open_log", list(reversed(l3_audit.AUTHORIZED_MEMBERS))),
        ("unauthorized_member_open_count", 1),
        ("zipfile_testzip_called", True),
    ],
)
def test_success_rejects_recommitted_archive_evidence_tampering(
    terminal_harness: TerminalHarness,
    archive_field: str,
    replacement: object,
) -> None:
    payload = _success_payload(terminal_harness)
    changed = _mutated_terminal(
        payload,
        f"source_archive.{archive_field}",
        replacement,
    )
    assert _terminal_commit_is_valid(changed)
    _write_json(terminal_harness.success_path, changed)

    with pytest.raises(
        l3_audit.GshhgL3HierarchyAuditError,
        match="source-access evidence|archive",
    ):
        l3_audit.authenticate_l3_audit_terminal(terminal_harness.config_path)


@pytest.mark.parametrize(
    ("access_field", "replacement"),
    [
        ("audit_program_network_requests", 1),
        ("gshhg_archive_opened", False),
        (
            "authorized_gshhg_member_count_opened",
            len(l3_audit.AUTHORIZED_MEMBERS) - 1,
        ),
        ("authorized_gshhg_members", list(reversed(l3_audit.AUTHORIZED_MEMBERS))),
        ("unauthorized_gshhg_members_opened", 1),
        ("fixed_target_blind_source_geometry_distances_computed", False),
    ],
)
def test_success_rejects_recommitted_positive_access_evidence_tampering(
    terminal_harness: TerminalHarness,
    access_field: str,
    replacement: object,
) -> None:
    payload = _success_payload(terminal_harness)
    changed = _mutated_terminal(
        payload,
        f"access_contract.{access_field}",
        replacement,
    )
    assert _terminal_commit_is_valid(changed)
    _write_json(terminal_harness.success_path, changed)

    with pytest.raises(l3_audit.GshhgL3HierarchyAuditError, match="access"):
        l3_audit.authenticate_l3_audit_terminal(terminal_harness.config_path)


@pytest.mark.parametrize(
    ("access_field", "replacement"),
    [
        ("network_requests", 1),
        ("gshhg_archive_opened", False),
        ("authorized_l1_l2_l3_members_may_have_been_opened", False),
        ("authorized_member_allowlist", list(reversed(l3_audit.AUTHORIZED_MEMBERS))),
        ("probe_derived", True),
        ("distance_values_computed", True),
    ],
)
def test_phase_one_failure_rejects_recommitted_access_evidence_tampering(
    terminal_harness: TerminalHarness,
    access_field: str,
    replacement: object,
) -> None:
    payload = _failure_payload(terminal_harness)
    changed = _mutated_terminal(
        payload,
        f"access_contract.{access_field}",
        replacement,
    )
    assert _terminal_commit_is_valid(changed)
    _write_json(terminal_harness.failure_path, changed)

    with pytest.raises(l3_audit.GshhgL3HierarchyAuditError, match="access|phase"):
        l3_audit.authenticate_l3_audit_terminal(terminal_harness.config_path)


def test_success_rejects_diagnostic_table_byte_tampering(
    terminal_harness: TerminalHarness,
) -> None:
    _publish_success(terminal_harness)
    terminal_harness.table_path.write_bytes(
        terminal_harness.table_path.read_bytes() + b"\n"
    )

    with pytest.raises(
        l3_audit.GshhgL3HierarchyAuditError,
        match="table bytes changed",
    ):
        l3_audit.authenticate_l3_audit_terminal(terminal_harness.config_path)


@pytest.mark.parametrize(
    ("table_field", "replacement"),
    [
        ("path", "data/interim/not-the-canonical-table.csv"),
        ("bytes", 1),
        ("sha256", "0" * 64),
        ("rows", 999),
        ("semantic_sha256", "0" * 64),
    ],
)
def test_success_rejects_recommitted_diagnostic_table_record_tampering(
    terminal_harness: TerminalHarness,
    table_field: str,
    replacement: object,
) -> None:
    payload = _success_payload(terminal_harness)
    changed = _mutated_terminal(
        payload,
        f"diagnostic_table.{table_field}",
        replacement,
    )
    assert _terminal_commit_is_valid(changed)
    _write_json(terminal_harness.success_path, changed)

    with pytest.raises(l3_audit.GshhgL3HierarchyAuditError, match="table"):
        l3_audit.authenticate_l3_audit_terminal(terminal_harness.config_path)


def test_success_rechecks_diagnostic_table_semantics_after_byte_record_update(
    terminal_harness: TerminalHarness,
) -> None:
    payload = _success_payload(terminal_harness)
    table = pd.read_csv(terminal_harness.table_path)
    table.loc[0, "distance_m"] = float(table.loc[0, "distance_m"]) + 1.0
    changed_bytes = table.to_csv(index=False, lineterminator="\n").encode("utf-8")
    terminal_harness.table_path.write_bytes(changed_bytes)
    changed = copy.deepcopy(payload)
    changed["diagnostic_table"]["bytes"] = len(changed_bytes)
    changed["diagnostic_table"]["sha256"] = hashlib.sha256(changed_bytes).hexdigest()
    _internal_commit(changed)
    assert _terminal_commit_is_valid(changed)
    _write_json(terminal_harness.success_path, changed)

    with pytest.raises(
        l3_audit.GshhgL3HierarchyAuditError,
        match="table semantics",
    ):
        l3_audit.authenticate_l3_audit_terminal(terminal_harness.config_path)


@pytest.mark.parametrize(
    ("runtime_path", "replacement"),
    [
        ("algorithm_version", "different-algorithm"),
        ("python", "0.0.0-tampered"),
        ("base_fingerprint_sha256", "0" * 64),
        ("relative_paths", ["src/not-the-executor.py"]),
        ("packages.pyproj", "tampered-package-version"),
    ],
)
def test_success_rejects_recommitted_code_runtime_tampering(
    terminal_harness: TerminalHarness,
    runtime_path: str,
    replacement: object,
) -> None:
    payload = _success_payload(terminal_harness)
    changed = _mutated_terminal(
        payload,
        f"code_runtime.{runtime_path}",
        replacement,
        rehash_code_runtime=True,
    )
    assert _terminal_commit_is_valid(changed)
    _write_json(terminal_harness.success_path, changed)
    terminal_harness.runtime_mock.reset_mock()

    with pytest.raises(
        l3_audit.GshhgL3HierarchyAuditError,
        match="code-runtime|fingerprint|executor",
    ):
        l3_audit.authenticate_l3_audit_terminal(terminal_harness.config_path)


def test_success_rejects_recommitted_executor_file_hash_tampering(
    terminal_harness: TerminalHarness,
) -> None:
    payload = _success_payload(terminal_harness)
    relative = l3_audit.CODE_PATHS[1]
    changed = copy.deepcopy(payload)
    changed["code_runtime"]["files"][relative] = "0" * 64
    changed["code_runtime"]["sha256"] = l3_audit.canonical_sha256(
        {
            key: value
            for key, value in changed["code_runtime"].items()
            if key != "sha256"
        }
    )
    _internal_commit(changed)
    assert _terminal_commit_is_valid(changed)
    _write_json(terminal_harness.success_path, changed)

    with pytest.raises(
        l3_audit.GshhgL3HierarchyAuditError,
        match="executor files|fingerprint",
    ):
        l3_audit.authenticate_l3_audit_terminal(terminal_harness.config_path)


def test_success_rejects_invalid_inner_code_runtime_hash_even_if_terminal_recommitted(
    terminal_harness: TerminalHarness,
) -> None:
    payload = _success_payload(terminal_harness)
    changed = _mutated_terminal(
        payload,
        "code_runtime.sha256",
        "0" * 64,
    )
    assert _terminal_commit_is_valid(changed)
    _write_json(terminal_harness.success_path, changed)

    with pytest.raises(
        l3_audit.GshhgL3HierarchyAuditError,
        match="code-runtime fingerprint is invalid",
    ):
        l3_audit.authenticate_l3_audit_terminal(terminal_harness.config_path)


@pytest.mark.parametrize(
    ("repository_field", "replacement"),
    [
        ("branch", "not-main"),
        ("origin_main", "2" * 40),
        ("head_equals_origin_main", False),
        ("working_tree_clean_at_preflight_archive_open_and_publish", False),
    ],
)
def test_success_rejects_recommitted_repository_evidence_tampering(
    terminal_harness: TerminalHarness,
    repository_field: str,
    replacement: object,
) -> None:
    payload = _success_payload(terminal_harness)
    changed = _mutated_terminal(
        payload,
        f"repository.{repository_field}",
        replacement,
    )
    assert _terminal_commit_is_valid(changed)
    _write_json(terminal_harness.success_path, changed)

    with pytest.raises(
        l3_audit.GshhgL3HierarchyAuditError,
        match="repository|branch|origin|clean",
    ):
        l3_audit.authenticate_l3_audit_terminal(terminal_harness.config_path)


def test_success_rejects_recommitted_repository_blob_tampering(
    terminal_harness: TerminalHarness,
) -> None:
    payload = _success_payload(terminal_harness)
    relative = l3_audit.CODE_PATHS[1]
    changed = copy.deepcopy(payload)
    changed["repository"]["tracked_blob_sha1"][relative] = "0" * 40
    _internal_commit(changed)
    assert _terminal_commit_is_valid(changed)
    _write_json(terminal_harness.success_path, changed)

    with pytest.raises(
        l3_audit.GshhgL3HierarchyAuditError,
        match="Executor blobs changed",
    ):
        l3_audit.authenticate_l3_audit_terminal(terminal_harness.config_path)


def test_success_rejects_terminal_head_that_is_not_a_current_ancestor(
    terminal_harness: TerminalHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _publish_success(terminal_harness)
    monkeypatch.setattr(
        l3_audit.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="not an ancestor",
        ),
    )

    with pytest.raises(
        l3_audit.GshhgL3HierarchyAuditError,
        match="not an ancestor",
    ):
        l3_audit.authenticate_l3_audit_terminal(terminal_harness.config_path)


def test_terminal_internal_commit_is_still_mandatory(
    terminal_harness: TerminalHarness,
) -> None:
    payload = _success_payload(terminal_harness)
    payload["state"] = "tampered-without-recommit"
    _write_json(terminal_harness.success_path, payload)

    with pytest.raises(
        l3_audit.GshhgL3HierarchyAuditError,
        match="invalid internal commit",
    ):
        l3_audit.authenticate_l3_audit_terminal(terminal_harness.config_path)


def test_synthetic_table_semantic_record_matches_csv_parser_contract(
    terminal_harness: TerminalHarness,
) -> None:
    payload = _success_payload(terminal_harness)
    parsed = pd.read_csv(io.BytesIO(terminal_harness.table_path.read_bytes()))

    assert payload["diagnostic_table"]["semantic_sha256"] == (
        l3_audit.canonical_sha256(
            parsed[
                [
                    "point_id",
                    "point_kind",
                    "distance_m",
                    "l1_l2_only_distance_m",
                ]
            ].to_dict("records")
        )
    )
