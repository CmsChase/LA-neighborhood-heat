from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from la_heat.multicity import (
    missing_support_calibration_evidence_v1 as evidence,
)
from la_heat.multicity import (
    plan_missing_support_calibration_hotfix_transition_v13 as transition,
)
from la_heat.provenance import canonical_sha256

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / transition.TRANSITION_MODULE_PATH
SCRIPT = ROOT / transition.AUTHORIZATION_SCRIPT_PATH


def _payload() -> tuple[dict[str, Any], dict[str, Any]]:
    predecessor, _ = transition._historical_v12(ROOT)
    code_files = {
        path: {
            "sha256": "a" * 64,
            "bytes": index + 1,
            "git_blob_oid": "b" * 40,
            "git_mode": "100644",
        }
        for index, path in enumerate(evidence.CODE_PATHS)
    }
    payload = transition._build_payload(
        predecessor,
        implementation="c" * 40,
        code_files=code_files,
        transition_code_files={
            **code_files,
            transition.TRANSITION_MODULE_PATH: {
                "sha256": "d" * 64,
                "bytes": 1,
                "git_blob_oid": "e" * 40,
                "git_mode": "100644",
            },
            transition.AUTHORIZATION_SCRIPT_PATH: {
                "sha256": "f" * 64,
                "bytes": 1,
                "git_blob_oid": "1" * 40,
                "git_mode": "100644",
            },
        },
        authorization_scope=evidence.expected_plan_authorization_scope(),
        authorized_now=evidence.expected_authorized_now(),
    )
    return predecessor, payload


def test_v13_changes_only_the_exact_worldcover_path_authorization() -> None:
    predecessor, payload = _payload()

    assert payload["schema_version"] == 13
    assert payload["algorithm_version"] == transition.ALGORITHM_VERSION
    assert payload["planning_stage"] == transition.PLANNING_STAGE
    assert payload["next_safe_stage"] == transition.NEXT_SAFE_STAGE
    assert payload["authorized_now"] == predecessor["authorized_now"]
    assert payload["locks"] == predecessor["locks"]
    assert payload["transition"]["authorized_fix"] == transition.AUTHORIZED_FIX
    assert payload["transition"]["authorized_fix"] == {
        "worldcover_provider_host": "ai4edataeuwest.blob.core.windows.net",
        "rejected_path_prefix": "/esa-worldcover/",
        "authorized_exact_path_prefix": "/esa-worldcover/v100/2020/map/",
        "asset_path_prefix_by_host": {
            "esa-worldcover.s3.eu-central-1.amazonaws.com": "/v100/2020/map/",
            "ai4edataeuwest.blob.core.windows.net": (
                "/esa-worldcover/v100/2020/map/"
            ),
        },
        "cross_host_prefix_reuse_allowed": False,
        "conflicting_next_plan_version_replaced": "v13",
        "successful_evidence_next_plan_version": "v14",
        "collection_year_version_or_asset_changed": False,
        "tracked_output_paths_changed": False,
        "permissions_changed": False,
        "locks_changed": False,
    }
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    assert payload["commit_sha256"] == canonical_sha256(body)
    assert set(payload["transition"]["transition_code_files"]) == set(
        transition.transition_code_paths(evidence.CODE_PATHS)
    )


def test_v13_binds_the_exact_five_resume_checkpoints() -> None:
    _, payload = _payload()

    assert tuple(
        record["path"] for record in payload["transition"]["resume_checkpoints"]
    ) == transition.RESUME_CHECKPOINT_PATHS
    assert transition.RESUME_CHECKPOINT_PATHS == evidence.TRACKED_OUTPUT_PATHS[:5]
    assert len(transition.RESUME_CHECKPOINTS) == 5
    assert all(record["bytes"] > 0 for record in transition.RESUME_CHECKPOINTS)
    assert all(
        len(str(record["file_sha256"])) == 64
        and len(str(record["commit_sha256"])) == 64
        for record in transition.RESUME_CHECKPOINTS
    )


