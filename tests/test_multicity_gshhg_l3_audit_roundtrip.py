from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
import pytest
from geopandas.testing import assert_geodataframe_equal
from shapely.geometry import box

import la_heat.multicity.gshhg_l3_hierarchy_audit as audit_module
from la_heat.multicity.gshhg_l3_hierarchy_audit import (
    AUTHORIZED_MEMBERS,
    EXPECTED_COLUMNS,
    EXPECTED_DTYPES,
    LAYER_MEMBER_QUARTETS,
    _read_isolated_layers,
)

L4_DECOY = "GSHHS_shp/f/GSHHS_f_L4.shp"


def _synthetic_layer(level: int) -> gpd.GeoDataFrame:
    offset = float(level * 10)
    geometries = [
        box(offset, 0.0, offset + 1.0, 1.0),
        box(offset + 2.0, 0.0, offset + 3.0, 1.0),
    ]
    return gpd.GeoDataFrame(
        {
            "id": pd.Series([f"{level}01", f"{level}02-E"], dtype="str"),
            "level": pd.Series([level, level], dtype="int32"),
            "source": pd.Series(["WDBII", "WVS"], dtype="str"),
            "parent_id": pd.Series([0, level * 100], dtype="int32"),
            "sibling_id": pd.Series([level * 100 + 2, level * 100 + 1], dtype="int32"),
            "area": pd.Series([1.25 + level, 2.5 + level], dtype="float64"),
        },
        geometry=geometries,
        crs="EPSG:4326",
    )


def _write_layer_quartets(
    directory: Path,
) -> tuple[dict[int, gpd.GeoDataFrame], dict[str, Path]]:
    expected_frames: dict[int, gpd.GeoDataFrame] = {}
    member_paths: dict[str, Path] = {}
    directory.mkdir()
    for level in (1, 2, 3):
        frame = _synthetic_layer(level)
        shapefile = directory / f"GSHHS_f_L{level}.shp"
        pyogrio.write_dataframe(
            frame,
            shapefile,
            driver="ESRI Shapefile",
            encoding="UTF-8",
        )
        expected_frames[level] = frame
        for member in LAYER_MEMBER_QUARTETS[level]:
            path = directory / Path(member).name
            assert path.is_file()
            member_paths[member] = path
    return expected_frames, member_paths


def _write_authorized_archive(
    archive_path: Path,
    member_paths: dict[str, Path],
) -> None:
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member in AUTHORIZED_MEMBERS:
            archive.write(member_paths[member], arcname=member)
        archive.writestr(L4_DECOY, b"decoy that the audit must never open")


