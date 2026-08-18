"""Preview, create, or authenticate the source joint nested-LOSO permit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.m3_source_joint_nested_loso_v1 import (
    AUTHORIZATION_PATH,
    authenticate_m3_source_joint_nested_loso_authorization,
    build_m3_source_joint_nested_loso_authorization,
    create_m3_source_joint_nested_loso_authorization,
    joint_loso_readiness,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=AUTHORIZATION_PATH)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--write", action="store_true")
    actions.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    if args.write:
        mode = "write"
        payload = create_m3_source_joint_nested_loso_authorization(args.project_root, args.output)
    elif args.check_only:
        mode = "check"
        payload = authenticate_m3_source_joint_nested_loso_authorization(
            args.project_root, args.output
        )
    else:
        readiness = joint_loso_readiness(args.project_root)
        if readiness.get("ready") is not True:
            print(json.dumps({"mode": "readiness", **readiness}, indent=2))
            return
        mode = "preview"
        payload = build_m3_source_joint_nested_loso_authorization(args.project_root)
    print(
        json.dumps(
            {
                "mode": mode,
                "state": payload["state"],
                "source_city_ids": payload["source_city_ids"],
                "joint_configuration_count": len(payload["joint_configuration_ids"]),
                "authorization_read_parquet_count": payload["authorization_audit"][
                    "predictor_or_qa_parquet_opened_or_statted"
                ],
                "commit_sha256": payload["commit_sha256"],
                "next_safe_stage": "run_source_only_joint_stage_after_authentication",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
