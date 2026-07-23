from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd
import pytest

import la_heat.returned_result_import as returned_import
from la_heat.model_run_compile import (
    FOLD_METRIC_COLUMNS,
    FOLD_METRICS_FILENAME,
    OOF_PREDICTIONS_FILENAME,
    PER_DATE_METRIC_COLUMNS,
    PER_DATE_METRICS_FILENAME,
    SUMMARY_METRIC_COLUMNS,
    SUMMARY_METRICS_FILENAME,
)
from la_heat.model_task_engine import OUTER_PREDICTION_COLUMNS
from la_heat.provenance import (
    canonical_frame_sha256,
    canonical_sha256,
    parquet_file_record,
    sha256_file,
)

RUN_ID = "a" * 64
CONTEXT_RUN_ID = "b" * 64
TASK_PLAN_SHA = "c" * 64
TRANSFER_ID = "transfer"


def _committed_json(path: Path, payload: dict[str, object]) -> dict[str, object]:
    payload["commit_sha256"] = canonical_sha256(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _csv_lock(path: Path, frame: pd.DataFrame) -> dict[str, object]:
    frame.to_csv(path, index=False)
    return {
        "path": path.name,
        "path_base": "output_directory",
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "rows": len(frame),
    }


def _metric_frame(columns: tuple[str, ...], model_ids: list[str]) -> pd.DataFrame:
    rows = []
    for index, model_id in enumerate(model_ids):
        row: dict[str, object] = {}
        for column in columns:
            if column == "family":
                row[column] = "temporal"
            elif column == "model_id":
                row[column] = model_id
            elif column == "fold_id":
                row[column] = f"fold-{index}"
            elif column == "candidate_id":
                row[column] = f"candidate-{index}"
            elif column == "target_date":
                row[column] = "2024-07-01"
            elif column == "spearman_defined":
                row[column] = True
            else:
                row[column] = 1
        rows.append(row)
    return pd.DataFrame(rows, columns=list(columns))


def _build_returned_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, bytes]]:
    project = tmp_path / "project"
    returned = tmp_path / "returned"
    project.mkdir()
    returned.mkdir()
    (project / "configs").mkdir()
    (project / "configs/research.toml").write_text(
        "[study]\nfinal_test_year = 2025\nunlock_final_test = false\n",
        encoding="utf-8",
    )
    (project / "configs/model_selection.toml").write_text(
        "final_test_year = 2025\nunlock_final_test = false\n",
        encoding="utf-8",
    )
    source_runs = project / "data/interim/model_runs"
    source_runs.mkdir(parents=True)
    (source_runs / "model_tasks.sqlite3").write_bytes(b"source-queue-must-not-change")
    (project / "RUN_DISABLED_TRANSFERRED_OUT.txt").write_text(
        "disabled", encoding="utf-8"
    )

    bundle = returned / "portable_bundle_manifest.json"
    bundle.write_text("{}", encoding="utf-8")
    bundle_sha = sha256_file(bundle)
    _committed_json(
        returned / "portable_relocation.json",
        {"schema_version": 1, "state": "complete"},
    )
    ownership = {
        "schema_version": 1,
        "transfer_id": TRANSFER_ID,
        "state": "transferred_out",
        "bundle_manifest_sha256": bundle_sha,
    }
    (source_runs / "transfer_ownership.json").write_text(
        json.dumps(ownership), encoding="utf-8"
    )
    authority = {
        "schema_version": 1,
        "transfer_id": TRANSFER_ID,
        "state": "target_active",
        "bundle_manifest_sha256": bundle_sha,
    }
    (returned / "transfer_authority.json").write_text(
        json.dumps(authority), encoding="utf-8"
    )

    status = {
        "schema_version": 1,
        "run_id": RUN_ID,
        "state": "complete",
        "phase": "complete",
        "desired_state": "running",
        "total": 3,
        "completed": 3,
        "active": 0,
        "pending": 0,
        "quarantined": 0,
        "counts": {
            "pending": 0,
            "running": 0,
            "complete": 3,
            "quarantined": 0,
            "total": 3,
        },
        "counts_by_kind": {
            "inner_fit": {
                "pending": 0,
                "running": 0,
                "complete": 1,
                "quarantined": 0,
                "total": 1,
            },
            "outer_refit": {
                "pending": 0,
                "running": 0,
                "complete": 2,
                "quarantined": 0,
                "total": 2,
            },
        },
        "active_tasks": [],
        "error": None,
    }
    status_path = returned / "data/interim/model_runs/status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text(json.dumps(status), encoding="utf-8")

    oof = pd.DataFrame(
        {
            "tract_geoid": pd.Series(["x", "x"], dtype="string"),
            "target_date": pd.to_datetime(["2024-07-01", "2024-07-01"]).astype(
                "datetime64[ns]"
            ),
            "spatial_block": ["block", "block"],
            "family": ["temporal", "temporal"],
            "fold_id": ["fold-0", "fold-1"],
            "model_id": ["B0", "B1"],
            "candidate_id": ["candidate-0", "candidate-1"],
            "y_true": [30.0, 30.0],
            "y_pred": [31.0, 29.0],
        },
        columns=list(OUTER_PREDICTION_COLUMNS),
    )
    evaluation = returned / "data/processed/model_evaluation"
    evaluation.mkdir(parents=True)
    oof_path = evaluation / OOF_PREDICTIONS_FILENAME
    oof.to_parquet(oof_path, index=False)
    oof_read = pd.read_parquet(oof_path)
    oof_lock = {
        "path": oof_path.name,
        "path_base": "output_directory",
        **parquet_file_record(oof_path, oof_read),
    }
    summary = _metric_frame(SUMMARY_METRIC_COLUMNS, ["B0", "B1"])
    per_date = _metric_frame(PER_DATE_METRIC_COLUMNS, ["B0", "B1"])
    folds = _metric_frame(FOLD_METRIC_COLUMNS, ["B0", "B1"])
    output_locks = {
        OOF_PREDICTIONS_FILENAME: oof_lock,
        SUMMARY_METRICS_FILENAME: _csv_lock(
            evaluation / SUMMARY_METRICS_FILENAME, summary
        ),
        PER_DATE_METRICS_FILENAME: _csv_lock(
            evaluation / PER_DATE_METRICS_FILENAME, per_date
        ),
        FOLD_METRICS_FILENAME: _csv_lock(
            evaluation / FOLD_METRICS_FILENAME, folds
        ),
    }

    fragment_records = []
    queue_fragments = {}
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    for index, model_id in enumerate(("B0", "B1")):
        task_id = f"outer-{index:064x}"
        frame = oof.iloc[[index]].copy().reset_index(drop=True)
        frame["target_date"] = frame["target_date"].astype("datetime64[ns]")
        fragment_path = scratch / f"{task_id}.parquet"
        frame.to_parquet(fragment_path, index=False)
        record = parquet_file_record(fragment_path, frame)
        compile_record = {
            "task_id": task_id,
            "family": "temporal",
            "fold_id": f"fold-{index}",
            "model_id": model_id,
            "candidate_id": f"candidate-{index}",
            "path": f"outer_fragments/{task_id}.parquet",
            "path_base": "run_directory",
            **record,
        }
        fragment_records.append(compile_record)
        queue_fragments[task_id] = {
            "path": compile_record["path"],
            "path_base": "run_directory",
            "sha256": record["sha256"],
            "bytes": record["bytes"],
            "rows": record["rows"],
            "schema_sha256": record["schema_sha256"],
            "semantic_sha256": canonical_frame_sha256(
                frame, sort_by=["target_date", "tract_geoid"]
            ),
        }

    provenance: dict[str, object] = {
        "schema_version": 2,
        "algorithm_version": "grouped-model-oof-compile-v2",
        "state": "complete",
        "ready_for_reporting": True,
        "run_id": RUN_ID,
        "context_run_id": CONTEXT_RUN_ID,
        "task_plan_sha256": TASK_PLAN_SHA,
        "final_test_year": 2025,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "context_row_count": 1,
        "independent_date_count": 1,
        "family_count": 1,
        "model_count": 2,
        "outer_fragment_count": 2,
        "oof_prediction_row_count": 2,
        "summary_metric_row_count": 2,
        "per_date_metric_row_count": 2,
        "fold_metric_row_count": 2,
        "input_fragments": fragment_records,
        "output_files": output_locks,
    }
    _committed_json(evaluation / "model_run_compile_provenance.json", provenance)

    relocation = json.loads(
        (returned / "portable_relocation.json").read_text(encoding="utf-8")
    )
    run_root = returned / "data/interim/model_runs/runs" / RUN_ID
    run_root.mkdir(parents=True)
    (run_root / "outer_fragments").mkdir()
    _committed_json(
        run_root / "run_manifest.json",
        {
            "schema_version": 1,
            "run_id": RUN_ID,
            "context_run_id": CONTEXT_RUN_ID,
            "task_plan_sha256": TASK_PLAN_SHA,
            "portable_relocation_commit_sha256": relocation["commit_sha256"],
            "inner_task_count": 1,
            "outer_task_count": 2,
            "total_task_count": 3,
            "final_test_year": 2025,
            "final_test_unlocked": False,
        },
    )
    _committed_json(
        run_root / "outer_selections.json",
        {
            "schema_version": 1,
            "run_id": RUN_ID,
            "selection_count": 2,
            "selections": [{"task": 0}, {"task": 1}],
        },
    )

    queue_path = returned / "data/interim/model_runs/model_tasks.sqlite3"
    connection = sqlite3.connect(queue_path)
    connection.execute(
        "CREATE TABLE model_runs (run_id TEXT PRIMARY KEY, desired_state TEXT)"
    )
    connection.execute(
        "CREATE TABLE model_run_tasks (run_id TEXT, task_id TEXT, kind TEXT, "
        "status TEXT, result_json TEXT)"
    )
    connection.execute("INSERT INTO model_runs VALUES (?, ?)", (RUN_ID, "running"))
    connection.execute(
        "INSERT INTO model_run_tasks VALUES (?, ?, ?, ?, ?)",
        (RUN_ID, "inner", "inner_fit", "complete", "{}"),
    )
    for index, model_id in enumerate(("B0", "B1")):
        task_id = f"outer-{index:064x}"
        result = {
            "schema_version": 2,
            "kind": "outer_refit",
            "model_id": model_id,
            "selected_candidate_id": f"candidate-{index}",
            "fragment": queue_fragments[task_id],
        }
        connection.execute(
            "INSERT INTO model_run_tasks VALUES (?, ?, ?, ?, ?)",
            (RUN_ID, task_id, "outer_refit", "complete", json.dumps(result)),
        )
    connection.commit()
    connection.close()

    protected = {
        "queue": (source_runs / "model_tasks.sqlite3").read_bytes(),
        "ownership": (source_runs / "transfer_ownership.json").read_bytes(),
        "marker": (project / "RUN_DISABLED_TRANSFERRED_OUT.txt").read_bytes(),
    }
    return project, returned, protected


