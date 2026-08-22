"""Authorize, run, resume, or check the blind-predictor support stage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.m3_blind_predictor_support_v1 import (
    authenticate_m3_blind_predictor_support_completion,
    authenticate_m3_blind_predictor_support_runtime_authorization,
    build_m3_blind_predictor_support_runtime_authorization,
    create_m3_blind_predictor_support_runtime_authorization,
    run_m3_blind_predictor_support,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--preview-authorization", action="store_true")
    actions.add_argument("--write-authorization", action="store_true")
    actions.add_argument("--check-authorization", action="store_true")
    actions.add_argument("--run", action="store_true")
    actions.add_argument("--check-completion", action="store_true")
    args = parser.parse_args()
    if args.preview_authorization:
        mode = "preview_authorization"
        payload = build_m3_blind_predictor_support_runtime_authorization(args.project_root)
    elif args.write_authorization:
        mode = "write_authorization"
        payload = create_m3_blind_predictor_support_runtime_authorization(args.project_root)
    elif args.check_authorization:
        mode = "check_authorization"
        payload = authenticate_m3_blind_predictor_support_runtime_authorization(
            args.project_root
        )
    elif args.run:
        mode = "run"
        payload = run_m3_blind_predictor_support(args.project_root)
    else:
        mode = "check_completion"
        payload = authenticate_m3_blind_predictor_support_completion(args.project_root)
    print(
        json.dumps(
            {
                "mode": mode,
                "state": payload["state"],
                "commit_sha256": payload["commit_sha256"],
                "next_safe_stage": payload.get("next_safe_stage"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
