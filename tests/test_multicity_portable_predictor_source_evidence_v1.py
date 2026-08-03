from __future__ import annotations

import ast
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.crs import CRS
from shapely.geometry import box

from la_heat.multicity import portable_predictor_source_evidence_v1 as evidence
from la_heat.provenance import canonical_frame_sha256, canonical_sha256, sha256_file

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / evidence.CONFIG_PATH


def test_exact_config_scope_and_tracked_outputs_are_closed() -> None:
    config = evidence._read_config(CONFIG)
    assert sha256_file(CONFIG) == evidence.CONFIG_SHA256
    assert config.raw["stage"]["authorized_city_ids"] == [
        "phoenix_az",
        "houston_tx",
        "chicago_il",
    ]
    authorized = evidence.expected_authorized_now()
    assert authorized["boundary_and_public_metadata_staging"] is False
    assert authorized["portable_predictor_missing_source_evidence_staging"] is True
    assert sum(authorized.values()) == 1
    scope = evidence.expected_plan_authorization_scope()
    assert scope["configuration"] == {
        "path": evidence.CONFIG_PATH,
        "sha256": evidence.CONFIG_SHA256,
    }
    assert scope["tracked_output_paths"] == list(evidence.TRACKED_OUTPUT_PATHS)
    assert len(scope["tracked_output_paths"]) == 8
    assert scope["write_contract"] == {
        "append_only": True,
        "terminal_written_last": True,
        "terminal_requires_all_city_checkpoints": True,
        "check_only_network_requests": 0,
        "tracked_output_set_must_be_exact": True,
    }
    assert scope["prohibitions"]["target_or_qa_values"] is True
    assert scope["prohibitions"]["predictor_construction"] is True


class _RedirectResponse:
    status_code = 302
    url = "https://example.test/exact"
    headers = {"location": "https://evil.test/payload"}

    def close(self) -> None:
        pass


class _Session:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def get(self, url: str, **kwargs: object) -> Any:
        self.calls.append(("get", url, kwargs))
        return self.response


def test_strict_client_rejects_redirect_before_following_it() -> None:
    session = _Session(_RedirectResponse())
    client = evidence._StrictClient(
        session,
        allowed={"get": {("example.test", "/exact")}},
        maximum_requests=1,
    )
    with pytest.raises(
        evidence.PortablePredictorSourceEvidenceV1Error,
        match="redirects are prohibited",
    ):
        client.get("https://example.test/exact", allow_redirects=True)
    assert len(session.calls) == 1
    assert session.calls[0][2]["allow_redirects"] is False


def test_strict_client_rejects_unregistered_method_host_or_path_before_request() -> None:
    session = _Session(_RedirectResponse())
    client = evidence._StrictClient(
        session,
        allowed={"get": {("example.test", "/exact")}},
        maximum_requests=1,
    )
    with pytest.raises(evidence.PortablePredictorSourceEvidenceV1Error):
        client.get("https://example.test/other")
    with pytest.raises(evidence.PortablePredictorSourceEvidenceV1Error):
        client.get("https://evil.test/exact")
    assert session.calls == []


def _planning_payload() -> dict[str, Any]:
    locks = {key: False for key in evidence._REQUIRED_FALSE_LOCKS}
    locks["portable_water_distance_source_locked"] = True
    locks["portable_water_distance_algorithm_locked"] = True
    body: dict[str, Any] = {
        "schema_version": 10,
        "state": "planning_ready",
        "experiment_id": "la_to_three_city_zero_shot_v1",
        "authorized_now": evidence.expected_authorized_now(),
        "portable_predictor_source_evidence_stage_authorization_scope": (
            evidence.expected_plan_authorization_scope()
        ),
        "locks": locks,
    }
    body["commit_sha256"] = canonical_sha256(body)
    return body


