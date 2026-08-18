"""Inspect or run the separately authorized source-only joint LOSO lane."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

for name in (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ[name] = "1"

from la_heat.multicity.m3_source_joint_nested_loso_v1 import (  # noqa: E402
    AUTHORIZATION_PATH,
    authenticate_m3_source_joint_nested_loso_authorization,
    authenticate_source_nested_loso_completion,
    create_source_nested_loso_completion,
    joint_loso_readiness,
    run_authorized_joint_stage,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--authorization", type=Path, default=AUTHORIZATION_PATH)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--start-joint", action="store_true")
    actions.add_argument("--finalize", action="store_true")
    actions.add_argument("--check-completion", action="store_true")
    args = parser.parse_args()

    if args.start_joint:
        payload = run_authorized_joint_stage(args.project_root, args.authorization)
        mode = "joint_stage"
    elif args.finalize:
        payload = create_source_nested_loso_completion(args.project_root)
        mode = "finalize"
    elif args.check_completion:
        payload = authenticate_source_nested_loso_completion(args.project_root)
        mode = "check_completion"
    else:
        readiness = joint_loso_readiness(args.project_root)
        authorization = None
        if args.authorization.is_absolute():
            authorization_path = args.authorization
        else:
            authorization_path = args.project_root / args.authorization
        if authorization_path.is_file():
            authorization = authenticate_m3_source_joint_nested_loso_authorization(
                args.project_root, args.authorization
            )
        print(
            json.dumps(
                {
                    "mode": "status",
                    "readiness": readiness,
                    "authorization_commit_sha256": (
                        None if authorization is None else authorization["commit_sha256"]
                    ),
                    "start_requested": False,
                    "source_values_read": False,
                    "model_fit_performed": False,
                },
                indent=2,
            )
        )
        return
    print(
        json.dumps(
            {
                "mode": mode,
                "state": payload["state"],
                "commit_sha256": payload["commit_sha256"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
