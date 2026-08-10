"""Run the resumable four-city portable Sentinel-2 feature builder.

The launcher installs the already-tested target-sharded compile adapter.  The
adapter is intentionally outside the acquisition pipeline fingerprint: it only
changes the final in-memory compilation scope, so completed acquisition caches
remain valid after this launcher-only repair.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from la_heat.multicity import portable_sentinel_build as engine
from la_heat.provenance import atomic_json, canonical_sha256
from la_heat.sentinel_compile_adapter import (
    COMPILE_ADAPTER_VERSION,
    build_previous_60_day_composites_by_target,
)


def _install_compile_adapter() -> None:
    if getattr(engine, "_target_sharded_compile_adapter_installed", False):
        return
    original_compile_city = engine.compile_city
    engine.build_previous_60_day_composites = (
        build_previous_60_day_composites_by_target
    )

    def compile_city_with_audit(
        project_root: Path,
        context: engine.CityBuildContext,
    ) -> dict[str, Any]:
        manifest = original_compile_city(project_root, context)
        manifest.pop("commit_sha256", None)
        manifest["compile_adapter_version_audit_only"] = COMPILE_ADAPTER_VERSION
        manifest["commit_sha256"] = canonical_sha256(manifest)
        atomic_json(
            manifest,
            context.output_directory / engine.CITY_COMPLETE_FILENAME,
        )
        return manifest

    engine.compile_city = compile_city_with_audit
    engine._target_sharded_compile_adapter_installed = True


def main() -> int:
    _install_compile_adapter()
    return engine.main()

if __name__ == "__main__":
    raise SystemExit(main())
