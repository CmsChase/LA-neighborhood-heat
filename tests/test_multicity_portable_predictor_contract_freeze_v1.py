from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path

import pytest

import la_heat.multicity.portable_predictor_contract_freeze_v1 as contract_v1
from la_heat.multicity.portable_predictor_contract_freeze_v1 import (
    ABSENT_SOURCE_PATHS,
    ALGORITHM_VERSION,
    CODE_PATHS,
    CONFIG_PATH,
    CONFIG_SHA256,
    EXPECTED_BLOCKERS,
    MODULE_PATH,
    NEXT_SAFE_STAGE,
    OUTPUT_PATH,
    PHOENIX_FILE_SHA256,
    PHOENIX_INTERNAL_COMMIT_SHA256,
    PHOENIX_PUBLICATION_COMMIT,
    PHOENIX_SOURCE_PATH,
    PROVENANCE_PATH,
    SCRIPT_PATH,
    STATE,
    V2_FILE_SHA256,
    V2_INTERNAL_COMMIT_SHA256,
    V2_PUBLICATION_COMMIT,
    V2_TERMINAL_PATH,
    V8_MODULE_PATH,
    V8_SCRIPT_PATH,
    PortablePredictorContractFreezeV1Error,
    _authenticate_terminal_history,
    _build_payload,
    _git_preflight,
    _git_regular_blob,
    _path_never_tracked,
    _read_config,
    _require_terminal_generation_precondition,
    _validate_config,
    _validate_phoenix,
    _validate_v2,
    audit_portable_predictor_contract_freeze_v1,
    expected_plan_authorization_scope,
)
from la_heat.provenance import canonical_sha256, sha256_file

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / CONFIG_PATH
MODULE = ROOT / MODULE_PATH
SCRIPT = ROOT / SCRIPT_PATH
V1_DECISION_PUBLICATION_COMMIT = "47a626f6fc0a6577148cc731bb00d21f5387f20a"
V1_DECISION_PRECONDITION_COMMIT = "35b6015a3a9a410b42752d2e50a7599e18bf2563"


def _historical_json(
    commit: str,
    path: str,
) -> tuple[dict[str, object], bytes]:
    raw, _, _ = _git_regular_blob(ROOT, commit=commit, relative_path=path)
    payload = contract_v1._json_from_bytes(raw, label=f"{commit}:{path}")
    return payload, raw


def _config() -> tuple[dict[str, object], bytes]:
    payload, raw = _read_config(CONFIG)
    _validate_config(payload)
    return payload, raw


def _synthetic_v8_plan(config: dict[str, object]) -> dict[str, object]:
    locks = deepcopy(config["locks"])
    plan: dict[str, object] = {
        "schema_version": 8,
        "algorithm_version": "multicity-planning-readiness-v8",
        "state": "planning_ready",
        "planning_stage": (
            "portable_water_distance_source_and_algorithm_frozen_"
            "predictor_contract_freeze_authorized"
        ),
        "next_safe_stage": (
            "freeze_exact_portable_predictor_source_and_calibration_contract"
        ),
        "experiment_id": "la_to_three_city_zero_shot_v1",
        "config_semantic_sha256": (
            "6a52eb39486dc9e992927e5506b4071aaba6ec31dd910eeb0f9bcc51dea1736c"
        ),
        "authorized_now": {
            "portable_predictor_source_and_calibration_contract_freeze": True,
            "predictor_construction": False,
            "model_fitting": False,
            "external_target_or_qa_value_access": False,
            "one_time_external_evaluation": False,
            "operational_forecast_claim": False,
        },
        "locks": locks,
        "predictor_contract_freeze_authorization_scope": (
            expected_plan_authorization_scope()
        ),
    }
    plan["commit_sha256"] = canonical_sha256(plan)
    return plan


def _synthetic_code_files() -> dict[str, dict[str, object]]:
    return {
        path: {
            "sha256": sha256_file(ROOT / path),
            "bytes": (ROOT / path).stat().st_size,
            "git_blob_oid": "1" * 40,
            "git_mode": "100644",
        }
        for path in CODE_PATHS
    }


def _payload(blockers: tuple[str, ...] = EXPECTED_BLOCKERS) -> dict[str, object]:
    config, config_raw = _config()
    v2, _ = _historical_json(V2_PUBLICATION_COMMIT, V2_TERMINAL_PATH)
    phoenix, _ = _historical_json(
        PHOENIX_PUBLICATION_COMMIT,
        PHOENIX_SOURCE_PATH,
    )
    plan = _synthetic_v8_plan(config)
    plan_raw = contract_v1._expected_json_bytes(plan)
    arguments: dict[str, object] = {
        "project_root": ROOT,
        "config": config,
        "config_bytes": len(config_raw),
        "plan": plan,
        "plan_raw": plan_raw,
        "plan_publication": "a" * 40,
        "v2": v2,
        "phoenix": phoenix,
        "blockers": blockers,
        "precondition_head": "a" * 40,
        "code_files": _synthetic_code_files(),
    }
    return _build_payload(**arguments)  # type: ignore[arg-type]


