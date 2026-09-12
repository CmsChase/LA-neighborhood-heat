from la_heat.multicity import m3_blind_predictor_full_offline_launch_v1 as launch


def test_full_launch_remains_offline_and_target_blind(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        launch.runner,
        "authenticate_authorization",
        lambda _root: {"commit_sha256": "b" * 64},
    )
    monkeypatch.setattr(
        launch,
        "_read_committed",
        lambda _path: {"commit_sha256": launch.EXPECTED_CANARY_COMMIT},
    )
    monkeypatch.setattr(
        launch,
        "_record",
        lambda _root, path: {"path": str(path), "bytes": 1, "sha256": "a" * 64},
    )
    payload = launch.build_authorization(tmp_path)
    assert payload["permissions"]["network_or_href_reads"] is False
    assert payload["permissions"]["read_daymet_landsat_qa_or_target_values"] is False
    assert payload["permissions"]["fit_predict_score_or_evaluate"] is False
