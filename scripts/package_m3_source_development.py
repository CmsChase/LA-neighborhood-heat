"""Package a safely paused M3 source-development checkpoint for migration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.m3_source_development_migration import create_transfer_folder


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--package-name")
    args = parser.parse_args()
    kwargs = {"package_name": args.package_name}
    if args.output_root is not None:
        kwargs["output_root"] = args.output_root
    result = create_transfer_folder(args.project_root, **kwargs)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
