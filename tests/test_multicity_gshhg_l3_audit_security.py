from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Mapping
from pathlib import Path

import pytest

from la_heat.multicity.gshhg_l3_hierarchy_audit import (
    AUTHORIZED_MEMBERS,
    GshhgL3HierarchyAuditError,
    StructuralAuditError,
    _central_directory_audit,
    _hash_archive,
    _publish_new_bytes,
    _publish_or_authenticate_bytes,
    _stream_authorized_members,
    run_numerical_phase,
)


def _payloads(*, include_decoy: bool = False) -> dict[str, bytes]:
    payloads = {name: f"synthetic payload for {name}\n".encode() for name in AUTHORIZED_MEMBERS}
    if include_decoy:
        payloads["GSHHS_shp/f/GSHHS_f_L4.shp"] = b"must never be opened"
    return payloads


def _write_zip(path: Path, payloads: Mapping[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in payloads.items():
            archive.writestr(name, content)


def _central_contract(
    archive: zipfile.ZipFile,
) -> tuple[int, int, str]:
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
        for member in archive.infolist()
    ]
    inventory_bytes = json.dumps(
        inventory,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return (
        len(inventory),
        sum(member.file_size for member in archive.infolist()),
        hashlib.sha256(inventory_bytes).hexdigest(),
    )


def _audit_central(
    archive: zipfile.ZipFile,
) -> tuple[dict[str, zipfile.ZipInfo], dict[str, object]]:
    member_count, uncompressed_bytes, inventory_sha256 = _central_contract(archive)
    return _central_directory_audit(
        archive,
        expected_member_count=member_count,
        expected_uncompressed_bytes=uncompressed_bytes,
        expected_inventory_sha256=inventory_sha256,
    )


def _inherited_hashes(payloads: Mapping[str, bytes]) -> dict[str, str]:
    return {
        name: hashlib.sha256(payloads[name]).hexdigest()
        for name in AUTHORIZED_MEMBERS
        if "_L1." in name or "_L2." in name
    }


def test_stream_opens_only_twelve_allowlisted_members_and_never_calls_testzip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads = _payloads(include_decoy=True)
    archive_path = tmp_path / "synthetic.zip"
    _write_zip(archive_path, payloads)

    opened: list[str] = []
    original_open = zipfile.ZipFile.open

    def guarded_open(
        archive: zipfile.ZipFile,
        name: str | zipfile.ZipInfo,
        mode: str = "r",
        pwd: bytes | None = None,
        *,
        force_zip64: bool = False,
    ) -> object:
        member_name = name.filename if isinstance(name, zipfile.ZipInfo) else name
        if mode == "r":
            assert member_name in AUTHORIZED_MEMBERS
            opened.append(member_name)
        return original_open(
            archive,
            name,
            mode=mode,
            pwd=pwd,
            force_zip64=force_zip64,
        )

    def forbidden_testzip(_archive: zipfile.ZipFile) -> None:
        raise AssertionError("ZipFile.testzip() must never be called by the L3 audit.")

    monkeypatch.setattr(zipfile.ZipFile, "open", guarded_open)
    monkeypatch.setattr(zipfile.ZipFile, "testzip", forbidden_testzip)

    with zipfile.ZipFile(archive_path, "r") as archive:
        file_members, central = _audit_central(archive)
        layer_paths, records, open_log = _stream_authorized_members(
            archive,
            file_members,
            staging_root=tmp_path / "staging",
            inherited_hashes=_inherited_hashes(payloads),
            callback=None,
        )

    assert central["file_member_count"] == len(AUTHORIZED_MEMBERS) + 1
    assert opened == list(AUTHORIZED_MEMBERS)
    assert open_log == list(AUTHORIZED_MEMBERS)
    assert set(records) == set(AUTHORIZED_MEMBERS)
    assert set(layer_paths) == {1, 2, 3}
    assert "GSHHS_shp/f/GSHHS_f_L4.shp" not in opened
    for name, record in records.items():
        assert record["sha256"] == hashlib.sha256(payloads[name]).hexdigest()
        assert record["crc32"] == f"{zipfile.crc32(payloads[name]) & 0xFFFFFFFF:08x}"


def test_synthetic_archive_hashes_exact_bytes(tmp_path: Path) -> None:
    archive_path = tmp_path / "synthetic.zip"
    _write_zip(archive_path, _payloads())
    raw = archive_path.read_bytes()

    sha256, md5, observed_bytes = _hash_archive(archive_path, callback=None)

    assert observed_bytes == len(raw)
    assert sha256 == hashlib.sha256(raw).hexdigest()
    assert md5 == hashlib.md5(raw, usedforsecurity=False).hexdigest()


def test_authorized_member_crc_corruption_fails_closed(tmp_path: Path) -> None:
    payloads = _payloads()
    archive_path = tmp_path / "synthetic.zip"
    _write_zip(archive_path, payloads)
    corrupt_name = AUTHORIZED_MEMBERS[0]

    class CorruptingArchive:
        def __init__(self, archive: zipfile.ZipFile) -> None:
            self.archive = archive

        def open(
            self,
            info: zipfile.ZipInfo,
            mode: str = "r",
        ) -> object:
            assert mode == "r"
            assert info.filename in AUTHORIZED_MEMBERS
            if info.filename == corrupt_name:
                original = payloads[corrupt_name]
                return io.BytesIO(bytes([original[0] ^ 1]) + original[1:])
            return self.archive.open(info, mode)

    with zipfile.ZipFile(archive_path, "r") as archive:
        file_members, _ = _audit_central(archive)
        with pytest.raises(
            StructuralAuditError,
            match="authorized_member_stream_and_crc",
        ):
            _stream_authorized_members(
                CorruptingArchive(archive),  # type: ignore[arg-type]
                file_members,
                staging_root=tmp_path / "staging",
                inherited_hashes=_inherited_hashes(payloads),
                callback=None,
            )


def test_inherited_l1_l2_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    payloads = _payloads()
    archive_path = tmp_path / "synthetic.zip"
    _write_zip(archive_path, payloads)
    inherited = _inherited_hashes(payloads)
    inherited[AUTHORIZED_MEMBERS[0]] = "0" * 64

    with zipfile.ZipFile(archive_path, "r") as archive:
        file_members, _ = _audit_central(archive)
        with pytest.raises(
            StructuralAuditError,
            match="inherited_l1_l2_member_sha256",
        ):
            _stream_authorized_members(
                archive,
                file_members,
                staging_root=tmp_path / "staging",
                inherited_hashes=inherited,
                callback=None,
            )


@pytest.mark.parametrize("mutation", ["missing", "case_changed"])
def test_exact_authorized_member_presence_and_case_are_required(
    tmp_path: Path,
    mutation: str,
) -> None:
    payloads = _payloads()
    original = AUTHORIZED_MEMBERS[-1]
    content = payloads.pop(original)
    if mutation == "case_changed":
        payloads[original[:-3] + original[-3:].upper()] = content
    archive_path = tmp_path / f"{mutation}.zip"
    _write_zip(archive_path, payloads)

    with zipfile.ZipFile(archive_path, "r") as archive:
        with pytest.raises(
            StructuralAuditError,
            match="authorized_member_presence_and_case",
        ):
            _audit_central(archive)


def test_casefold_member_collision_fails_closed(tmp_path: Path) -> None:
    payloads = _payloads()
    collision = AUTHORIZED_MEMBERS[0].upper()
    assert collision not in payloads
    payloads[collision] = b"casefold collision"
    archive_path = tmp_path / "casefold-collision.zip"
    _write_zip(archive_path, payloads)

    with zipfile.ZipFile(archive_path, "r") as archive:
        with pytest.raises(
            StructuralAuditError,
            match="archive_member_casefold_names_unique",
        ):
            _audit_central(archive)


def test_append_only_publication_never_clobbers_existing_bytes(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "terminal.json"
    _publish_new_bytes(b"first", destination)

    with pytest.raises(GshhgL3HierarchyAuditError, match="already exists"):
        _publish_new_bytes(b"first", destination)
    with pytest.raises(GshhgL3HierarchyAuditError, match="already exists"):
        _publish_new_bytes(b"second", destination)

    assert destination.read_bytes() == b"first"


def test_idempotent_table_publication_accepts_only_identical_bytes(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "diagnostic.csv"
    _publish_or_authenticate_bytes(b"exact\n", destination)
    _publish_or_authenticate_bytes(b"exact\n", destination)

    with pytest.raises(GshhgL3HierarchyAuditError, match="different bytes"):
        _publish_or_authenticate_bytes(b"changed\n", destination)

    assert destination.read_bytes() == b"exact\n"


def test_phase_two_rejects_every_non_structural_bundle_before_computation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_probe_derivation(_bundle: object) -> None:
        raise AssertionError("Phase 2 computation started without a structural bundle.")

    monkeypatch.setattr(
        "la_heat.multicity.gshhg_l3_hierarchy_audit._derive_probes",
        forbidden_probe_derivation,
    )

    with pytest.raises(
        TypeError,
        match="completed StructuralAuditBundle",
    ):
        run_numerical_phase(object(), config={})  # type: ignore[arg-type]
