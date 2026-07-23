"""Create a consistent, audited SQLite backup for portable model-run transfer."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def _queue_audit(connection: sqlite3.Connection, *, require_paused: bool) -> dict[str, object]:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    if not {"model_runs", "model_run_tasks"} <= tables:
        raise RuntimeError("Queue database lacks the required durable-run tables.")
    integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
    if integrity != "ok":
        raise RuntimeError("Queue database failed PRAGMA quick_check.")
    runs = [
        {"run_id": str(row[0]), "desired_state": str(row[1])}
        for row in connection.execute(
            "SELECT run_id, desired_state FROM model_runs ORDER BY run_id"
        )
    ]
    if not runs:
        raise RuntimeError("Queue database contains no initialized model run.")
    if require_paused and any(row["desired_state"] != "paused" for row in runs):
        raise RuntimeError("Every run must be paused before creating a transfer snapshot.")
    counts = {
        str(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT status, COUNT(*) FROM model_run_tasks GROUP BY status"
        )
    }
    if counts.get("running", 0) != 0:
        raise RuntimeError("Queue still has leased/running tasks; wait for safe drain first.")
    return {
        "runs": runs,
        "counts": {
            name: counts.get(name, 0)
            for name in ("pending", "running", "complete", "quarantined")
        },
        "total": sum(counts.values()),
        "quick_check": integrity,
    }


def create_snapshot(
    source: str | Path,
    destination: str | Path,
    *,
    require_paused: bool = True,
) -> dict[str, object]:
    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    if source_path == destination_path:
        raise ValueError("Source and destination queue paths must differ.")
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if destination_path.exists():
        raise FileExistsError(destination_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(source_path)
    destination_connection: sqlite3.Connection | None = None
    try:
        before = _queue_audit(source_connection, require_paused=require_paused)
        destination_connection = sqlite3.connect(destination_path)
        source_connection.backup(destination_connection)
        destination_connection.commit()
        after = _queue_audit(destination_connection, require_paused=require_paused)
        if before != after:
            raise RuntimeError("Queue backup audit disagrees with its source snapshot.")
    except Exception:
        if destination_connection is not None:
            destination_connection.close()
            destination_connection = None
        destination_path.unlink(missing_ok=True)
        raise
    finally:
        source_connection.close()
        if destination_connection is not None:
            destination_connection.close()
    return {
        "source": str(source_path),
        "destination": str(destination_path),
        **before,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument(
        "--allow-nonpaused-terminal",
        action="store_true",
        help="Permit a complete terminal queue whose desired state remains running.",
    )
    arguments = parser.parse_args()
    audit = create_snapshot(
        arguments.source,
        arguments.destination,
        require_paused=not arguments.allow_nonpaused_terminal,
    )
    print(json.dumps(audit, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
