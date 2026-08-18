"""Preview, create, or authenticate the M3 Daymet-order repair."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.m3_source_predictor_daymet_order_repair_v1 import (
    AUTHORIZATION_PATH,
    authenticate_daymet_order_repair_acquisition_completion,
    authenticate_daymet_order_repair_completion,
    authenticate_m3_source_predictor_daymet_order_repair_authorization,
    build_m3_source_predictor_daymet_order_repair_authorization,
    create_m3_source_predictor_daymet_order_repair_authorization,
)
from la_heat.multicity.m3_source_predictor_extension_authorization_v1 import (
    DEFAULT_CONFIG,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=AUTHORIZATION_PATH)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--write", action="store_true")
    actions.add_argument("--check-only", action="store_true")
    actions.add_argument("--check-acquisition", action="store_true")
    actions.add_argument("--check-completion", action="store_true")
    args = parser.parse_args()

    if args.check_acquisition:
        mode = "check_acquisition"
        payload = authenticate_daymet_order_repair_acquisition_completion(
            args.project_root, args.output
        )
    elif args.check_completion:
        mode = "check_completion"
        payload = authenticate_daymet_order_repair_completion(args.project_root, args.output)
    elif args.write:
        mode = "write"
        payload = create_m3_source_predictor_daymet_order_repair_authorization(
            args.project_root,
            args.output,
            config_path=args.config,
        )
    elif args.check_only:
        mode = "check"
        payload = authenticate_m3_source_predictor_daymet_order_repair_authorization(
            args.project_root,
            args.output,
            config_path=args.config,
        )
    else:
        mode = "preview"
        payload = build_m3_source_predictor_daymet_order_repair_authorization(
            args.project_root,
            config_path=args.config,
        )

    print(
        json.dumps(
            {
                "mode": mode,
                "state": payload["state"],
                "commit_sha256": payload["commit_sha256"],
                "next_safe_stage": payload.get("next_safe_stage"),
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
