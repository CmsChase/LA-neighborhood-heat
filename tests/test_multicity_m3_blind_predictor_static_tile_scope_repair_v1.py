from la_heat.multicity import m3_blind_predictor_static_tile_scope_repair_v1 as repair


def test_authorization_denies_network_and_targets(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        repair.parent,
        "authenticate_authorization",
        lambda _root: {"commit_sha256": repair.EXPECTED_PARENT_COMMIT},
    )
    monkeypatch.setattr(
        repair,
        "_record",
        lambda _root, path: {"path": str(path), "bytes": 1, "sha256": "a" * 64},
    )
    payload = repair.build_authorization(tmp_path)
    assert payload["permissions"]["network_or_href_reads"] is False
    assert payload["permissions"]["read_daymet_landsat_qa_or_target_values"] is False
