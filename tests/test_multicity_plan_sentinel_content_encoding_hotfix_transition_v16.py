from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from la_heat.multicity import (
    missing_support_calibration_evidence_v1 as evidence,
)
from la_heat.multicity import (
    plan_sentinel_content_encoding_hotfix_transition_v16 as transition,
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
    predecessor, _ = transition._historical_v15(ROOT)
    code_files = {path: _record("a") for path in evidence.CODE_PATHS}
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


def test_v16_bounds_encoded_and_decoded_sentinel_bodies() -> None:
    predecessor, payload = _payload()

    assert payload["schema_version"] == 16
    assert payload["algorithm_version"] == transition.ALGORITHM_VERSION
    assert payload["planning_stage"] == transition.PLANNING_STAGE
    assert payload["next_safe_stage"] == transition.NEXT_SAFE_STAGE
    assert payload["authorized_now"] == predecessor["authorized_now"]
    assert payload["locks"] == predecessor["locks"]
    assert payload["transition"]["authorized_fix"] == transition.AUTHORIZED_FIX
    assert transition.AUTHORIZED_FIX["declared_encoded_byte_limit_preserved"] is True
    assert transition.AUTHORIZED_FIX["decoded_byte_limit_enforced"] is True
    assert transition.AUTHORIZED_FIX["total_byte_accounting"] == (
        "maximum_of_declared_encoded_and_decoded_bytes"
    )
    assert transition.AUTHORIZED_FIX["sentinel_probe_selection_changed"] is False
    assert transition.AUTHORIZED_FIX[
        "sentinel_band_or_window_contract_changed"
    ] is False
    assert transition.AUTHORIZED_FIX["permissions_changed"] is False
    assert transition.AUTHORIZED_FIX["locks_changed"] is False
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    assert payload["commit_sha256"] == canonical_sha256(body)


def test_v16_binds_all_ten_completed_pre_sentinel_checkpoints() -> None:
    _, payload = _payload()

    resume = payload["transition"]["resume_checkpoints"]
    assert tuple(record["path"] for record in resume) == (
        transition.RESUME_CHECKPOINT_PATHS
    )
    assert transition.RESUME_CHECKPOINT_PATHS == evidence.TRACKED_OUTPUT_PATHS[:10]
    assert len(resume) == 10
    assert resume[-1]["state"] == (
        "complete_target_blind_four_city_worldcover_support"
    )


def test_v16_status_parser_rejects_noncheckpoint_paths() -> None:
    exact = b"".join(
        b"?? " + path.encode("utf-8") + b"\0"
        for path in transition.RESUME_CHECKPOINT_PATHS
    )
    assert transition._parse_status(exact) == frozenset(
        transition.RESUME_CHECKPOINT_PATHS
    )
    assert transition._parse_status(b"") == frozenset()
    with pytest.raises(
        transition.MulticityPlanSentinelContentEncodingHotfixV16Error,
        match="Unexpected dirty path",
    ):
        transition._parse_status(exact + b"?? unexpected.txt\0")


def test_v16_implementation_delta_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr(transition.v15.v14, "_run_git", fake_git)
    transition._implementation_delta(ROOT, implementation)
    mode["extra"] = True
    with pytest.raises(
        transition.MulticityPlanSentinelContentEncodingHotfixV16Error,
        match="outside its exact allowlist",
    ):
        transition._implementation_delta(ROOT, implementation)


@pytest.mark.parametrize("path", [MODULE, SCRIPT])
def test_v16_transition_has_no_data_network_geometry_or_model_reader_imports(
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
