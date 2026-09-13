from la_heat.multicity import m3_blind_predictor_daymet_compilation_v1 as compilation


def test_authorization_is_offline_and_does_not_open_values(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        compilation,
        "_anchor",
        lambda _root, path, commit: {"path": str(path), "commit_sha256": commit},
    )
    monkeypatch.setattr(
        compilation,
        "_record",
        lambda _root, path: {"path": str(path), "bytes": 1, "sha256": "a" * 64},
    )

    payload = compilation.build_authorization(tmp_path)

    assert payload["expected"]["row_count"] == 23_667
    assert payload["expected"]["feature_count"] == 46
    assert payload["permissions"]["network_or_href_reads"] is False
    assert payload["permissions"]["read_landsat_qa_or_target_values"] is False
    assert payload["permissions"]["fit_predict_score_or_evaluate"] is False
    assert (
        payload["authorization_access_audit"][
            "predictor_or_raster_value_files_opened_or_statted"
        ]
        == 0
    )
