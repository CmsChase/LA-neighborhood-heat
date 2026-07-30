from __future__ import annotations

import ast
import importlib.util
import json
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any
from unittest.mock import Mock

import pytest

import la_heat.multicity.gshhg_l3_hierarchy_audit as l3_audit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "src/la_heat/multicity/gshhg_l3_hierarchy_audit.py"
CLI_PATH = PROJECT_ROOT / "scripts/audit_multicity_gshhg_l3_hierarchy.py"

FORBIDDEN_IMPORT_PARTS = {
    "aiohttp",
    "boto3",
    "census",
    "ftplib",
    "httpx",
    "landsat",
    "model",
    "models",
    "network",
    "planetary_computer",
    "pystac_client",
    "requests",
    "socket",
    "target",
    "targets",
    "urllib",
    "urllib3",
}
FORBIDDEN_CALL_LEAVES = {
    "_audit_archive",
    "_build_pilot_payload",
    "audit_gshhg_geometry_pilot",
    "download",
    "get_model",
    "open_target",
    "request",
    "testzip",
    "urlopen",
    "validate_pinned_zip",
}
FORBIDDEN_CALL_FRAGMENTS = {
    "census",
    "download",
    "http",
    "landsat",
    "model",
    "network",
    "predict",
    "request",
    "target",
}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _dotted_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _import_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names.update(
                f"{module}.{alias.name}" if module else alias.name
                for alias in node.names
            )
    return names


def _call_names(tree: ast.AST) -> set[str]:
    return {
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and (name := _dotted_name(node.func))
    }


def _valid_plan() -> dict[str, Any]:
    return {
        "schema_version": 6,
        "commit_sha256": l3_audit.EXPECTED_PLAN_COMMIT_SHA256,
        "next_safe_stage": "target_blind_gshhg_l3_hierarchy_geometry_audit",
        "authorized_now": {
            "target_blind_gshhg_l3_hierarchy_geometry_read": True,
        },
    }


def _install_tracked_input_stubs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gshhg_l3.toml"
    plan_path = tmp_path / l3_audit.PLAN_PATH
    config: dict[str, Any] = {
        "source": {
            "archive_path": (
                "data/raw/multicity/water_distance/gshhg-shp-2.3.7.zip"
            ),
        },
        "unchanged_v2_contract": {
            "amendment_config_path": "configs/multicity/gshhg_geometry_pilot_v2.toml",
        },
    }
    monkeypatch.setattr(
        l3_audit,
        "_read_config",
        lambda _path: (tmp_path, config_path, config),
    )

    def fake_sha256(path: str | Path) -> str:
        resolved = Path(path)
        if resolved == config_path:
            return l3_audit.EXPECTED_CONFIG_SHA256
        if resolved == plan_path:
            return l3_audit.EXPECTED_PLAN_FILE_SHA256
        raise AssertionError(f"Unexpected file hash before the archive gate: {resolved}")

    monkeypatch.setattr(l3_audit, "sha256_file", fake_sha256)

    def fake_json(
        path: Path,
        *,
        label: str,
    ) -> tuple[dict[str, Any], str]:
        if path == tmp_path / l3_audit.PREREGISTRATION_PATH:
            return (
                {
                    "commit_sha256": (
                        l3_audit.EXPECTED_PREREGISTRATION_COMMIT_SHA256
                    ),
                },
                l3_audit.EXPECTED_PREREGISTRATION_FILE_SHA256,
            )
        if path == tmp_path / l3_audit.PILOT_PATH:
            return {"source_archive": {"required_member_sha256": {}}}, "pilot"
        raise AssertionError(f"Unexpected JSON read before the archive gate: {label}")

    monkeypatch.setattr(l3_audit, "_read_json_object", fake_json)
    monkeypatch.setattr(
        l3_audit,
        "_read_exact_configs",
        lambda _path: (
            tmp_path / "amendment.toml",
            {},
            tmp_path / "base.toml",
            {},
        ),
    )
    monkeypatch.setattr(
        l3_audit,
        "_required_git_paths",
        lambda _root: l3_audit.CODE_PATHS,
    )


def _install_forbidden_archive_spies(
    monkeypatch: pytest.MonkeyPatch,
) -> list[Mock]:
    spies: list[Mock] = []
    for name in ("run_structural_phase", "_read_isolated_layers", "_hash_archive"):
        spy = Mock(
            side_effect=AssertionError(
                f"{name} must not run before every plan and Git gate passes."
            )
        )
        monkeypatch.setattr(l3_audit, name, spy)
        spies.append(spy)
    zip_spy = Mock(
        side_effect=AssertionError(
            "ZipFile must not be constructed before every plan and Git gate passes."
        )
    )
    monkeypatch.setattr(l3_audit.zipfile, "ZipFile", zip_spy)
    spies.append(zip_spy)
    return spies


def _raise(message: str) -> Callable[..., Any]:
    def fail(*_args: object, **_kwargs: object) -> Any:
        raise RuntimeError(message)

    return fail


