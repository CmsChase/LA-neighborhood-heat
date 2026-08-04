from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from la_heat.multicity import missing_support_calibration_evidence_v1 as evidence
from la_heat.multicity import (
    plan_sentinel_source_footprint_verifier_hotfix_transition_v18 as transition,
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
    predecessor, _ = transition._historical_v17(ROOT)
    code_files = {path: _record("a") for path in evidence.CODE_PATHS}
    transition_files = {
        path: _record("b") for path in transition.transition_code_paths(evidence.CODE_PATHS)
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


def test_v18_routes_each_frozen_source_footprint_to_its_published_verifier() -> None:
    predecessor, payload = _payload()

    assert payload["schema_version"] == 18
    assert payload["algorithm_version"] == transition.ALGORITHM_VERSION
    assert payload["planning_stage"] == transition.PLANNING_STAGE
    assert payload["next_safe_stage"] == transition.NEXT_SAFE_STAGE
    assert payload["authorized_now"] == predecessor["authorized_now"]
    assert payload["locks"] == predecessor["locks"]
    fix = payload["transition"]["authorized_fix"]
    assert fix == transition.AUTHORIZED_FIX
    assert fix["existing_source_footprint_manifests_only"] is True
    assert fix["source_footprint_rediscovery_performed"] is False
    assert fix["source_metadata_table_or_values_changed"] is False
    assert fix["network_requests_added"] is False
    assert fix["sentinel_probe_selection_changed"] is False
    assert fix["permissions_changed"] is False
    assert fix["locks_changed"] is False
    assert fix["successful_evidence_next_plan_version"] == "v19"
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    assert payload["commit_sha256"] == canonical_sha256(body)


def test_v18_binds_phoenix_sentinel_as_the_eleventh_checkpoint() -> None:
    _, payload = _payload()

    resume = payload["transition"]["resume_checkpoints"]
    assert tuple(record["path"] for record in resume) == (transition.RESUME_CHECKPOINT_PATHS)
    assert transition.RESUME_CHECKPOINT_PATHS == evidence.TRACKED_OUTPUT_PATHS[:11]
    assert len(resume) == 11
    assert resume[-1] == transition.PHOENIX_SENTINEL_RESUME_CHECKPOINT_V18


def test_v18_binds_source_evidence_config_and_moves_decision_to_v19() -> None:
    scope = evidence.expected_plan_authorization_scope()
    assert "configs/multicity/portable_predictor_source_evidence_v1.toml" in scope["code_paths"]
    assert scope["next_gate"] == (
        "publish_tracked_only_plan_v19_for_portable_predictor_contract_v3_decision"
    )


def test_v18_status_parser_rejects_noncheckpoint_paths() -> None:
    exact = b"".join(
        b"?? " + path.encode("utf-8") + b"\0" for path in transition.RESUME_CHECKPOINT_PATHS
    )
    assert transition._parse_status(exact) == frozenset(transition.RESUME_CHECKPOINT_PATHS)
    assert transition._parse_status(b"") == frozenset()
    with pytest.raises(
        transition.MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error,
        match="Unexpected dirty path",
    ):
        transition._parse_status(exact + b"?? unexpected.txt\0")


def test_v18_implementation_delta_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    assert len(transition.EXPECTED_IMPLEMENTATION_DELTA) == 8
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

    monkeypatch.setattr(transition.v17.v16.v15.v14, "_run_git", fake_git)
    transition._implementation_delta(ROOT, implementation)
    mode["extra"] = True
    with pytest.raises(
        transition.MulticityPlanSentinelSourceFootprintVerifierHotfixV18Error,
        match="outside its exact allowlist",
    ):
        transition._implementation_delta(ROOT, implementation)


@pytest.mark.parametrize("path", [MODULE, SCRIPT])
def test_v18_transition_has_no_network_raster_or_model_reader_imports(
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
