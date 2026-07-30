from __future__ import annotations

import ast
import json
import shutil
import socket
import tomllib
import urllib.request
import zipfile
from copy import deepcopy
from pathlib import Path

import geopandas
import joblib
import pandas
import pytest
import rasterio

from la_heat.multicity.gshhg_l3_hierarchy_preregistration import (
    CODE_PATHS,
    COMPLETE_STATE,
    EXPECTED_CLOSED_LOCKS,
    EXPECTED_L3_MEMBERS,
    EXPECTED_SELECTED_L2_IDS,
    GshhgL3HierarchyPreregistrationError,
    preregister_gshhg_l3_hierarchy_audit,
)
from la_heat.provenance import canonical_sha256

ROOT = Path(__file__).parents[1]
CONFIG = (
    ROOT
    / "configs"
    / "multicity"
    / "gshhg_l3_hierarchy_audit_preregistration_v1.toml"
)
PLAN = ROOT / "manifests" / "multicity" / "PLAN_READINESS.json"
DECISION = (
    ROOT
    / "manifests"
    / "multicity"
    / "reviews"
    / "portable_water_distance"
    / "WATER_DISTANCE_FREEZE_DECISION.json"
)
MODULE = (
    ROOT
    / "src"
    / "la_heat"
    / "multicity"
    / "gshhg_l3_hierarchy_preregistration.py"
)


def _copy_minimal_project(destination: Path) -> Path:
    required = {
        Path("configs/multicity/experiment.toml"),
        Path("manifests/multicity/PLAN_READINESS.json"),
        Path(
            "manifests/multicity/reviews/portable_water_distance/"
            "WATER_DISTANCE_FREEZE_DECISION.json"
        ),
        *(Path(relative) for relative in CODE_PATHS),
        *(
            path.relative_to(ROOT)
            for path in (ROOT / "configs" / "multicity" / "cities").glob("*.toml")
        ),
    }
    for relative in required:
        source = ROOT / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return (
        destination
        / "configs"
        / "multicity"
        / "gshhg_l3_hierarchy_audit_preregistration_v1.toml"
    )


def _fail(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("Forbidden data, geometry, model, or network reader was called.")


def test_preregistration_generates_and_reauthenticates(tmp_path: Path) -> None:
    destination = tmp_path / "GSHHG_L3_PREREGISTRATION.json"

    payload = preregister_gshhg_l3_hierarchy_audit(
        CONFIG,
        output_path=destination,
    )

    assert payload["state"] == COMPLETE_STATE
    assert payload["source_lock_created"] is False
    assert payload["algorithm_lock_created"] is False
    assert payload["feature_names_frozen"] is False
    assert payload["predictor_build_authorized"] is False
    assert payload["locks"] == EXPECTED_CLOSED_LOCKS
    assert payload["hierarchy_contract"]["selected_l2_source_ids"] == (
        EXPECTED_SELECTED_L2_IDS
    )
    assert payload["source_identity_inherited_without_archive_access"][
        "required_l3_members"
    ] == EXPECTED_L3_MEMBERS
    assert payload["access_contract"]["gshhg_l3_member_opened"] is False
    assert payload["access_contract"]["distance_values_computed"] is False
    assert payload["next_gate"]["stage_id"] == (
        "authenticate_committed_l3_preregistration_and_authorize_geometry_audit"
    )

    verified = preregister_gshhg_l3_hierarchy_audit(
        CONFIG,
        output_path=destination,
        write=False,
    )
    assert verified["commit_sha256"] == payload["commit_sha256"]


def test_preregistration_works_without_any_data_or_result_directory(
    tmp_path: Path,
) -> None:
    project = tmp_path / "minimal"
    config = _copy_minimal_project(project)
    output = project / "manifest-output" / "PREREGISTRATION.json"

    payload = preregister_gshhg_l3_hierarchy_audit(
        config,
        output_path=output,
    )

    assert payload["state"] == COMPLETE_STATE
    for name in ("data", "exports", "reports"):
        assert not (project / name).exists()
    assert output.is_file()


def test_minimal_project_file_access_is_allowlisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "minimal"
    config = _copy_minimal_project(project)
    output = project / "manifest-output" / "PREREGISTRATION.json"
    allowed = {
        (project / "configs/multicity/experiment.toml").resolve(),
        (project / "manifests/multicity/PLAN_READINESS.json").resolve(),
        (
            project
            / "manifests/multicity/reviews/portable_water_distance/"
            "WATER_DISTANCE_FREEZE_DECISION.json"
        ).resolve(),
        *((project / relative).resolve() for relative in CODE_PATHS),
        *(
            path.resolve()
            for path in (project / "configs/multicity/cities").glob("*.toml")
        ),
        output.with_suffix(output.suffix + ".partial").resolve(),
    }
    observed: set[Path] = set()
    original_open = Path.open
    resolved_project = project.resolve()

    def guarded_open(path: Path, *args: object, **kwargs: object) -> object:
        resolved = path.resolve()
        if resolved.is_relative_to(resolved_project):
            observed.add(resolved)
            if resolved not in allowed:
                raise AssertionError(f"Unexpected project file access: {resolved}")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    preregister_gshhg_l3_hierarchy_audit(config, output_path=output)

    assert observed <= allowed
    assert not any(
        path.is_relative_to(project / prefix)
        for path in observed
        for prefix in ("data", "exports", "reports")
    )


def test_preregistration_calls_no_forbidden_reader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(zipfile, "ZipFile", _fail)
    monkeypatch.setattr(geopandas, "read_file", _fail)
    monkeypatch.setattr(pandas, "read_parquet", _fail)
    monkeypatch.setattr(rasterio, "open", _fail)
    monkeypatch.setattr(joblib, "load", _fail)
    monkeypatch.setattr(socket.socket, "connect", _fail)
    monkeypatch.setattr(urllib.request, "urlopen", _fail)

    import la_heat.multicity.gshhg_geometry_pilot as geometry_pilot
    import la_heat.multicity.plan_audit as plan_audit
    import la_heat.multicity.portable_water_distance_freeze as freeze_decision

    monkeypatch.setattr(geometry_pilot, "audit_gshhg_geometry_pilot", _fail)
    monkeypatch.setattr(plan_audit, "audit_multicity_plan", _fail)
    monkeypatch.setattr(
        freeze_decision,
        "audit_portable_water_distance_freeze_decision",
        _fail,
    )

    payload = preregister_gshhg_l3_hierarchy_audit(
        CONFIG,
        output_path=tmp_path / "PREREGISTRATION.json",
    )
    assert payload["access_contract"]["preregistration_program_network_requests"] == 0


def test_generator_imports_and_fingerprint_paths_are_tracked_only() -> None:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])
    assert imported_roots.isdisjoint(
        {
            "geopandas",
            "joblib",
            "pandas",
            "rasterio",
            "requests",
            "socket",
            "urllib",
            "zipfile",
        }
    )
    forbidden_prefixes = ("data/", "exports/", "reports/", "manifests/")
    assert not any(path.startswith(forbidden_prefixes) for path in CODE_PATHS)
    assert not any(
        token in path.lower()
        for path in CODE_PATHS
        for token in ("target", "prediction", "model_lock", "final_evaluation")
    )