def test_v1_config_and_scope_freeze_exact_runtime_and_read_set() -> None:
    config, _ = _config()
    scope = expected_plan_authorization_scope()

    assert sha256_file(CONFIG) == CONFIG_SHA256
    assert config["decision"]["algorithm_version"] == ALGORITHM_VERSION
    assert config["decision"]["state"] == STATE
    assert config["defer_rules"]["required_blockers"] == list(EXPECTED_BLOCKERS)
    assert tuple(scope["decision_runtime_paths"]) == CODE_PATHS
    assert len(CODE_PATHS) == len(set(CODE_PATHS))
    assert {
        CONFIG_PATH,
        V8_MODULE_PATH,
        V8_SCRIPT_PATH,
        MODULE_PATH,
        SCRIPT_PATH,
        PROVENANCE_PATH,
    }.issubset(CODE_PATHS)
    assert scope["tracked_read_set"] == {
        "water_distance_v2": {
            "path": V2_TERMINAL_PATH,
            "bytes": 18_541,
            "file_sha256": V2_FILE_SHA256,
            "commit_sha256": V2_INTERNAL_COMMIT_SHA256,
            "publication_git_commit": V2_PUBLICATION_COMMIT,
        },
        "phoenix_source_footprint": {
            "path": PHOENIX_SOURCE_PATH,
            "bytes": 18_861,
            "file_sha256": PHOENIX_FILE_SHA256,
            "commit_sha256": PHOENIX_INTERNAL_COMMIT_SHA256,
            "publication_git_commit": PHOENIX_PUBLICATION_COMMIT,
        },
    }
    assert scope["required_absent_paths"] == list(ABSENT_SOURCE_PATHS)
    assert scope["decision_output_path"] == OUTPUT_PATH
    assert scope["network_or_download_allowed"] is False
    assert scope["source_archive_payload_or_geometry_read_allowed"] is False
    assert scope["eligible_land_or_predictor_value_read_allowed"] is False
    assert scope["predictor_construction_allowed"] is False
    assert scope["model_target_or_result_read_allowed"] is False
    assert scope["protocol_promotion_allowed"] is False
    assert "*" not in str(scope)


def test_v1_authenticates_prerequisites_and_observes_exact_four_blockers() -> None:
    v2, v2_raw = _historical_json(V2_PUBLICATION_COMMIT, V2_TERMINAL_PATH)
    phoenix, phoenix_raw = _historical_json(
        PHOENIX_PUBLICATION_COMMIT,
        PHOENIX_SOURCE_PATH,
    )

    _validate_v2(v2, v2_raw)
    phoenix_blockers = _validate_phoenix(phoenix, phoenix_raw)
    ancestry = contract_v1._run_git(
        ROOT,
        "rev-list",
        "--parents",
        "-n",
        "1",
        V1_DECISION_PUBLICATION_COMMIT,
    )
    assert isinstance(ancestry, str)
    assert ancestry.split() == [
        V1_DECISION_PUBLICATION_COMMIT,
        V1_DECISION_PRECONDITION_COMMIT,
    ]
    assert all(
        _path_never_tracked(
            ROOT,
            path=path,
            head=V1_DECISION_PRECONDITION_COMMIT,
        )
        for path in ABSENT_SOURCE_PATHS
    )
    observed = (
        "houston_source_footprint_manifest_absent",
        "chicago_source_footprint_manifest_absent",
        *phoenix_blockers,
    )
    assert observed == EXPECTED_BLOCKERS


def test_v1_payload_defers_and_keeps_every_downstream_permission_closed() -> None:
    payload = _payload()

    assert payload["state"] == STATE
    assert payload["evidence_gaps"]["observed_blockers"] == list(
        EXPECTED_BLOCKERS
    )
    assert payload["decision"] == {
        "contract_freeze_passed": False,
        "portable_predictor_contract_locked": False,
        "feature_names_frozen": False,
        "predictor_build_authorized": False,
        "protocol_lock_created": False,
        "external_targets_unlocked": False,
        "next_safe_stage": NEXT_SAFE_STAGE,
    }
    assert payload["locks"] == {
        "portable_water_distance_source_locked": True,
        "portable_water_distance_algorithm_locked": True,
        "portable_predictor_source_and_calibration_contract_locked": False,
        "portable_water_distance_feature_names_frozen": False,
        "predictor_build_authorized": False,
        "protocol_lock_created": False,
        "external_targets_unlocked": False,
        "external_target_values_read": False,
        "external_prediction_commit_exists": False,
    }
    access = payload["access_contract"]
    for key, value in access.items():
        if key.endswith(("opened", "computed", "performed")):
            assert value is False, key
    runtime = payload["code_runtime"]
    assert runtime["algorithm_version"] == ALGORITHM_VERSION
    assert runtime["relative_paths"] == list(CODE_PATHS)
    assert set(runtime["files"]) == set(CODE_PATHS)
    assert runtime["git_files"] == _synthetic_code_files()


