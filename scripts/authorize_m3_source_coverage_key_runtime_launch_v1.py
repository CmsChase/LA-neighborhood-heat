"""Preview, create, or authenticate the M3 repair runtime-launch amendment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.m3_source_coverage_key_runtime_launch_v1 import (
    AUTHORIZATION_PATH,
    authenticate_m3_source_coverage_key_runtime_launch_authorization,
    build_m3_source_coverage_key_runtime_launch_authorization,
    create_m3_source_coverage_key_runtime_launch_authorization,
)
from la_heat.multicity.m3_source_development_engine_coverage_key_runtime_launch_v1 import (
    authenticate_source_qa_candidates_completion_coverage_key_runtime_launch_v1,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--output", type=Path, default=AUTHORIZATION_PATH)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--write", action="store_true")
    actions.add_argument("--check-only", action="store_true")
    actions.add_argument("--check-completion", action="store_true")
    args = parser.parse_args()

    if args.check_completion:
        payload = authenticate_source_qa_candidates_completion_coverage_key_runtime_launch_v1(
            args.project_root,
            launch_authorization_path=args.output,
        )
        summary = {
            "mode": "check_completion",
            "state": payload["state"],
            "overpasses": payload["overpass_count"],
            "source_cities": payload["source_city_ids"],
            "candidate_ids": payload["candidate_ids"],
            "support_gate": payload["support_gate"],
            "offline_audit": payload["offline_audit"],
            "coverage_key_runtime_launch_authorization_commit_sha256": payload[
                "coverage_key_runtime_launch_authorization_commit_sha256"
            ],
            "commit_sha256": payload["commit_sha256"],
            "next_safe_stage": payload["next_safe_stage"],
        }
    else:
        if args.write:
            mode = "write"
            payload = create_m3_source_coverage_key_runtime_launch_authorization(
                args.project_root, args.output
            )
        elif args.check_only:
            mode = "check"
            payload = authenticate_m3_source_coverage_key_runtime_launch_authorization(
                args.project_root, args.output
            )
        else:
            mode = "preview"
            payload = build_m3_source_coverage_key_runtime_launch_authorization(args.project_root)
        transition = payload["incident_evidence"]
        snapshot = payload["paused_semantic_queue_snapshot"]
        summary = {
            "mode": mode,
            "state": payload["state"],
            "run_id": payload["v2_run_id"],
            "queue_counts": snapshot["counts"],
            "active_leases": snapshot["active_lease_count"],
            "database_hash_transition": {
                "before": transition["before_prepare_database_record"]["sha256"],
                "after": transition["after_prepare_database_record"]["sha256"],
                "evidence_only": transition["database_hash_transition_is_evidence_only"],
            },
            "semantic_snapshot_exactly_unchanged": transition[
                "semantic_snapshot_exactly_unchanged"
            ],
            "commit_sha256": payload["commit_sha256"],
            "next_safe_stage": payload["next_safe_stage"],
        }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