def test_v13_resume_checkpoint_verifier_is_byte_and_commit_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    relative = "resume.json"
    body = {"schema_version": 1, "state": "complete"}
    payload = {**body, "commit_sha256": canonical_sha256(body)}
    raw = json.dumps(payload, indent=2).encode("utf-8")
    expected = {
        "path": relative,
        "bytes": len(raw),
        "file_sha256": transition._sha(raw),
        "commit_sha256": payload["commit_sha256"],
        "state": "complete",
    }
    (tmp_path / relative).write_bytes(raw)
    monkeypatch.setattr(transition, "RESUME_CHECKPOINTS", (expected,))

    assert transition._verify_resume_checkpoints(tmp_path) == [expected]
    (tmp_path / relative).write_bytes(raw + b"\n")
    with pytest.raises(
        transition.MulticityPlanMissingSupportCalibrationHotfixV13Error,
        match="changed",
    ):
        transition._verify_resume_checkpoints(tmp_path)


def test_v13_status_parser_allows_only_exact_untracked_checkpoints() -> None:
    exact = b"".join(
        b"?? " + path.encode("utf-8") + b"\0"
        for path in transition.RESUME_CHECKPOINT_PATHS
    )
    assert transition._parse_status(exact) == frozenset(
        transition.RESUME_CHECKPOINT_PATHS
    )
    assert transition._parse_status(b"") == frozenset()
    with pytest.raises(
        transition.MulticityPlanMissingSupportCalibrationHotfixV13Error,
        match="Unexpected dirty path",
    ):
        transition._parse_status(exact + b"?? unexpected.txt\0")
    with pytest.raises(
        transition.MulticityPlanMissingSupportCalibrationHotfixV13Error,
        match="only append-only untracked",
    ):
        transition._parse_status(b" M tracked.py\0")


def test_v13_transition_code_paths_freeze_module_and_script() -> None:
    paths = transition.transition_code_paths(evidence.CODE_PATHS)

    assert paths[: len(evidence.CODE_PATHS)] == evidence.CODE_PATHS
    assert transition.TRANSITION_MODULE_PATH in paths
    assert transition.AUTHORIZATION_SCRIPT_PATH in paths
    assert len(paths) == len(set(paths))


@pytest.mark.parametrize(
    ("status", "allow_clean", "accepted"),
    [
        (b"", True, True),
        (b"", False, False),
        (
            b"".join(
                b"?? " + path.encode("utf-8") + b"\0"
                for path in transition.RESUME_CHECKPOINT_PATHS
            ),
            False,
            True,
        ),
    ],
)
def test_v13_preflight_accepts_clean_only_after_plan_publication(
    status: bytes,
    allow_clean: bool,
    accepted: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head = "a" * 40

    def fake_git(_root: Path, *args: str, **_kwargs: Any) -> str | bytes:
        if args == ("branch", "--show-current"):
            return "main\n"
        if args in {("rev-parse", "HEAD"), ("rev-parse", "origin/main")}:
            return f"{head}\n"
        if args and args[0] == "status":
            return status
        raise AssertionError(args)

    monkeypatch.setattr(transition, "_run_git", fake_git)
    monkeypatch.setattr(
        transition, "_verify_resume_checkpoints", lambda _root: []
    )
    if accepted:
        assert transition._preflight(ROOT, allow_clean=allow_clean) == head
    else:
        with pytest.raises(
            transition.MulticityPlanMissingSupportCalibrationHotfixV13Error,
            match="exact five untracked",
        ):
            transition._preflight(ROOT, allow_clean=allow_clean)


def test_v13_implementation_delta_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
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
        transition.MulticityPlanMissingSupportCalibrationHotfixV13Error,
        match="outside its exact allowlist",
    ):
        transition._implementation_delta(ROOT, implementation)


@pytest.mark.parametrize("path", [MODULE, SCRIPT])
def test_v13_transition_has_no_data_network_geometry_or_model_reader_imports(
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
