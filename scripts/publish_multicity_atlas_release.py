"""Publish or authenticate the verified multi-city Atlas result overlay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.atlas_release import (
    ATLAS_OUTPUT_PATH,
    RELEASE_MANIFEST_PATH,
    publish_atlas_release,
)
from la_heat.multicity.external_evaluation import OUTPUT_DIRECTORY
from la_heat.multicity.external_evaluation_reporting import (
    OUTPUT_DIRECTORY as REPORT_DIRECTORY,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--evaluation-output", type=Path, default=OUTPUT_DIRECTORY)
    parser.add_argument("--evaluation-report", type=Path, default=REPORT_DIRECTORY)
    parser.add_argument("--atlas-output", type=Path, default=ATLAS_OUTPUT_PATH)
    parser.add_argument("--release-manifest", type=Path, default=RELEASE_MANIFEST_PATH)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    payload = publish_atlas_release(
        args.project_root,
        evaluation_output_directory=args.evaluation_output,
        evaluation_report_directory=args.evaluation_report,
        atlas_output_path=args.atlas_output,
        release_manifest_path=args.release_manifest,
        check_only=args.check_only,
    )
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