def test_plan_authentication_calls_publication_aware_v10_authenticator(tmp_path: Path) -> None:
    raw = evidence._read_config(CONFIG).raw
    plan_path = tmp_path / raw["stage"]["plan_path"]
    plan_path.parent.mkdir(parents=True)
    payload = _planning_payload()
    plan_path.write_text(json.dumps(payload), encoding="utf-8")
    config = evidence.SourceEvidenceConfig(CONFIG, tmp_path, deepcopy(raw))
    calls: list[dict[str, Any]] = []

    def authenticate(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return deepcopy(payload)

    record = evidence._authenticate_plan(
        config,
        publication_authenticator=authenticate,
    )
    assert len(calls) == 1
    assert calls[0]["write"] is False
    assert calls[0]["output_path"] == plan_path
    assert record["authorized_now"] == evidence.expected_authorized_now()
    assert (
        record["portable_predictor_source_evidence_stage_authorization_scope"]
        == evidence.expected_plan_authorization_scope()
    )


def test_plan_authentication_rejects_unpublished_or_different_payload(tmp_path: Path) -> None:
    raw = evidence._read_config(CONFIG).raw
    plan_path = tmp_path / raw["stage"]["plan_path"]
    plan_path.parent.mkdir(parents=True)
    payload = _planning_payload()
    plan_path.write_text(json.dumps(payload), encoding="utf-8")
    config = evidence.SourceEvidenceConfig(CONFIG, tmp_path, deepcopy(raw))
    different = deepcopy(payload)
    different["state"] = "forged"
    with pytest.raises(
        evidence.PortablePredictorSourceEvidenceV1Error,
        match="Publication-aware",
    ):
        evidence._authenticate_plan(
            config,
            publication_authenticator=lambda **_: different,
        )


def test_resume_authentication_allows_only_exact_untracked_checkpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = evidence._read_config(CONFIG).raw
    config = evidence.SourceEvidenceConfig(CONFIG, tmp_path, deepcopy(raw))
    plan_path = config.project_path(raw["stage"]["plan_path"])
    plan_path.parent.mkdir(parents=True)
    payload = _planning_payload()
    plan_path.write_text(json.dumps(payload), encoding="utf-8")
    checkpoint = evidence.TRACKED_OUTPUT_PATHS[0]
    checkpoint_path = config.project_path(checkpoint)
    checkpoint_path.parent.mkdir(parents=True)
    checkpoint_path.write_text("{}", encoding="utf-8")
    head = "1" * 40
    mode = {"unexpected": False}

    def fake_git(_root: Path, *args: str, **kwargs: Any) -> str | bytes:
        del kwargs
        if args == ("branch", "--show-current"):
            return "main\n"
        if args == ("rev-parse", "HEAD"):
            return head + "\n"
        if args == ("rev-parse", "origin/main"):
            return head + "\n"
        if args[:2] == ("status", "--porcelain=v1"):
            status = b"?? " + checkpoint.encode("utf-8") + b"\0"
            if mode["unexpected"]:
                status += b"?? unexpected.txt\0"
            return status
        raise AssertionError(args)

    monkeypatch.setattr(evidence, "_run_git", fake_git)
    observed = evidence._authenticate_plan_for_resume(
        config,
        allowed_untracked_paths=(checkpoint,),
        publication_locator=lambda *_args, **_kwargs: "2" * 40,
        historical_authenticator=lambda *_args, **_kwargs: deepcopy(payload),
    )
    assert observed["commit_sha256"] == payload["commit_sha256"]

    mode["unexpected"] = True
    with pytest.raises(
        evidence.PortablePredictorSourceEvidenceV1Error,
        match="Unexpected dirty path",
    ):
        evidence._authenticate_plan_for_resume(
            config,
            allowed_untracked_paths=(checkpoint,),
            publication_locator=lambda *_args, **_kwargs: "2" * 40,
            historical_authenticator=lambda *_args, **_kwargs: deepcopy(payload),
        )


def test_nlcd_bounds_are_native_grid_aligned_with_exact_halo() -> None:
    boundary = gpd.GeoDataFrame(
        {"city": ["synthetic"]},
        geometry=[box(46, 76, 104, 164)],
        crs="EPSG:5070",
    )
    assert evidence._aligned_nlcd_bounds(
        boundary,
        resolution=30,
        edge_offset=15,
        halo_pixels=2,
    ) == (-15.0, 15.0, 165.0, 225.0)


def test_non_geometry_checkpoint_hash_has_an_explicit_stable_sort_order(
    tmp_path: Path,
) -> None:
    frame = pd.DataFrame(
        {
            "concept_id": ["G2-ORNL_CLOUD", "G1-ORNL_CLOUD"],
            "variable": ["tmin", "tmax"],
            "year": [2025, 2025],
            "title": ["second", "first"],
        }
    )
    path = tmp_path / "daymet_granules.parquet"
    frame.to_parquet(path, index=False)
    config = evidence.SourceEvidenceConfig(
        CONFIG,
        tmp_path,
        deepcopy(evidence._read_config(CONFIG).raw),
    )
    observed = evidence._parquet_record(config, path, frame, geometry=False)
    expected = canonical_frame_sha256(
        frame,
        sort_by=["variable", "year", "concept_id"],
    )
    assert observed["frame_semantic_sha256"] == expected
    assert evidence._non_geometry_frame_sha256(frame.iloc[::-1]) == expected


def _write_nlcd(path: Path, *, scale: float = 1.0) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=2,
        width=2,
        count=1,
        dtype="uint8",
        crs="EPSG:5070",
        transform=rasterio.Affine(30, 0, 15, 0, -30, 75),
        nodata=0,
    ) as dataset:
        dataset.write(np.array([[11, 21], [0, 95]], dtype="uint8"), 1)
        dataset.update_tags(AREA_OR_POINT="Area")
        dataset.scales = (scale,)
        dataset.offsets = (0.0,)


