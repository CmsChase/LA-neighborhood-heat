"""Model-only runtime fingerprint extensions without changing upstream stages."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path
from typing import Any, Final

from la_heat.provenance import canonical_sha256, code_runtime_fingerprint

MODELING_RUNTIME_PACKAGES: Final = (
    "joblib",
    "scikit-learn",
    "scipy",
    "threadpoolctl",
)


def modeling_runtime_fingerprint(
    *,
    project_root: Path,
    relative_paths: tuple[str, ...],
    algorithm_version: str,
) -> tuple[str, dict[str, Any]]:
    """Extend the common code fingerprint with the numerical model stack."""

    _, common = code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=relative_paths,
        algorithm_version=algorithm_version,
    )
    payload = dict(common)
    packages = dict(payload["packages"])
    for name in MODELING_RUNTIME_PACKAGES:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "MISSING"
    payload["packages"] = packages
    return canonical_sha256(payload), payload
