"""Verify/import a returned Sentinel ZIP or copied work directory."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from la_heat.multicity.portable_sentinel_directory_return import (
    finalize_resumed_portable_sentinel_directory_return,
    reauthenticate_canonical_portable_sentinel_completion,
    verify_and_import_portable_sentinel_directory,
)
from la_heat.multicity.portable_sentinel_return import (
    verify_and_import_portable_sentinel_results,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--archive", type=Path, help="Returned ZIP package.")
    source.add_argument(
        "--source-directory",
        type=Path,
        help="Copied GAMING_LAPTOP_SENTINEL or extracted result folder.",
    )
    parser.add_argument(
        "--checksum",
        type=Path,
        help="Defaults to the .zip.sha256 file beside the ZIP.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument(
        "--resume-dashboard",
        action="store_true",
        help="After a partial directory import, open the existing resume dashboard.",
    )
    parser.add_argument(
        "--audit-if-complete",
        action="store_true",
        help="Run predictor readiness only when the authenticated return is complete.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.source_directory is not None:
        if args.checksum is not None:
            raise SystemExit("--checksum applies only to --archive.")
        summary = verify_and_import_portable_sentinel_directory(
            args.source_directory,
            args.project_root,
            verify_only=args.verify_only,
        )
    else:
        summary = verify_and_import_portable_sentinel_results(
            args.archive,
            args.project_root,
            checksum_path=args.checksum,
            verify_only=args.verify_only,
        )
    print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False))
    scientifically_complete = (
        True
        if args.source_directory is None
        else summary.scientifically_complete
    )
    resumed_from_dashboard = False
    dashboard_code = 0
    if (
        args.resume_dashboard
        and not args.verify_only
        and args.source_directory is not None
        and not scientifically_complete
    ):
        resumed_from_dashboard = True
        print(
            "Resume-ready, not scientifically complete. Opening the Sentinel dashboard."
        )
        command = [
            sys.executable,
            str(args.project_root.resolve() / "scripts/run_portable_sentinel_dashboard.py"),
        ]
        dashboard_code = subprocess.run(
            command, cwd=args.project_root.resolve(), check=False
        ).returncode
        canonical = reauthenticate_canonical_portable_sentinel_completion(
            args.project_root
        )
        print(
            json.dumps(
                {"post_resume_validation": canonical.to_dict()},
                indent=2,
                ensure_ascii=False,
            )
        )
        scientifically_complete = canonical.scientifically_complete
        if scientifically_complete:
            receipt = finalize_resumed_portable_sentinel_directory_return(
                args.project_root,
                completion=canonical,
            )
            print(f"Formal return receipt: {receipt}")
    if (
        (args.audit_if_complete or resumed_from_dashboard)
        and not args.verify_only
        and scientifically_complete
    ):
        audit_command = [
            sys.executable,
            str(args.project_root.resolve() / "scripts/audit_multicity_predictor_readiness.py"),
            "--project-root",
            str(args.project_root.resolve()),
            "--write-report",
        ]
        audit_code = subprocess.run(
            audit_command, cwd=args.project_root.resolve(), check=False
        ).returncode
        if audit_code:
            return audit_code
    if not scientifically_complete:
        print("Resume-ready, not scientifically complete; predictor readiness was not run.")
        return dashboard_code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
