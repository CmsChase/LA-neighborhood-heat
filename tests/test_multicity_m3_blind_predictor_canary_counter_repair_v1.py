from la_heat.multicity import m3_blind_predictor_canary_counter_repair_v1 as repair


def test_authorization_is_provenance_only(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        repair.parent,
        "authenticate_authorization",
        lambda _root: {"commit_sha256": repair.EXPECTED_PARENT_COMMIT},
    )
    evidence = {
        "commit_sha256": repair.EXPECTED_OLD_CANARY_COMMIT,
        "gshhg_completed_chunk_count": 0,
    }
    progress = {
        "commit_sha256": "b" * 64,
        "city_id": "miami_fl",
        "chunk_count": 1,
        "completed_chunk_indices": [1],
    }
    monkeypatch.setattr(
        repair,
        "_read_committed",
        lambda path, **_kwargs: (
            progress if path.name == "gshhg_distance_progress.json" else evidence
        ),
    )
    monkeypatch.setattr(
        repair,
        "_record",
        lambda _root, path: {"path": str(path), "bytes": 1, "sha256": "a" * 64},
    )
    payload = repair.build_authorization(tmp_path)
    assert payload["permissions"]["modify_or_recompute_existing_outputs"] is False
    assert payload["permissions"]["start_full_assembly"] is False
    assert payload["permissions"]["network_or_href_reads"] is False
