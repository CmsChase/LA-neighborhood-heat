"""Initialize, inspect, or run one M3 source predictor extension phase."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

# This runner is the low-load process boundary.  Force the native thread pools
# before importing any la_heat module that can transitively import NumPy/GDAL.
for _thread_env_name in (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "GDAL_NUM_THREADS",
):
    os.environ[_thread_env_name] = "1"

from la_heat.model_run_queue import ModelRunQueue
from la_heat.multicity.m3_source_predictor_extension_authorization_v1 import (
    DEFAULT_CONFIG,
    load_m3_source_predictor_extension_runtime_permit,
    load_predictor_extension_settings,
)
from la_heat.multicity.m3_source_predictor_extension_runtime_v1 import (
    PHASES,
    initialize_source_predictor_runtime,
    source_predictor_run_id,
    source_predictor_runtime_status,
)
from la_heat.multicity.m3_source_predictor_extension_worker_v1 import (
    execute_source_predictor_worker,
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
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--start", action="store_true")
    args = parser.parse_args()
    if args.start and args.phase is None:
        parser.error("--start requires --phase")

    if args.start:
        result = execute_source_predictor_worker(
            args.project_root,
            phase=args.phase,
            config_path=args.config,
        )
    elif args.initialize:
        result = initialize_source_predictor_runtime(args.project_root, config_path=args.config)
    else:
        settings = load_predictor_extension_settings(args.project_root, args.config)
        permit = load_m3_source_predictor_extension_runtime_permit(
            settings.root, settings.authorization, settings.config_path
        )
        queue = ModelRunQueue(settings.database)
        result = source_predictor_runtime_status(
            queue,
            source_predictor_run_id(permit),
            settings=settings,
            phase=args.phase,
        )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
