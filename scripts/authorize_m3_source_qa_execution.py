from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.m3_source_qa_authorization import (
    AUTHORIZATION_PATH,
    authenticate_m3_source_qa_authorization,
    create_m3_source_qa_authorization,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Authorize the frozen source-cache and offline-QA phases."
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=AUTHORIZATION_PATH)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if args.check_only:
        payload = authenticate_m3_source_qa_authorization(args.project_root, args.output)
    else:
        payload = create_m3_source_qa_authorization(args.project_root, args.output)
    print(
        json.dumps(
            {
                "state": payload["state"],
                "source_cities": payload["source_city_ids"],
                "overpasses": payload["expected_overpass_count"],
                "scenes": payload["expected_unique_city_scene_count"],
                "download_workers_allowed": payload["runtime_contract"][
                    "download_workers_allowed"
                ],
                "commit_sha256": payload["commit_sha256"],
                "next_safe_stage": payload["next_safe_stage"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
