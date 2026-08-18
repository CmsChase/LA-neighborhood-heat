"""Preview, create, or authenticate the M3 coverage-key repair permit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.m3_source_coverage_key_repair_v1 import (
    AUTHORIZATION_PATH,
    authenticate_m3_source_coverage_key_repair_authorization,
    build_m3_source_coverage_key_repair_authorization,
    create_m3_source_coverage_key_repair_authorization,
)
from la_heat.multicity.m3_source_development_engine_coverage_key_repair_v1 import (
    authenticate_source_qa_candidates_completion_coverage_key_repair_v1,
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
        mode = "check_completion"
        payload = authenticate_source_qa_candidates_completion_coverage_key_repair_v1(
            args.project_root,
            repair_authorization_path=args.output,
        )
        summary = {
            "mode": mode,
            "state": payload["state"],
            "overpasses": payload["overpass_count"],
            "source_cities": payload["source_city_ids"],
            "candidate_ids": payload["candidate_ids"],
            "support_gate": payload["support_gate"],
            "offline_audit": payload["offline_audit"],
            "coverage_key_repair_authorization_commit_sha256": payload[
                "coverage_key_repair_authorization_commit_sha256"
            ],
            "commit_sha256": payload["commit_sha256"],
            "next_safe_stage": payload["next_safe_stage"],
        }
    else:
        if args.write:
            mode = "write"
            payload = create_m3_source_coverage_key_repair_authorization(
                args.project_root, args.output
            )
        elif args.check_only:
            mode = "check"
            payload = authenticate_m3_source_coverage_key_repair_authorization(
                args.project_root, args.output
            )
        else:
            mode = "preview"
            payload = build_m3_source_coverage_key_repair_authorization(args.project_root)
        snapshot = payload["initial_paused_queue_snapshot"]
        mismatch = payload["incident_evidence"]["coverage_key_mismatch"]
        summary = {
            "mode": mode,
            "state": payload["state"],
            "run_id": payload["v2_run_id"],
            "queue_counts": snapshot["counts"],
            "active_leases": snapshot["active_lease_count"],
            "first_qa_error_type": snapshot["first_qa_task"]["error_type"],
            "coverage_key_rename": {
                "from": mismatch["producer_key"],
                "to": mismatch["consumer_key"],
            },
            "commit_sha256": payload["commit_sha256"],
            "next_safe_stage": payload["next_safe_stage"],
        }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
