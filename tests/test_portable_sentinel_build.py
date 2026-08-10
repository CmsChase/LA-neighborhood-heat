from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from la_heat.multicity.portable_sentinel_build import (
    _clean_message,
    _stage_for_city,
)
from la_heat.provenance import parquet_file_record, sha256_file
from la_heat.sentinel_feature_builder import _acquisition_cache_is_current


def test_portable_stage_uses_city_timezone_and_frozen_science(tmp_path: Path) -> None:
    stage = _stage_for_city(tmp_path, "phoenix_az", "America/Phoenix")

    assert stage.raw["window"]["local_timezone"] == "America/Phoenix"
    assert stage.minimum_coverage == 0.8
    assert stage.minimum_acquisitions == 3
    assert stage.raw["qa"]["accepted_scl_classes"] == [4, 5]
    assert set(stage.albedo_coefficients) == {
        "B02",
        "B03",
        "B04",
        "B08",
        "B11",
        "B12",
    }


def test_relative_metadata_path_survives_bundle_relocation(tmp_path: Path) -> None:
    metadata = tmp_path / "data/raw/metadata/item.xml"
    metadata.parent.mkdir(parents=True)
    metadata.write_bytes(b"<xml />")
    cache = tmp_path / "runtime/by_acquisition/example"
    cache.mkdir(parents=True)
    frame = pd.DataFrame({"tract_geoid": ["1"], "value": [2.0]})
    output = cache / "acquisition_tract.parquet"
    frame.to_parquet(output, index=False)
    lock = {"physical_acquisition_id": "example"}
    summary = {
        "state": "complete",
        "cache_lock": lock,
        "tract_count": 1,
        "product_metadata": [
            {
                "product_metadata_path": metadata.relative_to(tmp_path).as_posix(),
                "product_metadata_sha256": sha256_file(metadata),
            }
        ],
        "output_file": parquet_file_record(output, frame),
    }
    (cache / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

    assert _acquisition_cache_is_current(
        cache,
        expected_lock=lock,
        metadata_path_root=tmp_path,
    )


def test_status_error_does_not_persist_signed_query() -> None:
    value = "failed https://example.test/file.tif?sig=secret&se=tomorrow"

    assert _clean_message(value) == "failed https://example.test/file.tif?<redacted>"
