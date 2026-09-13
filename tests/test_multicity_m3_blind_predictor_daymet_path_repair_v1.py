from la_heat.multicity import m3_blind_predictor_daymet_path_repair_v1 as repair


def test_repair_scope_is_path_only(monkeypatch, tmp_path) -> None:
    parent = {
        "schema_version": 1,
        "algorithm_version": "parent",
        "state": "parent",
        "inputs": {},
        "expected": {"row_count": 23_667, "feature_count": 46},
        "code_identity": {},
        "run_id": "old",
        "claim_id": "old",
        "commit_sha256": repair.EXPECTED_PARENT_AUTHORIZATION_COMMIT,
    }
    monkeypatch.setattr(repair.parent, "_read_committed", lambda _path: parent)
    monkeypatch.setattr(
        repair,
        "_record",
        lambda _root, path: {"path": str(path), "bytes": 1, "sha256": "a" * 64},
    )

    payload = repair.build_authorization(tmp_path)

    assert payload["incident"]["completed_city_outputs"] == 0
    assert payload["repair_contract"]["retain_original_bytes_and_sha256_validation"] is True
    assert payload["repair_contract"]["change_scientific_values_schema_keys_or_features"] is False
    assert payload["repair_contract"]["network_or_href_reads"] is False
    assert payload["repair_contract"]["read_landsat_qa_or_target_values"] is False
