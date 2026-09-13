from la_heat.multicity import m3_blind_predictor_daymet_support_repair_v1 as repair


def test_support_repair_preserves_missingness(monkeypatch, tmp_path) -> None:
    previous = {
        "schema_version": 1,
        "inputs": {},
        "expected": {"row_count": 23_667, "feature_count": 46},
        "commit_sha256": repair.EXPECTED_PATH_REPAIR_COMMIT,
    }
    monkeypatch.setattr(repair.parent, "_read_committed", lambda _path: previous)
    monkeypatch.setattr(
        repair,
        "_record",
        lambda _root, path: {"path": str(path), "bytes": 1, "sha256": "a" * 64},
    )

    payload = repair.build_authorization(tmp_path)

    assert payload["support_incident"]["daymet_missing_row_count"] == 108
    assert payload["repair_contract"]["preserve_missing_values_without_fill_or_row_drop"]
    assert payload["repair_contract"]["change_feature_values_keys_dates_or_model"] is False
    assert payload["repair_contract"]["read_landsat_qa_or_target_values"] is False
