"""Preview, create, or authenticate the append-only M3 integrity-v2 permit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.m3_source_development_engine_v2 import (
    authenticate_source_qa_candidates_completion_v2,
)
from la_heat.multicity.m3_source_integrity_v2 import (
    AUTHORIZATION_PATH,
    authenticate_m3_source_integrity_v2_authorization,
    build_m3_source_integrity_v2_authorization,
    create_m3_source_integrity_v2_authorization,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--output", type=Path, default=AUTHORIZATION_PATH)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--write", action="store_true")
    action.add_argument("--check-only", action="store_true")
    action.add_argument("--check-completion", action="store_true")
    args = parser.parse_args()

    if args.check_completion:
        mode = "check_completion"
        payload = authenticate_source_qa_candidates_completion_v2(args.project_root)
    elif args.write:
        mode = "write"
        payload = create_m3_source_integrity_v2_authorization(
            args.project_root, args.output
        )
    elif args.check_only:
        mode = "check"
        payload = authenticate_m3_source_integrity_v2_authorization(
            args.project_root, args.output
        )
    else:
        mode = "preview"
        payload = build_m3_source_integrity_v2_authorization(args.project_root)
    if mode == "check_completion":
        summary = {
            "mode": mode,
            "state": payload["state"],
            "overpasses": payload["overpass_count"],
            "source_cities": payload["source_city_ids"],
            "candidate_ids": payload["candidate_ids"],
            "support_gate": payload["support_gate"],
            "offline_audit": payload["offline_audit"],
            "commit_sha256": payload["commit_sha256"],
            "next_safe_stage": payload["next_safe_stage"],
        }
    else:
        overlay = payload["logical_overlay"]
        summary = {
            "mode": mode,
            "state": payload["state"],
            "overpasses": overlay["overpass_count"],
            "retained_scenes": overlay["scene_count"],
            "reused_content_commits": overlay["content_count"],
            "old_queue_desired_state": payload["original_queue_snapshot"][
                "desired_state"
            ],
            "old_queue_active_leases": payload["original_queue_snapshot"][
                "active_lease_count"
            ],
            "commit_sha256": payload["commit_sha256"],
            "next_safe_stage": payload["next_safe_stage"],
        }
    print(
        json.dumps(
            summary,
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