def _small_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "EXPECTED_RUN_TASK_COUNT": 3,
        "EXPECTED_INNER_TASK_COUNT": 1,
        "EXPECTED_OUTER_TASK_COUNT": 2,
        "EXPECTED_CONTEXT_ROW_COUNT": 1,
        "EXPECTED_INDEPENDENT_DATE_COUNT": 1,
        "EXPECTED_FAMILY_COUNT": 1,
        "EXPECTED_MODEL_COUNT": 2,
        "EXPECTED_OOF_ROW_COUNT": 2,
        "EXPECTED_SUMMARY_ROW_COUNT": 2,
        "EXPECTED_PER_DATE_ROW_COUNT": 2,
        "EXPECTED_FOLD_METRIC_ROW_COUNT": 2,
    }
    for name, value in values.items():
        monkeypatch.setattr(returned_import, name, value)


def _write_exact_returned_fragments(returned: Path) -> list[Path]:
    evaluation = returned / "data/processed/model_evaluation"
    provenance = json.loads(
        (evaluation / "model_run_compile_provenance.json").read_text(
            encoding="utf-8"
        )
    )
    oof = pd.read_parquet(evaluation / OOF_PREDICTIONS_FILENAME)
    paths = []
    for record in provenance["input_fragments"]:
        frame = oof.loc[
            (oof["family"] == record["family"])
            & (oof["fold_id"] == record["fold_id"])
            & (oof["model_id"] == record["model_id"]),
            list(OUTER_PREDICTION_COLUMNS),
        ].reset_index(drop=True)
        frame["target_date"] = pd.to_datetime(frame["target_date"]).astype(
            "datetime64[ns]"
        )
        path = (
            returned
            / "data/interim/model_runs/runs"
            / RUN_ID
            / record["path"]
        )
        returned_import.atomic_parquet(frame, path)
        assert parquet_file_record(path, frame)["sha256"] == record["sha256"]
        paths.append(path)
    return paths