def test_preregistration_manifest_tampering_fails_closed(tmp_path: Path) -> None:
    destination = tmp_path / "PREREGISTRATION.json"
    preregister_gshhg_l3_hierarchy_audit(CONFIG, output_path=destination)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    payload["source_lock_created"] = True
    destination.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        GshhgL3HierarchyPreregistrationError,
        match="invalid internal commit",
    ):
        preregister_gshhg_l3_hierarchy_audit(
            CONFIG,
            output_path=destination,
            write=False,
        )


def test_preregistration_refuses_different_valid_existing_manifest(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "PREREGISTRATION.json"
    payload = preregister_gshhg_l3_hierarchy_audit(
        CONFIG,
        output_path=destination,
    )
    changed = deepcopy(payload)
    changed["scope"] = "changed"
    body = {key: value for key, value in changed.items() if key != "commit_sha256"}
    changed["commit_sha256"] = canonical_sha256(body)
    destination.write_text(json.dumps(changed), encoding="utf-8")

    with pytest.raises(
        GshhgL3HierarchyPreregistrationError,
        match="already exists with different bytes",
    ):
        preregister_gshhg_l3_hierarchy_audit(
            CONFIG,
            output_path=destination,
        )


def test_any_config_byte_change_fails_closed(tmp_path: Path) -> None:
    changed = tmp_path / CONFIG.name
    raw = CONFIG.read_text(encoding="utf-8")
    assert raw.count("selection_may_use_bbox = false") == 1
    changed.write_text(
        raw.replace(
            "selection_may_use_bbox = false",
            "selection_may_use_bbox = true",
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        GshhgL3HierarchyPreregistrationError,
        match="config bytes changed",
    ):
        preregister_gshhg_l3_hierarchy_audit(
            changed,
            output_path=tmp_path / "PREREGISTRATION.json",
        )


@pytest.mark.parametrize(
    ("source", "field", "replacement"),
    [
        (PLAN, "next_safe_stage", "changed"),
        (DECISION, "outcome", "changed"),
    ],
)
def test_prerequisite_substitution_fails_closed(
    tmp_path: Path,
    source: Path,
    field: str,
    replacement: str,
) -> None:
    project = tmp_path / "minimal"
    config = _copy_minimal_project(project)
    target = project / source.relative_to(ROOT)
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload[field] = replacement
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    payload["commit_sha256"] = canonical_sha256(body)
    target.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        GshhgL3HierarchyPreregistrationError,
        match="changed",
    ):
        preregister_gshhg_l3_hierarchy_audit(
            config,
            output_path=project / "PREREGISTRATION.json",
        )


def test_manifest_commit_hash_is_canonical(tmp_path: Path) -> None:
    destination = tmp_path / "PREREGISTRATION.json"
    payload = preregister_gshhg_l3_hierarchy_audit(
        CONFIG,
        output_path=destination,
    )
    body = {key: value for key, value in payload.items() if key != "commit_sha256"}
    assert canonical_sha256(body) == payload["commit_sha256"]


def test_config_records_exact_phase_order_and_no_clobber_contract() -> None:
    with CONFIG.open("rb") as handle:
        config = tomllib.load(handle)
    failure = config["phase_order_and_failure"]
    assert failure["phase_2_may_start_after_any_phase_1_failure"] is False
    assert failure["silent_repair_reselection_or_fallback_allowed"] is False
    assert (
        failure[
            "structural_amendment_must_be_separately_committed_and_pushed_before_probe_or_distance"
        ]
        is True
    )
    assert config["outputs"]["geometry_export_allowed"] is False
