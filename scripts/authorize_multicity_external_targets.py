"""Issue the one-time three-city target permit after predictions are committed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from la_heat.multicity.external_target_authorization import (
    AUTHORIZATION_PATH,
    create_external_target_authorization,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output", default=str(AUTHORIZATION_PATH))
    args = parser.parse_args()
    payload = create_external_target_authorization(args.project_root, args.output)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
