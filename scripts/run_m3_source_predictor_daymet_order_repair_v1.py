"""Inspect or resume the existing M3 predictor queue with the Daymet repair."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

# This is the low-load process boundary.  Set every native pool before any
# la_heat import can transitively load NumPy, GDAL, or a BLAS runtime.
for _thread_env_name in (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "GDAL_NUM_THREADS",
):
    os.environ[_thread_env_name] = "1"

from la_heat.model_run_queue import ModelRunQueue
from la_heat.multicity.m3_source_predictor_daymet_order_repair_v1 import (
    AUTHORIZATION_PATH,
    execute_daymet_order_repair_worker,
    load_m3_source_predictor_daymet_order_repair_runtime_permit,
)
from la_heat.multicity.m3_source_predictor_extension_authorization_v1 import (
    DEFAULT_CONFIG,
    load_m3_source_predictor_extension_runtime_permit,
    load_predictor_extension_settings,
)
from la_heat.multicity.m3_source_predictor_extension_runtime_v1 import (
    PHASES,
    source_predictor_run_id,
    source_predictor_runtime_status,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--phase", choices=PHASES)
    parser.add_argument("--start", action="store_true")
    args = parser.parse_args()
    if args.start and args.phase is None:
        parser.error("--start requires --phase")

    if args.start:
        result = execute_daymet_order_repair_worker(
            args.project_root,
            phase=args.phase,
            authorization_path=AUTHORIZATION_PATH,
            config_path=args.config,
        )
    else:
        settings = load_predictor_extension_settings(args.project_root, args.config)
        repair = load_m3_source_predictor_daymet_order_repair_runtime_permit(
            settings.root,
            AUTHORIZATION_PATH,
            config_path=settings.config_path,
            require_paused=False,
        )
        parent = load_m3_source_predictor_extension_runtime_permit(
            settings.root, settings.authorization, settings.config_path
        )
        queue = ModelRunQueue(settings.database)
        result = {
            **source_predictor_runtime_status(
                queue,
                source_predictor_run_id(parent),
                settings=settings,
                phase=args.phase,
            ),
            "daymet_order_repair_authorization_commit_sha256": repair[
                "commit_sha256"
            ],
        }
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