def _zip_returned_fixture(
    tmp_path: Path,
    *,
    omit_fragment: bool = False,
) -> tuple[Path, Path, dict[str, bytes], str]:
    project, returned, protected = _build_returned_fixture(tmp_path)
    fragment_paths = _write_exact_returned_fragments(returned)
    relocation = returned / "portable_relocation.json"
    bundle_payload = {
        "schema_version": 1,
        "relocation_manifest": "portable_relocation.json",
        "immutable_files": [
            {
                "path": "portable_relocation.json",
                "bytes": relocation.stat().st_size,
                "sha256": sha256_file(relocation),
            }
        ],
    }
    bundle = returned / "portable_bundle_manifest.json"
    bundle.write_text(json.dumps(bundle_payload), encoding="utf-8")
    bundle_sha = sha256_file(bundle)
    for path in (
        project / "data/interim/model_runs/transfer_ownership.json",
        returned / "transfer_authority.json",
    ):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["bundle_manifest_sha256"] = bundle_sha
        path.write_text(json.dumps(payload), encoding="utf-8")
    protected["ownership"] = (
        project / "data/interim/model_runs/transfer_ownership.json"
    ).read_bytes()

    archive_path = tmp_path / "returned.zip"
    omitted = fragment_paths[0] if omit_fragment else None
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(item for item in returned.rglob("*") if item.is_file()):
            if path == omitted:
                continue
            relative = path.relative_to(returned).as_posix()
            archive.write(path, f"ISEF_MODEL_RUNNER_8845H/{relative}")
    return project, archive_path, protected, sha256_file(archive_path)


