from __future__ import annotations

from pathlib import Path

from la_heat.multicity import m3_blind_predictor_sentinel_static_assembly_v1 as assembly


def test_authorization_contract_is_offline_and_target_blind(monkeypatch, tmp_path: Path) -> None:
    acquisition = {
        "schema_version": 1,
        "commit_sha256": assembly.EXPECTED_ACQUISITION_COMMIT,
    }
    monkeypatch.setattr(assembly, "_read_committed", lambda *_args, **_kwargs: acquisition)
    monkeypatch.setattr(
        assembly,
        "_load_config",
        lambda _root: {
            "runtime": {
                "network_allowed": False,
                "href_reads_allowed": False,
            }
        },
    )
    monkeypatch.setattr(
        assembly,
        "_record",
        lambda _root, path: {"path": str(path), "bytes": 1, "sha256": "a" * 64},
    )
    payload = assembly.build_authorization(tmp_path)
    assert payload["permissions"]["network_or_href_reads"] is False
    assert payload["permissions"]["read_daymet_landsat_qa_or_target_values"] is False
    assert payload["permissions"]["fit_predict_score_or_evaluate"] is False
    assert payload["outputs"]["static_feature_count"] == 18
    assert payload["outputs"]["sentinel_feature_count"] == 5
    assert payload["outputs"]["tract_date_key_count"] == 23667


def test_full_run_requires_canary(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(assembly, "authenticate_authorization", lambda _root: {"run_id": "x"})
    monkeypatch.setattr(assembly, "_load_config", lambda _root: {})
    monkeypatch.setattr(assembly, "_canary_current", lambda *_args: False)
    try:
        assembly.run(tmp_path)
    except assembly.M3BlindAssemblyError as error:
        assert "canary" in str(error).lower()
    else:
        raise AssertionError("full run was allowed without a canary")
