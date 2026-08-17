"""Append-only CLI for the narrow M3 source-asset repair v1 contract."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from la_heat.multicity.m3_source_asset_repair_v1 import (
    AUTHORIZATION_PATH,
    COMPLETION_PATH,
    INCIDENT_PATH,
    authenticate_source_asset_repair_authorization,
    authenticate_source_asset_repair_completion,
    authenticate_source_asset_repair_incident,
    build_source_asset_repair_authorization,
    build_source_asset_repair_incident,
    create_source_asset_repair_authorization,
    create_source_asset_repair_incident,
    run_source_asset_repair,
)


def _summary(mode: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        "mode": mode,
        "state": payload.get("state"),
        "commit_sha256": payload.get("commit_sha256"),
        "next_safe_stage": payload.get("next_safe_stage"),
    }
    for key in (
        "affected_asset_count",
        "repaired_asset_count",
        "source_asset_repair_incident_commit_sha256",
        "repair_authorization_commit_sha256",
    ):
        if key in payload:
            result[key] = payload[key]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--incident-path", type=Path, default=INCIDENT_PATH)
    parser.add_argument("--authorization-path", type=Path, default=AUTHORIZATION_PATH)
    parser.add_argument("--completion-path", type=Path, default=COMPLETION_PATH)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--write-incident", action="store_true")
    action.add_argument("--check-incident", action="store_true")
    action.add_argument("--preview-authorization", action="store_true")
    action.add_argument("--write-authorization", action="store_true")
    action.add_argument("--check-authorization", action="store_true")
    action.add_argument("--repair-official-directory", type=Path)
    action.add_argument("--repair-planetary-computer-restored", action="store_true")
    action.add_argument("--check-completion", action="store_true")
    args = parser.parse_args()

    root = args.project_root.resolve()
    if args.write_incident:
        mode = "write_incident"
        payload = create_source_asset_repair_incident(root, args.incident_path)
    elif args.check_incident:
        mode = "check_incident"
        payload = authenticate_source_asset_repair_incident(root, args.incident_path)
    elif args.preview_authorization:
        mode = "preview_authorization"
        payload = build_source_asset_repair_authorization(
            root,
            incident_path=args.incident_path,
            completion_path=args.completion_path,
        )
    elif args.write_authorization:
        mode = "write_authorization"
        payload = create_source_asset_repair_authorization(
            root,
            args.authorization_path,
            incident_path=args.incident_path,
            completion_path=args.completion_path,
        )
    elif args.check_authorization:
        mode = "check_authorization"
        payload = authenticate_source_asset_repair_authorization(
            root,
            args.authorization_path,
        )
    elif args.repair_official_directory is not None:
        mode = "repair_official_directory"
        payload = run_source_asset_repair(
            root,
            source_mode="official_original_directory",
            source_directory=args.repair_official_directory,
            authorization_path=args.authorization_path,
            completion_path=args.completion_path,
        )
    elif args.repair_planetary_computer_restored:
        mode = "repair_planetary_computer_restored"
        payload = run_source_asset_repair(
            root,
            source_mode="planetary_computer_restored",
            authorization_path=args.authorization_path,
            completion_path=args.completion_path,
        )
    elif args.check_completion:
        mode = "check_completion"
        payload = authenticate_source_asset_repair_completion(
            root,
            args.completion_path,
            authorization_path=args.authorization_path,
        )
    else:
        mode = "preview_incident"
        payload = build_source_asset_repair_incident(root)
    print(json.dumps(_summary(mode, payload), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