def test_reconstructs_exact_fragments_and_imports_without_touching_source_guards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _small_contract(monkeypatch)
    project, returned, protected = _build_returned_fixture(tmp_path)

    summary = returned_import.verify_and_import_returned_results(returned, project)

    assert summary.state == "imported"
    assert summary.reconstructed_fragment_count == 2
    assert summary.verified_existing_fragment_count == 0
    run = project / "data/interim/model_runs/runs" / RUN_ID
    assert len(list((run / "outer_fragments").glob("*.parquet"))) == 2
    assert len(list((project / "data/processed/model_evaluation").iterdir())) == 5
    snapshot = project / "data/interim/model_runs/returned_snapshots" / RUN_ID
    assert (snapshot / "model_tasks.sqlite3").is_file()
    audit = json.loads(
        (snapshot / "returned_result_import_audit.json").read_text(encoding="utf-8")
    )
    commit = audit.pop("commit_sha256")
    assert canonical_sha256(audit) == commit == summary.audit_commit_sha256
    assert (project / "data/interim/model_runs/model_tasks.sqlite3").read_bytes() == protected[
        "queue"
    ]
    assert (
        project / "data/interim/model_runs/transfer_ownership.json"
    ).read_bytes() == protected["ownership"]
    assert (project / "RUN_DISABLED_TRANSFERRED_OUT.txt").read_bytes() == protected[
        "marker"
    ]
    assert "unlock_final_test = false" in (
        project / "configs/research.toml"
    ).read_text(encoding="utf-8")

    with pytest.raises(FileExistsError, match="refuses to overwrite"):
        returned_import.verify_and_import_returned_results(returned, project)


def test_existing_fragment_corruption_is_not_silently_reconstructed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _small_contract(monkeypatch)
    project, returned, _ = _build_returned_fixture(tmp_path)
    provenance = json.loads(
        (
            returned
            / "data/processed/model_evaluation/model_run_compile_provenance.json"
        ).read_text(encoding="utf-8")
    )
    record = provenance["input_fragments"][0]
    fragment = returned / "data/interim/model_runs/runs" / RUN_ID / record["path"]
    fragment.write_bytes(b"corrupt")

    with pytest.raises(returned_import.ReturnedResultImportError, match="byte lock"):
        returned_import.verify_and_import_returned_results(
            returned, project, verify_only=True
        )


