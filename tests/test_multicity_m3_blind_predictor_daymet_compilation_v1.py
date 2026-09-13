import numpy as np
import pandas as pd

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


def test_result_validation_preserves_rowwise_complete_daymet_gaps(monkeypatch) -> None:
    columns = list(compilation.REQUIRED_COLUMNS)
    frame = pd.DataFrame(np.zeros((2, len(columns))), columns=columns)
    frame["city_id"] = "test_city"
    frame["tract_geoid"] = ["1", "2"]
    frame["target_date"] = pd.to_datetime(["2025-01-01", "2025-01-01"])
    frame.loc[0, list(compilation.DAYMET_FEATURES)] = np.nan
    monkeypatch.setitem(compilation.EXPECTED_CITY_COUNTS, "test_city", {"row_count": 2})

    audit = compilation._validate_result(frame, "test_city")

    assert audit["daymet_missing_row_count"] == 1
    assert audit["daymet_missing_cell_count"] == 21
    assert audit["rows_dropped_or_imputed"] == 0