def test_v1_rejects_any_blocker_set_other_than_the_preregistered_four() -> None:
    with pytest.raises(
        PortablePredictorContractFreezeV1Error,
        match="observed blockers differ",
    ):
        _payload(EXPECTED_BLOCKERS[:-1])


def test_v1_rejects_config_byte_or_semantic_tamper(tmp_path: Path) -> None:
    changed_path = tmp_path / "portable_predictor_contract_freeze_v1.toml"
    changed_path.write_bytes(
        CONFIG.read_bytes().replace(
            b"predictor_build_authorized = false",
            b"predictor_build_authorized = true ",
        )
    )
    with pytest.raises(
        PortablePredictorContractFreezeV1Error,
        match="config changed",
    ):
        _read_config(changed_path)

    config, _ = _config()
    changed = deepcopy(config)
    changed["locks"]["predictor_build_authorized"] = True
    with pytest.raises(
        PortablePredictorContractFreezeV1Error,
        match="lock|contract",
    ):
        _validate_config(changed)


def test_v1_rejects_noncanonical_config_and_output_paths(tmp_path: Path) -> None:
    with pytest.raises(
        PortablePredictorContractFreezeV1Error,
        match="canonical config path",
    ):
        audit_portable_predictor_contract_freeze_v1(
            project_root=ROOT,
            config_path=tmp_path / "config.toml",
        )
    with pytest.raises(
        PortablePredictorContractFreezeV1Error,
        match="canonical terminal",
    ):
        audit_portable_predictor_contract_freeze_v1(
            project_root=ROOT,
            output_path=tmp_path / "terminal.json",
        )


def test_v1_preflight_rejects_hidden_worktree_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head = "a" * 40

    def fake_git(_root: Path, *args: str, **_kwargs: object) -> str:
        if args == ("branch", "--show-current"):
            return "main\n"
        if args == ("rev-parse", "HEAD") or args == (
            "rev-parse",
            "origin/main",
        ):
            return f"{head}\n"
        if args[0] == "status":
            return ""
        if args[0] == "hash-object":
            return f"{'2' * 40}\n"
        raise AssertionError(args)

    monkeypatch.setattr(contract_v1, "_run_git", fake_git)
    monkeypatch.setattr(
        contract_v1,
        "_git_regular_blob",
        lambda *args, **kwargs: (b"tracked", "1" * 40, "100644"),
    )

    with pytest.raises(
        PortablePredictorContractFreezeV1Error,
        match="differs from HEAD|visibility",
    ):
        _git_preflight(ROOT, required_paths=("runtime.py",))


def test_v1_expected_absent_path_is_not_accepted_after_git_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_git(_root: Path, *args: str, **_kwargs: object) -> str:
        if args[0] == "log":
            return f"{'a' * 40}\n"
        if args[0] == "ls-tree":
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(contract_v1, "_run_git", fake_git)
    assert not _path_never_tracked(
        ROOT,
        path=ABSENT_SOURCE_PATHS[0],
        head="b" * 40,
    )


def _terminal_history_git(
    *,
    publication: str,
    parent: str,
    later: str = "",
):
    def fake_git(_root: Path, *args: str, **_kwargs: object) -> str | bytes:
        if args[0] == "log" and "--diff-filter=A" in args:
            return f"{publication}\n"
        if args[0] == "rev-list":
            return f"{publication} {parent}\n"
        if args[0] == "diff-tree":
            return f"A\0{OUTPUT_PATH}\0".encode()
        if args[0] == "log":
            return later
        raise AssertionError(args)

    return fake_git


def test_v1_terminal_history_accepts_one_direct_child_append_only_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = "b" * 40
    parent = "a" * 40
    raw = b"terminal"
    monkeypatch.setattr(
        contract_v1,
        "_run_git",
        _terminal_history_git(publication=publication, parent=parent),
    )
    monkeypatch.setattr(
        contract_v1,
        "_git_regular_blob",
        lambda *args, **kwargs: (raw, "1" * 40, "100644"),
    )
    if hasattr(contract_v1, "_is_ancestor"):
        monkeypatch.setattr(contract_v1, "_is_ancestor", lambda *args: True)

    _authenticate_terminal_history(
        ROOT,
        payload_raw=raw,
        plan_publication=parent,
        current_head=publication,
    )


