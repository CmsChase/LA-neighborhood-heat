from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from la_heat.multicity import (
    missing_support_calibration_evidence_v1 as evidence,
)
from la_heat.multicity import (
    plan_worldcover_bbox_query_hotfix_transition_v14 as transition,
)
from la_heat.provenance import canonical_sha256

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / transition.TRANSITION_MODULE_PATH
SCRIPT = ROOT / transition.AUTHORIZATION_SCRIPT_PATH


def _record(seed: str) -> dict[str, Any]:
    return {
        "sha256": seed * 64,
        "bytes": 1,
        "git_blob_oid": seed * 40,
        "git_mode": "100644",
    }


def _payload() -> tuple[dict[str, Any], dict[str, Any]]:
    predecessor, _ = transition._historical_v13(ROOT)
    code_files = {
        path: _record("a") for path in evidence.CODE_PATHS
    }
    transition_files = {
        path: _record("b")
        for path in transition.transition_code_paths(evidence.CODE_PATHS)
    }
    payload = transition._build_payload(
        predecessor,
        implementation="c" * 40,
        code_files=code_files,
        transition_code_files=transition_files,
        authorization_scope=evidence.expected_plan_authorization_scope(),
        authorized_now=evidence.expected_authorized_now(),
    )
    return predecessor, payload


def test_v14_changes_only_the_preregistered_worldcover_candidate_query() -> None:
    predecessor, payload = _payload()

    assert payload["schema_version"] == 14
    assert payload["algorithm_version"] == transition.ALGORITHM_VERSION
    assert payload["planning_stage"] == transition.PLANNING_STAGE
    assert payload["next_safe_stage"] == transition.NEXT_SAFE_STAGE
    assert payload["authorized_now"] == predecessor["authorized_now"]
    assert payload["locks"] == predecessor["locks"]
    assert payload["transition"]["authorized_fix"] == transition.AUTHORIZED_FIX
    assert transition.AUTHORIZED_FIX["worldcover_stac_candidate_query_after"] == (
        "bbox_only"
    )
    assert transition.AUTHORIZED_FIX[
        "exact_positive_polygon_item_selection_after_query"
    ] is True
    assert transition.AUTHORIZED_FIX["selected_item_rule_changed"] is False
    assert transition.AUTHORIZED_FIX["permissions_changed"] is False
    assert transition.AUTHORIZED_FIX["locks_changed"] is False
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    assert payload["commit_sha256"] == canonical_sha256(body)


def test_v14_binds_exactly_seven_resume_checkpoints() -> None:
    _, payload = _payload()

    resume = payload["transition"]["resume_checkpoints"]
    assert tuple(record["path"] for record in resume) == (
        transition.RESUME_CHECKPOINT_PATHS
    )
    assert transition.RESUME_CHECKPOINT_PATHS == evidence.TRACKED_OUTPUT_PATHS[:7]
    assert len(resume) == 7
    assert [record["city_id"] for record in resume[5:]] == [
        "los_angeles_ca",
        "phoenix_az",
    ]
    assert all(int(record["bytes"]) > 0 for record in resume)


def test_v14_status_parser_rejects_every_noncheckpoint_path() -> None:
    exact = b"".join(
        b"?? " + path.encode("utf-8") + b"\0"
        for path in transition.RESUME_CHECKPOINT_PATHS
    )
    assert transition._parse_status(exact) == frozenset(
        transition.RESUME_CHECKPOINT_PATHS
    )
    assert transition._parse_status(b"") == frozenset()
    with pytest.raises(
        transition.MulticityPlanWorldCoverBboxQueryHotfixV14Error,
        match="Unexpected dirty path",
    ):
        transition._parse_status(exact + b"?? unexpected.txt\0")
    with pytest.raises(
        transition.MulticityPlanWorldCoverBboxQueryHotfixV14Error,
        match="only append-only untracked",
    ):
        transition._parse_status(b" M tracked.py\0")


def test_v14_implementation_delta_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
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
        transition.MulticityPlanWorldCoverBboxQueryHotfixV14Error,
        match="outside its exact allowlist",
    ):
        transition._implementation_delta(ROOT, implementation)


def test_v14_transition_code_paths_freeze_predecessor_and_current_transition() -> None:
    paths = transition.transition_code_paths(evidence.CODE_PATHS)

    assert paths[: len(evidence.CODE_PATHS)] == evidence.CODE_PATHS
    assert transition.v13.TRANSITION_MODULE_PATH in paths
    assert transition.TRANSITION_MODULE_PATH in paths
    assert transition.AUTHORIZATION_SCRIPT_PATH in paths
    assert len(paths) == len(set(paths))


@pytest.mark.parametrize("path", [MODULE, SCRIPT])
def test_v14_transition_has_no_data_network_geometry_or_model_reader_imports(
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
