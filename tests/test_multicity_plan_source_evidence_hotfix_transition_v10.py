from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from la_heat.multicity import plan_source_evidence_hotfix_transition_v10 as transition
from la_heat.provenance import canonical_sha256

ROOT = Path(__file__).resolve().parents[1]


def _v9_payload() -> dict[str, Any]:
    path = ROOT / transition.PLAN_PATH
    raw = path.read_bytes()
    payload = json.loads(raw)
    transition._validate_v9(payload, raw)
    return payload


def test_v10_hotfix_preserves_every_permission_lock_and_scientific_scope() -> None:
    predecessor = _v9_payload()
    code_files = {"runtime.py": {"sha256": "a" * 64}}
    payload = transition._build_v10_payload(
        predecessor,
        predecessor_bytes=transition.V9_BYTES,
        precondition_commit="1" * 40,
        code_files=code_files,
    )
    assert payload["schema_version"] == 10
    assert payload["authorized_now"] == predecessor["authorized_now"]
    assert payload["locks"] == predecessor["locks"]
    assert (
        payload["portable_predictor_source_evidence_stage_authorization_scope"]
        == predecessor["portable_predictor_source_evidence_stage_authorization_scope"]
    )
    fix = payload["transition"]["authorized_fix"]
    assert fix["use_explicit_sort_by"] == ["variable", "year", "concept_id"]
    assert fix["permissions_changed"] is False
    assert fix["locks_changed"] is False
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    assert payload["commit_sha256"] == canonical_sha256(body)


def test_resume_checkpoint_constants_match_the_v9_generated_files() -> None:
    assert transition._verify_resume_checkpoints(ROOT) == list(
        transition.RESUME_CHECKPOINTS
    )
    assert set(transition.RESUME_CHECKPOINT_PATHS).issubset(
        set(transition.TRACKED_OUTPUT_PATHS)
    )


def test_status_parser_allows_only_preregistered_untracked_outputs() -> None:
    path = transition.RESUME_CHECKPOINT_PATHS[0]
    assert transition._parse_status_paths(b"?? " + path.encode() + b"\0") == {
        path
    }
    with pytest.raises(
        transition.MulticityPlanSourceEvidenceHotfixTransitionV10Error,
        match="Unexpected dirty path",
    ):
        transition._parse_status_paths(b"?? unexpected.txt\0")
    with pytest.raises(
        transition.MulticityPlanSourceEvidenceHotfixTransitionV10Error,
        match="only append-only untracked",
    ):
        transition._parse_status_paths(b" M " + path.encode() + b"\0")


def test_implementation_delta_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    precondition = "1" * 40
    mode = {"extra": False}

    def fake_git(_root: Path, *args: str, **kwargs: Any) -> str | bytes:
        del kwargs
        if args[:4] == ("rev-list", "--parents", "-n", "1"):
            return f"{precondition} {transition.V9_PUBLICATION_COMMIT}\n"
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
    transition._implementation_delta(ROOT, precondition)
    mode["extra"] = True
    with pytest.raises(
        transition.MulticityPlanSourceEvidenceHotfixTransitionV10Error,
        match="outside its exact allowlist",
    ):
        transition._implementation_delta(ROOT, precondition)


def test_exact_v10_payload_rejects_tampering() -> None:
    predecessor = _v9_payload()
    payload = transition._build_v10_payload(
        predecessor,
        predecessor_bytes=transition.V9_BYTES,
        precondition_commit="1" * 40,
        code_files={},
    )
    transition._validate_exact_v10_payload(payload, payload)
    tampered = deepcopy(payload)
    tampered["next_safe_stage"] = "skip_ahead"
    with pytest.raises(
        transition.MulticityPlanSourceEvidenceHotfixTransitionV10Error,
        match="internal commit",
    ):
        transition._validate_exact_v10_payload(tampered, payload)