def test_zip_requires_original_fragments_and_imports_without_reconstruction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _small_contract(monkeypatch)
    project, archive, protected, archive_sha = _zip_returned_fixture(tmp_path)

    verified = returned_import.verify_and_import_returned_results(
        archive,
        project,
        verify_only=True,
        expected_archive_sha256=archive_sha,
    )

    assert verified.verified_existing_fragment_count == 2
    assert verified.reconstructed_fragment_count == 0
    assert not (project / "data/processed/model_evaluation").exists()

    imported = returned_import.verify_and_import_returned_results(
        archive,
        project,
        expected_archive_sha256=archive_sha,
    )

    assert imported.state == "imported"
    assert imported.verified_existing_fragment_count == 2
    assert imported.reconstructed_fragment_count == 0
    snapshot = project / "data/interim/model_runs/returned_snapshots" / RUN_ID
    audit = json.loads(
        (snapshot / "returned_result_import_audit.json").read_text(
            encoding="utf-8"
        )
    )
    assert audit["returned_source"] == {
        "kind": "zip",
        "path": str(archive.resolve()),
        "sha256": archive_sha,
        "bytes": archive.stat().st_size,
        "crc_check": "ok",
        "member_count": 14,
        "uncompressed_bytes": audit["returned_source"]["uncompressed_bytes"],
        "compressed_bytes": audit["returned_source"]["compressed_bytes"],
        "archive_root": "ISEF_MODEL_RUNNER_8845H",
        "required_files_extracted": 14,
        "portable_immutable_files": {
            "file_count": 1,
            "total_bytes": (
                tmp_path / "returned/portable_relocation.json"
            ).stat().st_size,
        },
        "result_manifest_present": False,
        "result_manifest_files": None,
    }
    assert sha256_file(snapshot / "model_tasks.sqlite3") == audit["returned_queue"][
        "sha256"
    ]
    assert (project / "data/interim/model_runs/model_tasks.sqlite3").read_bytes() == (
        protected["queue"]
    )
    assert (
        project / "data/interim/model_runs/transfer_ownership.json"
    ).read_bytes() == protected["ownership"]
    assert (project / "RUN_DISABLED_TRANSFERRED_OUT.txt").read_bytes() == protected[
        "marker"
    ]


def test_zip_rejects_wrong_sha_and_missing_original_fragment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _small_contract(monkeypatch)
    project, archive, _, archive_sha = _zip_returned_fixture(
        tmp_path, omit_fragment=True
    )

    with pytest.raises(
        returned_import.ReturnedResultImportError,
        match="external SHA-256 mismatch",
    ):
        returned_import.verify_and_import_returned_results(
            archive,
            project,
            verify_only=True,
            expected_archive_sha256="0" * 64,
        )
    with pytest.raises(
        returned_import.ReturnedResultImportError,
        match="lacks required result files",
    ):
        returned_import.verify_and_import_returned_results(
            archive,
            project,
            verify_only=True,
            expected_archive_sha256=archive_sha,
        )


def test_zip_inventory_rejects_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("root/good.txt", "good")
        archive.writestr("root/../escape.txt", "bad")

    with ZipFile(archive_path) as archive, pytest.raises(
        returned_import.ReturnedResultImportError,
        match="unsafe member name",
    ):
        returned_import._inventory_zip(archive)


def test_publication_rolls_back_if_a_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staged = {name: tmp_path / "staged" / name for name in ("evaluation", "run", "snapshot")}
    destinations = {
        name: tmp_path / "published" / name
        for name in ("evaluation", "run", "snapshot")
    }
    for name, path in staged.items():
        path.mkdir(parents=True)
        (path / f"{name}.txt").write_text(name, encoding="utf-8")
    for path in destinations.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    expected = {
        name: returned_import._tree_record(path) for name, path in staged.items()
    }
    original_replace = Path.replace
    forward_calls = 0
    destination_strings = {str(path) for path in destinations.values()}

    def flaky_replace(self: Path, target: str | Path) -> Path:
        nonlocal forward_calls
        if str(target) in destination_strings:
            forward_calls += 1
            if forward_calls == 2:
                raise OSError("injected publication failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    with pytest.raises(OSError, match="injected publication failure"):
        returned_import._publish_staged_directories(
            staged, destinations, expected
        )

    assert all(path.is_dir() for path in staged.values())
    assert all(not path.exists() for path in destinations.values())