def test_nlcd_validator_checks_grid_domain_scale_and_offset(tmp_path: Path) -> None:
    path = tmp_path / "nlcd.tif"
    bounds = (15.0, 15.0, 75.0, 75.0)
    _write_nlcd(path)
    record = evidence._inspect_nlcd(path, product="land_cover", bounds=bounds)
    assert record["value_domain_verified"] is True
    assert record["scale"] == 1.0
    assert record["offset"] == 0.0
    bad = tmp_path / "bad-scale.tif"
    _write_nlcd(bad, scale=2.0)
    with pytest.raises(evidence.PortablePredictorSourceEvidenceV1Error):
        evidence._inspect_nlcd(bad, product="land_cover", bounds=bounds)


class _FakeSrtm:
    count = 1
    shape = (3601, 3601)
    crs = CRS.from_epsg(4326)
    dtypes = ("int16",)
    nodata = -32768
    scales = (1.0,)
    offsets = (0.0,)
    units = (None,)
    transform = evidence._srtm_expected_transform("N33W112")

    def tags(self) -> dict[str, str]:
        return {"AREA_OR_POINT": "Point"}

    def __enter__(self) -> _FakeSrtm:
        return self

    def __exit__(self, *_: object) -> None:
        pass


def test_srtm_validator_checks_calibration_without_claiming_tiff_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "N33W112.tif"
    path.write_bytes(b"source-byte-placeholder")
    fake = _FakeSrtm()
    monkeypatch.setattr(evidence.rasterio, "open", lambda _: fake)
    record = evidence._inspect_srtm(path, tile_id="N33W112")
    assert record["raster_band_unit_metadata"] is None
    assert record["documented_unit"] == "metre"
    assert record["unit_and_vertical_datum_source"].endswith("not_tiff_claim")
    fake.scales = (2.0,)
    with pytest.raises(evidence.PortablePredictorSourceEvidenceV1Error):
        evidence._inspect_srtm(path, tile_id="N33W112")


def test_check_only_constructs_no_network_client(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = {"state": "checked", "commit_sha256": "x" * 64}
    monkeypatch.setattr(evidence, "_verify_terminal", lambda *_args, **_kwargs: sentinel)

    class _ExplodingClient:
        def __getattribute__(self, name: str) -> Any:
            raise AssertionError(f"network client accessed during check-only: {name}")

    observed = evidence.stage_portable_predictor_source_evidence_v1(
        CONFIG,
        check_only=True,
        client=_ExplodingClient(),
    )
    assert observed is sentinel


def test_initial_terminal_verification_reuses_plan_override_and_awaits_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = evidence._read_config(CONFIG).raw
    config = evidence.SourceEvidenceConfig(CONFIG, tmp_path, deepcopy(raw))
    plan_authorization = {"authenticated_before_writes": True}
    runtime = {"sha256": "r" * 64}
    checkpoints = {
        path: {
            "path": path,
            "bytes": 1,
            "sha256": "f" * 64,
            "commit_sha256": "c" * 64,
            "state": "checkpoint_complete",
        }
        for path in evidence.TRACKED_OUTPUT_PATHS[:-1]
    }
    terminal = {
        "schema_version": evidence.SCHEMA_VERSION,
        "algorithm_version": evidence.ALGORITHM_VERSION,
        "state": evidence.COMPLETE_STATE,
        "stage_id": evidence.STAGE_ID,
        "experiment_id": raw["stage"]["experiment_id"],
        "plan_authorization": plan_authorization,
        "authorization_scope": evidence.expected_plan_authorization_scope(),
        "tracked_output_paths": list(evidence.TRACKED_OUTPUT_PATHS),
        "terminal_written_last": True,
        "config": {
            "path": evidence.CONFIG_PATH,
            "bytes": CONFIG.stat().st_size,
            "sha256": sha256_file(CONFIG),
            "semantic_sha256": config.semantic_sha256,
        },
        "access_contract": raw["access_contract"],
        "tracked_checkpoints": checkpoints,
        "code_runtime": runtime,
        "commit_sha256": "t" * 64,
    }
    terminal_path = config.project_path(evidence.TERMINAL_PATH)

    def fake_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
        del label
        if path == terminal_path:
            return terminal, b"terminal"
        relative = path.relative_to(tmp_path).as_posix()
        record = checkpoints[relative]
        return {
            "state": record["state"],
            "commit_sha256": record["commit_sha256"],
        }, b"checkpoint"

    monkeypatch.setattr(evidence, "_json_with_commit", fake_json)
    monkeypatch.setattr(
        evidence,
        "_verify_file_record",
        lambda _config, record: tmp_path / record["path"],
    )
    monkeypatch.setattr(evidence, "_runtime_record", lambda _config: runtime)
    monkeypatch.setattr(
        evidence,
        "load_multicity_plan",
        lambda _path: SimpleNamespace(path=tmp_path / "experiment.toml"),
    )

    class _Workspace:
        @staticmethod
        def from_plan(_plan: object) -> object:
            return object()

    monkeypatch.setattr(evidence, "MulticityWorkspace", _Workspace)
    monkeypatch.setattr(evidence._geography, "verify_city_geography", lambda *_: {})
    monkeypatch.setattr(
        evidence._footprints,
        "verify_city_source_footprints",
        lambda *_: {},
    )
    monkeypatch.setattr(evidence, "_verify_new_geography", lambda *_: {})
    monkeypatch.setattr(evidence, "_verify_new_source_footprint", lambda *_: {})
    monkeypatch.setattr(evidence, "_verify_city_source_evidence", lambda *_: {})
    monkeypatch.setattr(
        evidence,
        "_authenticate_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("dirty-worktree plan preflight reran")
        ),
    )
    monkeypatch.setattr(
        evidence,
        "_authenticate_output_publication",
        lambda *_: (_ for _ in ()).throw(AssertionError("publication required too early")),
    )
    observed = evidence._verify_terminal(
        config,
        plan_authorization_override=plan_authorization,
        require_publication=False,
    )
    assert observed["publication_status"] == "awaiting_git_publication"