def _central_directory_contract(archive_path: Path) -> tuple[int, int, str]:
    with zipfile.ZipFile(archive_path, "r") as archive:
        members = archive.infolist()
        inventory = [
            {
                "name": member.filename,
                "bytes": member.file_size,
                "compressed_bytes": member.compress_size,
                "crc32": f"{member.CRC:08x}",
                "method": member.compress_type,
                "flags": member.flag_bits,
                "external_attr": member.external_attr,
            }
            for member in members
        ]
    inventory_bytes = json.dumps(
        inventory,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return (
        len(members),
        sum(int(member["bytes"]) for member in inventory),
        hashlib.sha256(inventory_bytes).hexdigest(),
    )


def _source_contract(archive_path: Path) -> dict[str, object]:
    raw = archive_path.read_bytes()
    member_count, uncompressed_bytes, inventory_sha256 = _central_directory_contract(
        archive_path
    )
    return {
        "source_id": "synthetic-gshhg",
        "dataset": "synthetic GSHHG",
        "version": "test-only",
        "archive_path": str(archive_path),
        "expected_archive_bytes": len(raw),
        "expected_archive_sha256": hashlib.sha256(raw).hexdigest(),
        "published_archive_md5": hashlib.md5(raw, usedforsecurity=False).hexdigest(),
        "expected_archive_member_count_from_v2": member_count,
        "expected_archive_uncompressed_bytes_from_v2": uncompressed_bytes,
        "expected_member_inventory_sha256_from_v2": inventory_sha256,
    }


def _pilot_contract(member_paths: dict[str, Path]) -> dict[str, object]:
    inherited = {
        member: hashlib.sha256(member_paths[member].read_bytes()).hexdigest()
        for member in AUTHORIZED_MEMBERS
        if "_L1." in member or "_L2." in member
    }
    return {"source_archive": {"required_member_sha256": inherited}}


def test_isolated_layer_roundtrip_opens_only_authorized_quartets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_frames, member_paths = _write_layer_quartets(tmp_path / "source-quartets")
    archive_path = tmp_path / "synthetic-gshhg.zip"
    _write_authorized_archive(archive_path, member_paths)

    opened_zip_members: list[str] = []
    original_zip_open = zipfile.ZipFile.open

    def guarded_zip_open(
        archive: zipfile.ZipFile,
        name: str | zipfile.ZipInfo,
        mode: str = "r",
        pwd: bytes | None = None,
        *,
        force_zip64: bool = False,
    ) -> object:
        member = name.filename if isinstance(name, zipfile.ZipInfo) else name
        if mode == "r":
            assert member in AUTHORIZED_MEMBERS
            opened_zip_members.append(member)
        return original_zip_open(
            archive,
            name,
            mode=mode,
            pwd=pwd,
            force_zip64=force_zip64,
        )

    staged_roots: list[Path] = []
    os_temporary_root = tmp_path / "os-temporary-root"
    os_temporary_root.mkdir()
    original_temporary_directory = tempfile.TemporaryDirectory

    def tracked_temporary_directory(*args: object, **kwargs: object) -> object:
        assert "dir" not in kwargs
        kwargs["dir"] = os_temporary_root
        temporary = original_temporary_directory(*args, **kwargs)
        staged_roots.append(Path(temporary.name))
        return temporary

    parsed_paths: list[Path] = []
    original_read_file = gpd.read_file

    def guarded_read_file(path: str | Path, *args: object, **kwargs: object) -> gpd.GeoDataFrame:
        parsed_path = Path(path)
        assert parsed_path.suffix == ".shp"
        assert ".zip" not in str(parsed_path).lower()
        assert "/vsizip/" not in parsed_path.as_posix().lower()
        assert parsed_path.is_file()
        assert any(parsed_path.is_relative_to(root) for root in staged_roots)
        parsed_paths.append(parsed_path)
        return original_read_file(path, *args, **kwargs)

    def forbidden_testzip(_archive: zipfile.ZipFile) -> None:
        raise AssertionError("ZipFile.testzip() must not inspect unauthorized members.")

    monkeypatch.setattr(zipfile.ZipFile, "open", guarded_zip_open)
    monkeypatch.setattr(zipfile.ZipFile, "testzip", forbidden_testzip)
    monkeypatch.setattr(
        audit_module.tempfile,
        "TemporaryDirectory",
        tracked_temporary_directory,
    )
    monkeypatch.setattr(audit_module.gpd, "read_file", guarded_read_file)

    access_evidence: dict[str, object] = {}
    observed_frames, archive_record = _read_isolated_layers(
        archive_path,
        source_config=_source_contract(archive_path),
        pilot=_pilot_contract(member_paths),
        callback=None,
        access_evidence=access_evidence,
    )

    assert opened_zip_members == list(AUTHORIZED_MEMBERS)
    assert L4_DECOY not in opened_zip_members
    assert archive_record["member_open_log"] == list(AUTHORIZED_MEMBERS)
    assert archive_record["authorized_member_count"] == 12
    assert archive_record["unauthorized_member_open_count"] == 0
    assert archive_record["zipfile_testzip_called"] is False
    assert archive_record["isolated_os_temporary_staging_deleted"] is True
    assert access_evidence["member_open_log"] == list(AUTHORIZED_MEMBERS)

    assert len(parsed_paths) == 3
    assert {path.name for path in parsed_paths} == {
        "GSHHS_f_L1.shp",
        "GSHHS_f_L2.shp",
        "GSHHS_f_L3.shp",
    }
    assert staged_roots
    assert all(not root.exists() for root in staged_roots)
    assert list(os_temporary_root.iterdir()) == []
    assert archive_path.is_file()

    assert set(observed_frames) == {1, 2, 3}
    for level in (1, 2, 3):
        observed = observed_frames[level]
        assert tuple(observed.columns) == EXPECTED_COLUMNS
        assert {column: str(dtype) for column, dtype in observed.dtypes.items()} == (
            EXPECTED_DTYPES
        )
        assert observed.crs is not None
        assert observed.crs.to_epsg() == 4326
        assert observed.geometry.geom_type.tolist() == ["Polygon", "Polygon"]
        assert_geodataframe_equal(
            observed,
            expected_frames[level],
            check_dtype=True,
            check_crs=True,
            normalize=True,
        )
