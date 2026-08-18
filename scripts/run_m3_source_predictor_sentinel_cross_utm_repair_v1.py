"""Authorize or run the same predictor queue with the Sentinel UTM repair."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

for _name in (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "GDAL_NUM_THREADS",
):
    os.environ[_name] = "1"

from la_heat.multicity.m3_source_predictor_extension_runtime_v1 import PHASES
from la_heat.multicity.m3_source_predictor_sentinel_cross_utm_repair_v1 import (
    authenticate_authorization,
    build_authorization,
    create_authorization,
    execute_repaired_worker,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--phase", choices=PHASES)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--write-authorization", action="store_true")
    actions.add_argument("--check-authorization", action="store_true")
    actions.add_argument("--start", action="store_true")
    args = parser.parse_args()
    if args.start and args.phase is None:
        parser.error("--start requires --phase")
    if args.write_authorization:
        mode, payload = "write_authorization", create_authorization(args.project_root)
    elif args.check_authorization:
        mode, payload = "check_authorization", authenticate_authorization(args.project_root)
    elif args.start:
        mode, payload = "start", execute_repaired_worker(args.project_root, phase=args.phase)
    else:
        mode, payload = "preview", build_authorization(args.project_root)
    print(
        json.dumps(
            {
                "mode": mode,
                "state": payload["state"],
                "commit_sha256": payload.get("commit_sha256"),
                "counts": payload.get("counts"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
