from la_heat.multicity import m3_blind_predictor_daymet_bearer_runtime_v1 as bearer


def test_authorization_does_not_read_or_persist_token(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        bearer.parent,
        "authenticate_authorization",
        lambda _root: {"commit_sha256": bearer.EXPECTED_PARENT_COMMIT},
    )
    monkeypatch.setattr(
        bearer,
        "_record",
        lambda _root, path: {"path": str(path), "bytes": 1, "sha256": "a" * 64},
    )
    payload = bearer.build_authorization(tmp_path)
    assert payload["authorization_audit"]["credential_read"] is False
    assert payload["credential_contract"]["persisted_to_file_log_manifest_or_output"] is False
    assert payload["network_contract"]["redirects_allowed"] is False
    assert payload["permissions"]["read_sentinel_static_landsat_qa_or_target_values"] is False


def test_run_injects_bearer_only_at_allowed_host_and_clears_it(
    monkeypatch, tmp_path
) -> None:
    seen = {}

    class Response:
        is_redirect = False
        is_permanent_redirect = False

    def fake_get(url, *args, **kwargs):
        seen.update(url=url, args=args, kwargs=kwargs)
        return Response()

    monkeypatch.setenv(bearer.TOKEN_ENV, "aaa.bbb.ccc")
    monkeypatch.setattr(bearer, "authenticate_authorization", lambda _root: {})
    monkeypatch.setattr(bearer.parent.requests, "get", fake_get)
    monkeypatch.setattr(
        bearer.parent,
        "run_acquisition",
        lambda _root: bearer.parent.requests.get(
            "https://opendap.earthdata.nasa.gov/example.nc"
        ),
    )

    bearer.run(tmp_path)

    assert seen["kwargs"]["headers"] == {"Authorization": "Bearer aaa.bbb.ccc"}
    assert seen["kwargs"]["allow_redirects"] is False
    assert bearer.TOKEN_ENV not in bearer.os.environ
