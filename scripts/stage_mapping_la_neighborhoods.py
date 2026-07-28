"""Download and authenticate the frozen Mapping L.A. neighborhood snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import requests

from la_heat.website_export import MAPPING_LA_SHA256, MAPPING_LA_SOURCE_URL

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    ROOT
    / "data"
    / "raw"
    / "neighborhoods"
    / "mapping-la"
    / "la-county-neighborhoods-v6.geojson"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    output = arguments.output.resolve()

    if output.is_file():
        digest = sha256_file(output)
        if digest != MAPPING_LA_SHA256:
            raise RuntimeError("Existing Mapping L.A. snapshot has the wrong SHA-256.")
        print(json.dumps({"state": "verified-cache", "path": str(output), "sha256": digest}))
        return

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".part")
    digest = hashlib.sha256()
    byte_count = 0
    with requests.get(MAPPING_LA_SOURCE_URL, stream=True, timeout=(30, 180)) as response:
        response.raise_for_status()
        with temporary.open("wb") as handle:
            for chunk in response.iter_content(1024 * 1024):
                if not chunk:
                    continue
                handle.write(chunk)
                digest.update(chunk)
                byte_count += len(chunk)
    if digest.hexdigest() != MAPPING_LA_SHA256:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("Downloaded Mapping L.A. snapshot failed its SHA-256 lock.")
    temporary.replace(output)
    print(
        json.dumps(
            {
                "state": "downloaded",
                "path": str(output),
                "bytes": byte_count,
                "sha256": digest.hexdigest(),
            }
        )
    )


if __name__ == "__main__":
    main()
