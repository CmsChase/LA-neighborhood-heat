"""CLI for the append-only metadata endpoint repair."""

from __future__ import annotations

import argparse
import json

from la_heat.multicity.m3_blind_predictor_metadata_endpoint_repair_v1 import (
    authenticate_authorization,
    build_authorization,
    create_authorization,
    run_repaired_metadata,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preview-authorization", action="store_true")
    group.add_argument("--create-authorization", action="store_true")
    group.add_argument("--check-authorization", action="store_true")
    group.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.preview_authorization:
        payload = build_authorization(args.project_root)
    elif args.create_authorization:
        payload = create_authorization(args.project_root)
    elif args.check_authorization:
        payload = authenticate_authorization(args.project_root)
    else:
        payload = run_repaired_metadata(args.project_root)
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