def test_publication_requires_exact_eight_additions_and_direct_v10_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = evidence._read_config(CONFIG).raw
    config = evidence.SourceEvidenceConfig(CONFIG, tmp_path, deepcopy(raw))
    plan_path = config.project_path(raw["stage"]["plan_path"])
    plan_path.parent.mkdir(parents=True)
    plan = _planning_payload()
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    for relative in evidence.TRACKED_OUTPUT_PATHS:
        path = config.project_path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode("utf-8"))
    head = "1" * 40
    publication = "2" * 40
    v10 = "3" * 40
    mode = {"extra": False, "wrong_parent": False}

    from la_heat.multicity import plan_source_evidence_hotfix_transition_v10 as transition

    monkeypatch.setattr(
        transition,
        "_locate_v10_publication_commit",
        lambda *_args, **_kwargs: v10,
    )
    monkeypatch.setattr(evidence, "_is_ancestor", lambda *_: True)
    monkeypatch.setattr(
        evidence,
        "_git_blob",
        lambda root, commit, relative: (root / relative).read_bytes(),
    )

    def fake_git(_root: Path, *args: str, **kwargs: Any) -> str | bytes:
        del kwargs
        if args[:2] == ("rev-parse", "HEAD"):
            return head + "\n"
        if args[:3] == ("log", "--all", "--diff-filter=A"):
            return publication + "\n"
        if args[:3] == ("rev-list", "--parents", "-n"):
            parent = "4" * 40 if mode["wrong_parent"] else v10
            return f"{publication} {parent}\n"
        if args and args[0] == "diff-tree":
            pairs = [("A", path) for path in evidence.TRACKED_OUTPUT_PATHS]
            if mode["extra"]:
                pairs.append(("A", "unexpected.txt"))
            return b"".join(
                status.encode("ascii") + b"\0" + path.encode("utf-8") + b"\0"
                for status, path in pairs
            )
        if args and args[0] == "log":
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(evidence, "_run_git", fake_git)
    terminal = {"tracked_output_paths": list(evidence.TRACKED_OUTPUT_PATHS)}
    assert evidence._authenticate_output_publication(config, terminal) == publication
    mode["extra"] = True
    with pytest.raises(
        evidence.PortablePredictorSourceEvidenceV1Error,
        match="exactly the eight",
    ):
        evidence._authenticate_output_publication(config, terminal)
    mode["extra"] = False
    mode["wrong_parent"] = True
    with pytest.raises(
        evidence.PortablePredictorSourceEvidenceV1Error,
        match="direct child",
    ):
        evidence._authenticate_output_publication(config, terminal)


def test_module_imports_no_target_model_or_result_reader() -> None:
    source = (ROOT / "src/la_heat/multicity/portable_predictor_source_evidence_v1.py").read_text(
        encoding="utf-8"
    )
    imported = {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module or ""
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    )
    forbidden = {
        "la_heat.final_evaluation_protocol",
        "la_heat.final_evaluation_targets",
        "la_heat.modeling",
        "la_heat.target_grid",
    }
    assert imported.isdisjoint(forbidden)