def test_v1_terminal_history_rejects_non_direct_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = "b" * 40
    monkeypatch.setattr(
        contract_v1,
        "_run_git",
        _terminal_history_git(publication=publication, parent="c" * 40),
    )
    if hasattr(contract_v1, "_is_ancestor"):
        monkeypatch.setattr(contract_v1, "_is_ancestor", lambda *args: True)

    with pytest.raises(
        PortablePredictorContractFreezeV1Error,
        match="direct child",
    ):
        _authenticate_terminal_history(
            ROOT,
            payload_raw=b"terminal",
            plan_publication="a" * 40,
            current_head=publication,
        )


def test_v1_terminal_history_rejects_same_commit_source_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = "b" * 40
    parent = "a" * 40

    def fake_git(_root: Path, *args: str, **_kwargs: object) -> str | bytes:
        if args[0] == "log" and "--diff-filter=A" in args:
            return f"{publication}\n"
        if args[0] == "rev-list":
            return f"{publication} {parent}\n"
        if args[0] == "diff-tree":
            return (
                f"A\0{OUTPUT_PATH}\0A\0{ABSENT_SOURCE_PATHS[0]}\0".encode()
            )
        raise AssertionError(args)

    monkeypatch.setattr(contract_v1, "_run_git", fake_git)

    with pytest.raises(
        PortablePredictorContractFreezeV1Error,
        match="add only its canonical terminal",
    ):
        _authenticate_terminal_history(
            ROOT,
            payload_raw=b"terminal",
            plan_publication=parent,
            current_head=publication,
        )


def test_v1_generation_requires_head_to_equal_v8_publication() -> None:
    with pytest.raises(
        PortablePredictorContractFreezeV1Error,
        match="exact planning-v8 publication",
    ):
        _require_terminal_generation_precondition(
            head="b" * 40,
            plan_publication="a" * 40,
            output_exists=False,
            write=True,
        )

    _require_terminal_generation_precondition(
        head="b" * 40,
        plan_publication="a" * 40,
        output_exists=True,
        write=False,
    )


def test_v1_terminal_history_rejects_multiple_publications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = "b" * 40

    def fake_git(_root: Path, *args: str, **_kwargs: object) -> str:
        assert args[0] == "log" and "--diff-filter=A" in args
        return f"{publication}\n{'c' * 40}\n"

    monkeypatch.setattr(contract_v1, "_run_git", fake_git)
    with pytest.raises(
        PortablePredictorContractFreezeV1Error,
        match="one unique publication",
    ):
        _authenticate_terminal_history(
            ROOT,
            payload_raw=b"terminal",
            plan_publication="a" * 40,
            current_head=publication,
        )


def test_v1_terminal_history_rejects_publication_outside_current_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = "b" * 40
    parent = "a" * 40
    monkeypatch.setattr(
        contract_v1,
        "_run_git",
        _terminal_history_git(publication=publication, parent=parent),
    )
    monkeypatch.setattr(contract_v1, "_is_ancestor", lambda *args: False)

    with pytest.raises(
        PortablePredictorContractFreezeV1Error,
        match="not an ancestor",
    ):
        _authenticate_terminal_history(
            ROOT,
            payload_raw=b"terminal",
            plan_publication=parent,
            current_head="d" * 40,
        )


def test_v1_terminal_history_rejects_tamper_then_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = "b" * 40
    parent = "a" * 40
    raw = b"terminal"
    monkeypatch.setattr(
        contract_v1,
        "_run_git",
        _terminal_history_git(
            publication=publication,
            parent=parent,
            later=f"{'c' * 40}\n",
        ),
    )
    monkeypatch.setattr(
        contract_v1,
        "_git_regular_blob",
        lambda *args, **kwargs: (raw, "1" * 40, "100644"),
    )
    if hasattr(contract_v1, "_is_ancestor"):
        monkeypatch.setattr(contract_v1, "_is_ancestor", lambda *args: True)

    with pytest.raises(
        PortablePredictorContractFreezeV1Error,
        match="changed after publication",
    ):
        _authenticate_terminal_history(
            ROOT,
            payload_raw=raw,
            plan_publication=parent,
            current_head="d" * 40,
        )


@pytest.mark.parametrize("path", [MODULE, SCRIPT])
def test_v1_program_has_no_data_network_geometry_or_model_reader_imports(
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
