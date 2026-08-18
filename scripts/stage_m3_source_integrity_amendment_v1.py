"""Preview, create, or authenticate the M3 source-integrity amendment/overlay."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from la_heat.multicity.m3_source_asset_repair_v1 import INCIDENT_PATH
from la_heat.multicity.m3_source_integrity_amendment_v1 import (
    AMENDMENT_PATH,
    OVERLAY_PATH,
    authenticate_source_integrity_availability_amendment,
    authenticate_source_integrity_logical_overlay,
    build_source_integrity_availability_amendment,
    build_source_integrity_logical_overlay,
    create_source_integrity_availability_amendment,
    create_source_integrity_logical_overlay,
)


def _summary(mode: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "mode": mode,
        "state": payload.get("state"),
        "commit_sha256": payload.get("commit_sha256"),
        "logical_totals": payload.get(
            "logical_totals", payload.get("required_logical_totals")
        ),
        "next_safe_stage": payload.get("next_safe_stage"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--incident-path", type=Path, default=INCIDENT_PATH)
    parser.add_argument("--amendment-path", type=Path, default=AMENDMENT_PATH)
    parser.add_argument("--overlay-path", type=Path, default=OVERLAY_PATH)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--write-amendment", action="store_true")
    action.add_argument("--check-amendment", action="store_true")
    action.add_argument("--preview-overlay", action="store_true")
    action.add_argument("--write-overlay", action="store_true")
    action.add_argument("--check-overlay", action="store_true")
    args = parser.parse_args()

    root = args.project_root.resolve()
    if args.write_amendment:
        mode = "write_amendment"
        payload = create_source_integrity_availability_amendment(
            root,
            args.amendment_path,
            incident_path=args.incident_path,
            overlay_path=args.overlay_path,
        )
    elif args.check_amendment:
        mode = "check_amendment"
        payload = authenticate_source_integrity_availability_amendment(
            root, args.amendment_path
        )
    elif args.preview_overlay:
        mode = "preview_overlay"
        payload = build_source_integrity_logical_overlay(
            root, amendment_path=args.amendment_path
        )
    elif args.write_overlay:
        mode = "write_overlay"
        payload = create_source_integrity_logical_overlay(
            root,
            args.overlay_path,
            amendment_path=args.amendment_path,
        )
    elif args.check_overlay:
        mode = "check_overlay"
        payload = authenticate_source_integrity_logical_overlay(
            root,
            args.overlay_path,
            amendment_path=args.amendment_path,
        )
    else:
        mode = "preview_amendment"
        payload = build_source_integrity_availability_amendment(
            root,
            incident_path=args.incident_path,
            overlay_path=args.overlay_path,
        )
    print(json.dumps(_summary(mode, payload), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