def _load_cli_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "test_gshhg_l3_audit_cli",
        CLI_PATH,
    )
    if spec is None or spec.loader is None:
        raise AssertionError("Could not load the L3 audit CLI for its isolated test.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _decode_json_stream(raw: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    position = 0
    records: list[dict[str, Any]] = []
    while position < len(raw):
        while position < len(raw) and raw[position].isspace():
            position += 1
        if position == len(raw):
            break
        record, position = decoder.raw_decode(raw, position)
        assert isinstance(record, dict)
        records.append(record)
    return records


def test_executor_static_surface_excludes_forbidden_readers_and_orchestrators() -> None:
    trees = [_parse(MODULE_PATH), _parse(CLI_PATH)]
    imported = set().union(*(_import_names(tree) for tree in trees))
    calls = set().union(*(_call_names(tree) for tree in trees))

    for imported_name in imported:
        normalized = imported_name.casefold()
        parts = set(normalized.split("."))
        assert parts.isdisjoint(FORBIDDEN_IMPORT_PARTS), imported_name
        assert not any(
            fragment in normalized
            for fragment in {
                "census",
                "landsat",
                ".model",
                ".network",
                ".target",
            }
        ), imported_name
    assert not {
        "la_heat.multicity.gshhg_geometry_pilot.audit_gshhg_geometry_pilot",
        "la_heat.multicity.gshhg_geometry_pilot._build_pilot_payload",
        "la_heat.multicity.gshhg_geometry_pilot._audit_archive",
        "la_heat.multicity.gshhg_geometry_pilot.validate_pinned_zip",
    }.intersection(imported)

    for call in calls:
        leaf = call.rsplit(".", maxsplit=1)[-1].casefold()
        assert leaf not in FORBIDDEN_CALL_LEAVES, call
        assert not any(fragment in leaf for fragment in FORBIDDEN_CALL_FRAGMENTS), call

    string_literals = {
        node.value
        for tree in trees
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert not any("zip://" in value.casefold() for value in string_literals)


def test_code_paths_bind_executor_and_cli_without_data_or_result_artifacts() -> None:
    paths = tuple(PurePosixPath(path) for path in l3_audit.CODE_PATHS)

    assert PurePosixPath(
        "src/la_heat/multicity/gshhg_l3_hierarchy_audit.py"
    ) in paths
    assert PurePosixPath("scripts/audit_multicity_gshhg_l3_hierarchy.py") in paths
    assert all(
        path.parts[0].casefold()
        not in {"data", "exports", "manifests", "reports", "results"}
        for path in paths
    )


@pytest.mark.parametrize(
    ("failed_gate", "expected_message"),
    [
        ("plan", "Planning v6"),
        ("git", "tracked Git dependency"),
        ("dirty", "clean working tree"),
        ("head", "HEAD to equal local origin/main"),
    ],
)
def test_top_level_never_reaches_archive_when_a_pre_archive_gate_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_gate: str,
    expected_message: str,
) -> None:
    _install_tracked_input_stubs(monkeypatch, tmp_path)
    archive_spies = _install_forbidden_archive_spies(monkeypatch)
    monkeypatch.setattr(l3_audit, "audit_multicity_plan", lambda *_args, **_kwargs: _valid_plan())

    if failed_gate == "plan":
        monkeypatch.setattr(
            l3_audit,
            "audit_multicity_plan",
            lambda *_args, **_kwargs: {},
        )
    elif failed_gate == "git":
        monkeypatch.setattr(
            l3_audit,
            "_git_blob_records",
            _raise("tracked Git dependency failed authentication"),
        )
    elif failed_gate == "dirty":
        monkeypatch.setattr(
            l3_audit,
            "_git_preflight",
            _raise("Planning transition requires a completely clean working tree."),
        )
    else:
        monkeypatch.setattr(
            l3_audit,
            "_git_preflight",
            _raise("Planning transition requires HEAD to equal local origin/main."),
        )

    with pytest.raises(
        (RuntimeError, l3_audit.GshhgL3HierarchyAuditError),
        match=expected_message,
    ):
        l3_audit.audit_gshhg_l3_hierarchy(tmp_path / "gshhg_l3.toml")

    assert all(not spy.called for spy in archive_spies)


def test_cli_emits_jsonl_progress_and_terminal_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli = _load_cli_module()
    config_path = tmp_path / "synthetic-only.toml"

    def fake_audit(
        config: Path,
        *,
        write: bool,
        progress: Callable[[dict[str, Any]], None] | None,
    ) -> dict[str, Any]:
        assert config == config_path
        assert write is True
        assert progress is not None
        progress({"event": "preflight.plan_v6.start", "synthetic_test": True})
        progress({"event": "preflight.git_gate.complete", "synthetic_test": True})
        return {
            "state": cli.COMPLETE_STATE,
            "commit_sha256": "a" * 64,
            "decision": {
                "source_frozen": False,
                "predictor_build_authorized": False,
                "next_safe_stage": "separate_freeze_decision",
            },
        }

    monkeypatch.setattr(cli, "audit_gshhg_l3_hierarchy", fake_audit)

    assert cli.main(["--config", str(config_path)]) == 0
    records = _decode_json_stream(capsys.readouterr().out)

    assert [record.get("event") for record in records[:-1]] == [
        "preflight.plan_v6.start",
        "preflight.git_gate.complete",
    ]
    assert records[-1] == {
        "state": cli.COMPLETE_STATE,
        "phase": None,
        "gate": None,
        "source_frozen": False,
        "predictor_build_authorized": False,
        "next_safe_stage": "separate_freeze_decision",
        "commit_sha256": "a" * 64,
    }
