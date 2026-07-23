"""Authenticated block-level residual spatial diagnostics for joint M2 and B1.

The analysis is development-OOF-only.  It aggregates residuals to complete
date-by-5-km-block cells before computing Moran's I, and it reads tract
geometry only for fixed grouping validation and diagnostic maps.
"""

from __future__ import annotations

import json
import math
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import geopandas as gpd
import matplotlib
import numpy as np
import pandas as pd
import shapely
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize, TwoSlopeNorm
from matplotlib.figure import Figure

from la_heat.model_result_analysis import (
    authenticate_model_results,
    load_result_analysis_config,
    select_strongest_legal_baseline,
)
from la_heat.provenance import (
    atomic_csv,
    atomic_json,
    canonical_sha256,
    code_runtime_fingerprint,
    sha256_file,
)
from la_heat.validation_splits import (
    read_fixed_tracts,
    validate_row_group_tract_lock,
)

DIAGNOSTIC_SCHEMA_VERSION: Final = 1
DIAGNOSTIC_ALGORITHM_VERSION: Final = "residual-spatial-diagnostics-v1"
DIAGNOSTIC_STATE: Final = "frozen_development_oof_diagnostics"
DEFAULT_DIAGNOSTIC_CONFIG: Final = Path(
    "configs/residual_spatial_diagnostics.toml"
)

DATE_BLOCK_FILENAME: Final = "joint_m2_b1_date_block_residuals.csv"
DATE_MORAN_FILENAME: Final = "joint_m2_b1_morans_i_by_date.csv"
MORAN_SUMMARY_FILENAME: Final = "joint_m2_b1_morans_i_summary.csv"
TRACT_SUMMARY_FILENAME: Final = "joint_m2_b1_tract_residual_summary.csv"
MAP_FILENAME: Final = "joint_m2_b1_residual_diagnostics_map.png"
PROVENANCE_FILENAME: Final = "residual_spatial_diagnostics_provenance.json"

_FROZEN_LOCKS: Final = {
    "result_analysis_config_sha256": (
        "08bb91d00f1da1b89e5c0e43af63ac184e8521b8f2fa63db9544974823dd8fd8"
    ),
    "compile_provenance_file_sha256": (
        "55416c987a2d25f9177dd974307ae595761c1db31ecaa9d7561fb6c42d4d5e7c"
    ),
    "compile_provenance_commit_sha256": (
        "00d1e59ce78baffbf9d24361ac8ba16a1d577b22cb193e15596627259190ed07"
    ),
    "oof_predictions_sha256": (
        "0c06b88fd09cd514745b699e6b6a7e12901db8d3247e42b582feaf1f7e91c04a"
    ),
    "target_manifest_sha256": (
        "11f4fe862570895441036964a9b308e92c2ee6ba8a87b5da9009ffd5208a4bda"
    ),
    "tract_manifest_sha256": (
        "fbdcd5b6e3a6d5f55d972a1f19178b453d753e3f9b3878c8074c1972eb0f68bd"
    ),
    "tract_manifest_semantic_sha256": (
        "73fde5df7372404a0b4700981b612c0ded5a2c3d5edaa3fc88d4fa2626c440b0"
    ),
    "split_provenance_file_sha256": (
        "8b03a143c11e1df85f7916627011f95b6c4d33edea7b81e530abbd2a0801498f"
    ),
    "split_provenance_commit_sha256": (
        "4a2a0bee89a87bae9c98e35a480606171d570ccaa743355fa0d3639d68fee213"
    ),
    "split_promotion_file_sha256": (
        "055cda0b265c98e7fccad08c1d5772132c101ad342230c7ae609fcd6f818fcdb"
    ),
    "split_promotion_commit_sha256": (
        "6a72169db012cf8c12aeecde573275e23205363608e60d4cde616a681fa08fcc"
    ),
}

_PIPELINE_PATHS: Final = (
    "configs/residual_spatial_diagnostics.toml",
    "scripts/analyze_residual_spatial.py",
    "src/la_heat/model_result_analysis.py",
    "src/la_heat/provenance.py",
    "src/la_heat/residual_spatial_diagnostics.py",
    "src/la_heat/validation_splits.py",
)


class ResidualSpatialDiagnosticError(ValueError):
    """Raised when a residual spatial diagnostic contract is violated."""


@dataclass(frozen=True, slots=True)
class ResidualSpatialConfig:
    """Validated frozen configuration for the spatial residual diagnostics."""

    path: Path
    semantic_sha256: str
    result_analysis_config: Path
    evaluation_directory: Path
    target_manifest: Path
    tract_manifest: Path
    split_provenance: Path
    split_promotion: Path
    table_output_directory: Path
    figure_output_directory: Path
    family: str
    target_model_id: str
    baseline_model_id: str
    residual_definition: str
    final_test_year: int
    development_years: tuple[int, ...]
    expected_tract_date_rows_per_model: int
    expected_independent_dates: int
    expected_spatial_blocks: int
    expected_tracts: int
    expected_date_block_cells_per_model: int
    analysis_crs: str
    block_size_km: float
    block_id_pattern: str
    adjacency: str
    weights: str
    date_block_aggregation: str
    permutation_seed: int
    permutations: int
    permutation_p_value: str
    exploratory_alpha: float
    locks: dict[str, str]


@dataclass(frozen=True, slots=True)
class AuthenticatedSpatialInputs:
    """Authenticated selected OOF rows, target rows, tract geometry, and locks."""

    predictions: pd.DataFrame
    targets: pd.DataFrame
    tracts: gpd.GeoDataFrame
    input_authentication: dict[str, Any]


@dataclass(frozen=True, slots=True)
class MoranResult:
    """One binary-rook Moran's I result."""

    observation_count: int
    rook_edge_count: int
    morans_i: float
    randomization_expectation: float
    permutation_count: int
    permutation_p_value_two_sided: float


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _exact_keys(payload: Mapping[str, Any], expected: set[str], *, label: str) -> None:
    if set(payload) != expected:
        raise ResidualSpatialDiagnosticError(
            f"{label} keys must be exactly {sorted(expected)}."
        )


def _integer(value: object, *, label: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ResidualSpatialDiagnosticError(
            f"{label} must be an integer >= {minimum}."
        )
    return value


def _finite_number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResidualSpatialDiagnosticError(f"{label} must be numeric.")
    result = float(value)
    if not math.isfinite(result):
        raise ResidualSpatialDiagnosticError(f"{label} must be finite.")
    return result


def _sha256(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ResidualSpatialDiagnosticError(
            f"{label} must be a lowercase SHA-256 digest."
        )
    return value


def _resolved_project_path(value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ResidualSpatialDiagnosticError(f"{label} must be a path string.")
    path = Path(value)
    return (path if path.is_absolute() else _project_root() / path).resolve()


def load_residual_spatial_config(
    path: str | Path = DEFAULT_DIAGNOSTIC_CONFIG,
) -> ResidualSpatialConfig:
    """Load and fail closed on any drift from the frozen diagnostic design."""

    config_path = Path(path).resolve()
    try:
        with config_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ResidualSpatialDiagnosticError(
            f"Cannot read residual-spatial configuration: {config_path}"
        ) from error
    _exact_keys(
        raw,
        {"schema_version", "algorithm_version", "state", "paths", "analysis", "spatial", "locks"},
        label="diagnostic configuration",
    )
    if (
        raw["schema_version"] != DIAGNOSTIC_SCHEMA_VERSION
        or raw["algorithm_version"] != DIAGNOSTIC_ALGORITHM_VERSION
        or raw["state"] != DIAGNOSTIC_STATE
    ):
        raise ResidualSpatialDiagnosticError(
            "Residual-spatial configuration header drifted."
        )
    paths = raw["paths"]
    analysis = raw["analysis"]
    spatial = raw["spatial"]
    locks = raw["locks"]
    if not all(isinstance(section, dict) for section in (paths, analysis, spatial, locks)):
        raise ResidualSpatialDiagnosticError("Configuration sections must be TOML tables.")
    _exact_keys(
        paths,
        {
            "result_analysis_config",
            "evaluation_directory",
            "target_manifest",
            "tract_manifest",
            "split_provenance",
            "split_promotion",
            "table_output_directory",
            "figure_output_directory",
        },
        label="paths",
    )
    _exact_keys(
        analysis,
        {
            "family",
            "target_model_id",
            "baseline_model_id",
            "residual_definition",
            "final_test_year",
            "final_test_locked",
            "development_years",
            "expected_tract_date_rows_per_model",
            "expected_independent_dates",
            "expected_spatial_blocks",
            "expected_tracts",
            "expected_date_block_cells_per_model",
        },
        label="analysis",
    )
    _exact_keys(
        spatial,
        {
            "analysis_crs",
            "block_size_km",
            "block_id_pattern",
            "adjacency",
            "weights",
            "date_block_aggregation",
            "permutation_seed",
            "permutations",
            "permutation_p_value",
            "exploratory_alpha",
        },
        label="spatial",
    )
    _exact_keys(locks, set(_FROZEN_LOCKS), label="locks")
    parsed_locks = {
        name: _sha256(value, label=f"locks.{name}") for name, value in locks.items()
    }
    if parsed_locks != _FROZEN_LOCKS:
        raise ResidualSpatialDiagnosticError("Frozen input hashes drifted.")

    development_years = analysis["development_years"]
    if development_years != [2020, 2021, 2022, 2023, 2024]:
        raise ResidualSpatialDiagnosticError("Development years must remain 2020-2024.")
    expected_counts = {
        "rows": _integer(
            analysis["expected_tract_date_rows_per_model"],
            label="expected_tract_date_rows_per_model",
        ),
        "dates": _integer(
            analysis["expected_independent_dates"],
            label="expected_independent_dates",
        ),
        "blocks": _integer(
            analysis["expected_spatial_blocks"],
            label="expected_spatial_blocks",
        ),
        "tracts": _integer(
            analysis["expected_tracts"], label="expected_tracts"
        ),
        "cells": _integer(
            analysis["expected_date_block_cells_per_model"],
            label="expected_date_block_cells_per_model",
        ),
    }
    if expected_counts != {
        "rows": 63_403,
        "dates": 65,
        "blocks": 71,
        "tracts": 1_096,
        "cells": 4_202,
    }:
        raise ResidualSpatialDiagnosticError("Frozen diagnostic cardinalities drifted.")
    final_test_year = _integer(analysis["final_test_year"], label="final_test_year")
    if final_test_year != 2025 or analysis["final_test_locked"] is not True:
        raise ResidualSpatialDiagnosticError("The 2025 final test must remain locked.")
    if (
        analysis["family"] != "joint"
        or analysis["target_model_id"] != "M2"
        or analysis["baseline_model_id"] != "B1"
        or analysis["residual_definition"] != "y_pred_minus_y_true"
    ):
        raise ResidualSpatialDiagnosticError(
            "Diagnostics must remain the joint M2/B1 residual comparison."
        )
    block_size_km = _finite_number(spatial["block_size_km"], label="block_size_km")
    permutations = _integer(spatial["permutations"], label="permutations")
    permutation_seed = _integer(
        spatial["permutation_seed"], label="permutation_seed", minimum=0
    )
    alpha = _finite_number(spatial["exploratory_alpha"], label="exploratory_alpha")
    expected_spatial = {
        "analysis_crs": "EPSG:3310",
        "block_id_pattern": r"^x([+-]\d{4})_y([+-]\d{4})$",
        "adjacency": "rook_on_parsed_fixed_grid",
        "weights": "binary_symmetric",
        "date_block_aggregation": "unweighted_mean_residual_across_tracts",
        "permutation_p_value": "two_sided_centered_on_randomization_expectation",
    }
    if any(spatial[key] != value for key, value in expected_spatial.items()):
        raise ResidualSpatialDiagnosticError("Frozen spatial method drifted.")
    if block_size_km != 5.0 or permutation_seed != 20_260_722 or permutations != 999:
        raise ResidualSpatialDiagnosticError("Frozen grid or permutation design drifted.")
    if alpha != 0.05:
        raise ResidualSpatialDiagnosticError("Exploratory alpha must remain 0.05.")

    return ResidualSpatialConfig(
        path=config_path,
        semantic_sha256=canonical_sha256(raw),
        result_analysis_config=_resolved_project_path(
            paths["result_analysis_config"], label="paths.result_analysis_config"
        ),
        evaluation_directory=_resolved_project_path(
            paths["evaluation_directory"], label="paths.evaluation_directory"
        ),
        target_manifest=_resolved_project_path(
            paths["target_manifest"], label="paths.target_manifest"
        ),
        tract_manifest=_resolved_project_path(
            paths["tract_manifest"], label="paths.tract_manifest"
        ),
        split_provenance=_resolved_project_path(
            paths["split_provenance"], label="paths.split_provenance"
        ),
        split_promotion=_resolved_project_path(
            paths["split_promotion"], label="paths.split_promotion"
        ),
        table_output_directory=_resolved_project_path(
            paths["table_output_directory"], label="paths.table_output_directory"
        ),
        figure_output_directory=_resolved_project_path(
            paths["figure_output_directory"], label="paths.figure_output_directory"
        ),
        family="joint",
        target_model_id="M2",
        baseline_model_id="B1",
        residual_definition="y_pred_minus_y_true",
        final_test_year=final_test_year,
        development_years=tuple(development_years),
        expected_tract_date_rows_per_model=expected_counts["rows"],
        expected_independent_dates=expected_counts["dates"],
        expected_spatial_blocks=expected_counts["blocks"],
        expected_tracts=expected_counts["tracts"],
        expected_date_block_cells_per_model=expected_counts["cells"],
        analysis_crs="EPSG:3310",
        block_size_km=block_size_km,
        block_id_pattern=str(spatial["block_id_pattern"]),
        adjacency=str(spatial["adjacency"]),
        weights=str(spatial["weights"]),
        date_block_aggregation=str(spatial["date_block_aggregation"]),
        permutation_seed=permutation_seed,
        permutations=permutations,
        permutation_p_value=str(spatial["permutation_p_value"]),
        exploratory_alpha=alpha,
        locks=parsed_locks,
    )


def _read_committed_json(
    path: Path,
    *,
    expected_file_sha256: str,
    expected_commit_sha256: str,
    label: str,
) -> dict[str, Any]:
    if not path.is_file() or sha256_file(path) != expected_file_sha256:
        raise ResidualSpatialDiagnosticError(f"{label} file lock failed.")
    before = sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ResidualSpatialDiagnosticError(f"Cannot read {label}.") from error
    if not isinstance(payload, dict) or sha256_file(path) != before:
        raise ResidualSpatialDiagnosticError(f"{label} changed while being read.")
    working = dict(payload)
    commit = working.pop("commit_sha256", None)
    if commit != expected_commit_sha256 or canonical_sha256(working) != commit:
        raise ResidualSpatialDiagnosticError(f"{label} canonical commit failed.")
    return payload


def parse_spatial_block(
    block_id: object,
    *,
    pattern: str = r"^x([+-]\d{4})_y([+-]\d{4})$",
) -> tuple[int, int]:
    """Parse one canonical fixed-grid block ID into integer x/y indices."""

    if not isinstance(block_id, str):
        raise ResidualSpatialDiagnosticError("spatial_block must be a string.")
    match = re.fullmatch(pattern, block_id)
    if match is None:
        raise ResidualSpatialDiagnosticError(
            f"Invalid fixed-grid spatial_block: {block_id!r}."
        )
    x, y = (int(value) for value in match.groups())
    if block_id != f"x{x:+05d}_y{y:+05d}":
        raise ResidualSpatialDiagnosticError(
            f"Noncanonical fixed-grid spatial_block: {block_id!r}."
        )
    return x, y


def rook_adjacency_edges(
    block_ids: Sequence[str],
    *,
    pattern: str = r"^x([+-]\d{4})_y([+-]\d{4})$",
) -> tuple[tuple[str, str], ...]:
    """Return unique undirected rook edges among the supplied fixed-grid blocks."""

    blocks = tuple(sorted(str(value) for value in block_ids))
    if not blocks or len(set(blocks)) != len(blocks):
        raise ResidualSpatialDiagnosticError(
            "Rook adjacency requires unique nonempty spatial blocks."
        )
    coordinate_to_block: dict[tuple[int, int], str] = {}
    for block in blocks:
        coordinate = parse_spatial_block(block, pattern=pattern)
        if coordinate in coordinate_to_block:
            raise ResidualSpatialDiagnosticError(
                "Multiple block IDs map to the same fixed-grid coordinate."
            )
        coordinate_to_block[coordinate] = block
    edges: list[tuple[str, str]] = []
    for (x, y), block in sorted(coordinate_to_block.items()):
        for neighbor_coordinate in ((x + 1, y), (x, y + 1)):
            neighbor = coordinate_to_block.get(neighbor_coordinate)
            if neighbor is not None:
                edges.append((block, neighbor))
    return tuple(edges)


def morans_i_rook(
    values_by_block: pd.Series,
    *,
    permutations: int = 0,
    seed: int = 0,
    block_id_pattern: str = r"^x([+-]\d{4})_y([+-]\d{4})$",
) -> MoranResult:
    """Compute binary symmetric rook Moran's I on one block-level value vector."""

    if not isinstance(values_by_block, pd.Series):
        raise ResidualSpatialDiagnosticError("Moran input must be a pandas Series.")
    if values_by_block.index.has_duplicates or len(values_by_block) < 3:
        raise ResidualSpatialDiagnosticError(
            "Moran input needs at least three uniquely indexed blocks."
        )
    if isinstance(permutations, bool) or permutations < 0:
        raise ResidualSpatialDiagnosticError("permutations must be nonnegative.")
    ordered = values_by_block.copy()
    ordered.index = ordered.index.map(str)
    ordered = ordered.sort_index(kind="stable")
    numeric = pd.to_numeric(ordered, errors="raise").to_numpy(dtype=float)
    if not np.isfinite(numeric).all():
        raise ResidualSpatialDiagnosticError("Moran input contains non-finite values.")
    edges = rook_adjacency_edges(
        ordered.index.tolist(), pattern=block_id_pattern
    )
    if not edges:
        raise ResidualSpatialDiagnosticError("Moran input has no rook-neighbor edges.")
    positions = {block: index for index, block in enumerate(ordered.index)}
    left = np.fromiter((positions[a] for a, _ in edges), dtype=np.int64)
    right = np.fromiter((positions[b] for _, b in edges), dtype=np.int64)
    centered = numeric - numeric.mean()
    denominator = float(np.dot(centered, centered))
    if not math.isfinite(denominator) or denominator <= 0:
        raise ResidualSpatialDiagnosticError("Moran input has zero spatial variance.")
    n = len(centered)
    edge_count = len(edges)
    scale = n / (2.0 * edge_count)
    observed = float(
        scale
        * (2.0 * np.sum(centered[left] * centered[right], dtype=float))
        / denominator
    )
    expectation = -1.0 / (n - 1)
    p_value = math.nan
    if permutations:
        rng = np.random.default_rng(seed)
        statistics = np.empty(permutations, dtype=float)
        batch_size = 256
        for start in range(0, permutations, batch_size):
            stop = min(start + batch_size, permutations)
            permuted = np.stack(
                [rng.permutation(centered) for _ in range(stop - start)]
            )
            numerators = 2.0 * np.sum(
                permuted[:, left] * permuted[:, right], axis=1, dtype=float
            )
            statistics[start:stop] = scale * numerators / denominator
        observed_distance = abs(observed - expectation)
        p_value = float(
            (1 + np.count_nonzero(np.abs(statistics - expectation) >= observed_distance))
            / (permutations + 1)
        )
    return MoranResult(
        observation_count=n,
        rook_edge_count=edge_count,
        morans_i=observed,
        randomization_expectation=expectation,
        permutation_count=permutations,
        permutation_p_value_two_sided=p_value,
    )


def _fixed_tract_semantic_sha256(tracts: gpd.GeoDataFrame) -> str:
    records = [
        {
            "tract_geoid": row.GEOID,
            "spatial_block": row.spatial_block,
            "geometry_wkb": shapely.to_wkb(
                shapely.normalize(row.geometry)
            ).hex(),
        }
        for row in tracts.sort_values("GEOID", kind="stable").itertuples(index=False)
    ]
    return canonical_sha256({"crs": tracts.crs.to_string(), "tracts": records})


def _validate_target_frame(
    frame: pd.DataFrame,
    config: ResidualSpatialConfig,
) -> pd.DataFrame:
    required = {
        "tract_geoid",
        "target_date",
        "spatial_block",
        "target_lst_c",
        "target_available",
        "date_usable",
        "tract_manifest_sha256",
    }
    if set(frame.columns) != required:
        raise ResidualSpatialDiagnosticError("Target manifest columns are not exact.")
    result = frame.copy()
    result["target_date"] = pd.to_datetime(result["target_date"], errors="raise")
    if result["target_date"].dt.tz is not None:
        raise ResidualSpatialDiagnosticError("Target dates must be timezone-naive.")
    if not result["target_date"].dt.normalize().equals(result["target_date"]):
        raise ResidualSpatialDiagnosticError("Target dates must be civil midnights.")
    if (result["target_date"].dt.year >= config.final_test_year).any():
        raise PermissionError(
            f"Locked final-test year {config.final_test_year} or later is present."
        )
    observed_years = tuple(sorted(int(value) for value in result["target_date"].dt.year.unique()))
    if observed_years != config.development_years:
        raise ResidualSpatialDiagnosticError("Target development years drifted.")
    if (
        len(result) != config.expected_tract_date_rows_per_model
        or result["target_date"].nunique() != config.expected_independent_dates
        or result["spatial_block"].nunique() != config.expected_spatial_blocks
        or result["tract_geoid"].nunique() != config.expected_tracts
        or result.duplicated(["tract_geoid", "target_date"]).any()
    ):
        raise ResidualSpatialDiagnosticError("Target manifest cardinalities drifted.")
    if not result["target_available"].eq(True).all() or not result["date_usable"].eq(True).all():
        raise ResidualSpatialDiagnosticError(
            "Model-ready target rows must all be target-available and date-usable."
        )
    numeric = pd.to_numeric(result["target_lst_c"], errors="raise")
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise ResidualSpatialDiagnosticError("Target LST contains non-finite values.")
    result["target_lst_c"] = numeric
    for block in result["spatial_block"].unique():
        parse_spatial_block(block, pattern=config.block_id_pattern)
    return result.sort_values(
        ["target_date", "tract_geoid"], kind="stable"
    ).reset_index(drop=True)


def _validate_selected_predictions(
    predictions: pd.DataFrame,
    targets: pd.DataFrame,
    config: ResidualSpatialConfig,
) -> pd.DataFrame:
    models = (config.target_model_id, config.baseline_model_id)
    result = predictions.loc[
        predictions["family"].eq(config.family)
        & predictions["model_id"].isin(models)
    ].copy()
    result["target_date"] = pd.to_datetime(result["target_date"], errors="raise")
    if (result["target_date"].dt.year >= config.final_test_year).any():
        raise PermissionError("Locked 2025-or-later rows appeared in selected OOF.")
    if len(result) != config.expected_tract_date_rows_per_model * len(models):
        raise ResidualSpatialDiagnosticError("Selected joint M2/B1 OOF rows are incomplete.")
    target_keys = targets.loc[
        :, ["tract_geoid", "target_date", "spatial_block", "target_lst_c"]
    ]
    for model_id in models:
        model = result.loc[result["model_id"].eq(model_id)].copy()
        if (
            len(model) != config.expected_tract_date_rows_per_model
            or model["target_date"].nunique() != config.expected_independent_dates
            or model["spatial_block"].nunique() != config.expected_spatial_blocks
            or model["tract_geoid"].nunique() != config.expected_tracts
            or model.duplicated(["tract_geoid", "target_date"]).any()
        ):
            raise ResidualSpatialDiagnosticError(
                f"Selected OOF cardinalities drifted for {model_id}."
            )
        joined = model.merge(
            target_keys,
            on=["tract_geoid", "target_date"],
            how="outer",
            validate="one_to_one",
            indicator=True,
            suffixes=("_oof", "_target"),
        )
        if not joined["_merge"].eq("both").all():
            raise ResidualSpatialDiagnosticError(
                f"OOF/target key lock failed for {model_id}."
            )
        if not joined["spatial_block_oof"].astype(str).equals(
            joined["spatial_block_target"].astype(str)
        ):
            raise ResidualSpatialDiagnosticError(
                f"OOF/target spatial-block lock failed for {model_id}."
            )
        if not np.allclose(
            joined["y_true"].to_numpy(dtype=float),
            joined["target_lst_c"].to_numpy(dtype=float),
            rtol=0.0,
            atol=1e-12,
        ):
            raise ResidualSpatialDiagnosticError(
                f"OOF truth differs from target manifest for {model_id}."
            )
    result["residual_c"] = pd.to_numeric(result["y_pred"], errors="raise") - pd.to_numeric(
        result["y_true"], errors="raise"
    )
    result["absolute_error_c"] = result["residual_c"].abs()
    if not np.isfinite(
        result[["residual_c", "absolute_error_c"]].to_numpy(dtype=float)
    ).all():
        raise ResidualSpatialDiagnosticError("Selected residuals are non-finite.")
    return result.sort_values(
        ["model_id", "target_date", "tract_geoid"], kind="stable"
    ).reset_index(drop=True)


def authenticate_spatial_inputs(
    config: ResidualSpatialConfig,
) -> AuthenticatedSpatialInputs:
    """Authenticate OOF, target, tract, split draft, and split promotion locks."""

    if sha256_file(config.result_analysis_config) != config.locks[
        "result_analysis_config_sha256"
    ]:
        raise ResidualSpatialDiagnosticError("Result-analysis config file lock failed.")
    result_config = load_result_analysis_config(config.result_analysis_config)
    if (
        result_config.evaluation_directory != config.evaluation_directory
        or result_config.target_family != config.family
        or result_config.target_model_id != config.target_model_id
        or result_config.expected_tract_date_row_count
        != config.expected_tract_date_rows_per_model
        or result_config.expected_independent_date_count
        != config.expected_independent_dates
        or result_config.expected_independent_spatial_block_count
        != config.expected_spatial_blocks
    ):
        raise ResidualSpatialDiagnosticError(
            "Frozen result-analysis contract disagrees with spatial diagnostics."
        )
    compiled = authenticate_model_results(
        result_config, evaluation_directory=config.evaluation_directory
    )
    auth = compiled.input_authentication
    if (
        auth["compile_provenance_file_sha256"]
        != config.locks["compile_provenance_file_sha256"]
        or auth["compile_provenance_commit_sha256"]
        != config.locks["compile_provenance_commit_sha256"]
        or auth["oof_predictions_sha256"]
        != config.locks["oof_predictions_sha256"]
    ):
        raise ResidualSpatialDiagnosticError("Compiled OOF locks drifted.")
    strongest = select_strongest_legal_baseline(
        compiled.summary, family=config.family
    )
    if strongest != config.baseline_model_id:
        raise ResidualSpatialDiagnosticError(
            "B1 is no longer the authenticated strongest legal joint baseline."
        )

    promotion = _read_committed_json(
        config.split_promotion,
        expected_file_sha256=config.locks["split_promotion_file_sha256"],
        expected_commit_sha256=config.locks["split_promotion_commit_sha256"],
        label="split promotion",
    )
    draft = _read_committed_json(
        config.split_provenance,
        expected_file_sha256=config.locks["split_provenance_file_sha256"],
        expected_commit_sha256=config.locks["split_provenance_commit_sha256"],
        label="split provenance",
    )
    promotion_required = {
        "state": "promoted",
        "phase_complete": True,
        "ready_for_model_evaluation": True,
        "final_test_year": config.final_test_year,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "row_count": config.expected_tract_date_rows_per_model,
        "independent_date_count": config.expected_independent_dates,
        "spatial_block_count": config.expected_spatial_blocks,
        "tract_count": config.expected_tracts,
    }
    draft_required = {
        "state": "predeclared_draft",
        "final_test_year": config.final_test_year,
        "final_test_locked": True,
        "row_count": config.expected_tract_date_rows_per_model,
        "independent_date_count": config.expected_independent_dates,
        "spatial_block_count": config.expected_spatial_blocks,
        "fixed_tract_count": config.expected_tracts,
        "development_years": list(config.development_years),
    }
    if any(promotion.get(key) != value for key, value in promotion_required.items()):
        raise ResidualSpatialDiagnosticError("Split promotion terminal contract drifted.")
    if any(draft.get(key) != value for key, value in draft_required.items()):
        raise ResidualSpatialDiagnosticError("Split provenance contract drifted.")
    try:
        promoted_draft_lock = promotion["inputs"]["split_draft_provenance"]
        draft_target_lock = draft["input_files"]["legal_row_groups"]
        draft_tract_lock = draft["input_files"]["fixed_tract_manifest"]
    except (KeyError, TypeError) as error:
        raise ResidualSpatialDiagnosticError("Split input-lock chain is incomplete.") from error
    if (
        promoted_draft_lock.get("sha256")
        != config.locks["split_provenance_file_sha256"]
        or promoted_draft_lock.get("commit_sha256")
        != config.locks["split_provenance_commit_sha256"]
        or draft_target_lock.get("sha256") != config.locks["target_manifest_sha256"]
        or draft_tract_lock.get("sha256") != config.locks["tract_manifest_sha256"]
        or draft_tract_lock.get("semantic_sha256")
        != config.locks["tract_manifest_semantic_sha256"]
    ):
        raise ResidualSpatialDiagnosticError("Split-to-target/tract lock chain failed.")

    target_before = sha256_file(config.target_manifest)
    if target_before != config.locks["target_manifest_sha256"]:
        raise ResidualSpatialDiagnosticError("Target manifest byte lock failed.")
    target_columns = [
        "tract_geoid",
        "target_date",
        "spatial_block",
        "target_lst_c",
        "target_available",
        "date_usable",
        "tract_manifest_sha256",
    ]
    targets = _validate_target_frame(
        pd.read_parquet(config.target_manifest, columns=target_columns), config
    )
    if sha256_file(config.target_manifest) != target_before:
        raise ResidualSpatialDiagnosticError("Target manifest changed while being read.")

    tract_before = sha256_file(config.tract_manifest)
    if tract_before != config.locks["tract_manifest_sha256"]:
        raise ResidualSpatialDiagnosticError("Tract manifest byte lock failed.")
    tracts = read_fixed_tracts(
        config.tract_manifest,
        analysis_crs=config.analysis_crs,
        block_size_km=config.block_size_km,
    )
    tract_internal = pd.read_parquet(
        config.tract_manifest,
        columns=["GEOID", "primary_included", "tract_manifest_sha256"],
    )
    if sha256_file(config.tract_manifest) != tract_before:
        raise ResidualSpatialDiagnosticError("Tract manifest changed while being read.")
    if (
        len(tracts) != config.expected_tracts
        or tracts["spatial_block"].nunique() != config.expected_spatial_blocks
        or not tract_internal["primary_included"].eq(True).all()
        or tract_internal["GEOID"].duplicated().any()
        or len(tract_internal) != config.expected_tracts
    ):
        raise ResidualSpatialDiagnosticError("Fixed tract-manifest contract drifted.")
    semantic_sha = _fixed_tract_semantic_sha256(tracts)
    if semantic_sha != config.locks["tract_manifest_semantic_sha256"]:
        raise ResidualSpatialDiagnosticError("Tract geometry semantic lock failed.")
    internal_hashes = tract_internal["tract_manifest_sha256"].astype(str).unique()
    target_internal_hashes = targets["tract_manifest_sha256"].astype(str).unique()
    if (
        len(internal_hashes) != 1
        or len(target_internal_hashes) != 1
        or internal_hashes[0] != target_internal_hashes[0]
    ):
        raise ResidualSpatialDiagnosticError("Internal tract-manifest identity drifted.")
    validate_row_group_tract_lock(
        targets.loc[:, ["tract_geoid", "target_date", "spatial_block"]], tracts
    )
    predictions = _validate_selected_predictions(compiled.oof, targets, config)
    input_authentication = {
        "compile_run_id": auth["compile_run_id"],
        "compile_provenance_file_sha256": auth[
            "compile_provenance_file_sha256"
        ],
        "compile_provenance_commit_sha256": auth[
            "compile_provenance_commit_sha256"
        ],
        "oof_predictions_sha256": auth["oof_predictions_sha256"],
        "target_manifest_sha256": target_before,
        "tract_manifest_sha256": tract_before,
        "tract_manifest_semantic_sha256": semantic_sha,
        "tract_manifest_internal_sha256": str(internal_hashes[0]),
        "split_provenance_file_sha256": config.locks[
            "split_provenance_file_sha256"
        ],
        "split_provenance_commit_sha256": config.locks[
            "split_provenance_commit_sha256"
        ],
        "split_promotion_file_sha256": config.locks[
            "split_promotion_file_sha256"
        ],
        "split_promotion_commit_sha256": config.locks[
            "split_promotion_commit_sha256"
        ],
        "result_analysis_config_sha256": config.locks[
            "result_analysis_config_sha256"
        ],
    }
    return AuthenticatedSpatialInputs(
        predictions=predictions,
        targets=targets,
        tracts=tracts,
        input_authentication=input_authentication,
    )


def aggregate_date_block_residuals(predictions: pd.DataFrame) -> pd.DataFrame:
    """Aggregate tract residuals before any spatial autocorrelation statistic."""

    required = {
        "family",
        "model_id",
        "tract_geoid",
        "target_date",
        "spatial_block",
        "y_true",
        "y_pred",
        "residual_c",
        "absolute_error_c",
    }
    if not required.issubset(predictions.columns):
        raise ResidualSpatialDiagnosticError("Prediction residual columns are incomplete.")
    result = (
        predictions.groupby(
            ["family", "model_id", "target_date", "spatial_block"],
            observed=True,
            sort=True,
        )
        .agg(
            tract_date_row_count=("tract_geoid", "size"),
            mean_residual_c=("residual_c", "mean"),
            mean_absolute_error_c=("absolute_error_c", "mean"),
            root_mean_squared_error_c=(
                "residual_c",
                lambda values: float(np.sqrt(np.mean(np.square(values)))),
            ),
            mean_observed_lst_c=("y_true", "mean"),
            mean_predicted_lst_c=("y_pred", "mean"),
        )
        .reset_index()
    )
    if result.duplicated(["family", "model_id", "target_date", "spatial_block"]).any():
        raise ResidualSpatialDiagnosticError("Date-block residual cells are duplicated.")
    return result


def compute_date_morans(
    cells: pd.DataFrame,
    *,
    model_ids: Sequence[str],
    permutations: int,
    base_seed: int,
    block_id_pattern: str,
) -> pd.DataFrame:
    """Compute one Moran's I per model/date from block-aggregated residuals."""

    records: list[dict[str, Any]] = []
    for model_index, model_id in enumerate(model_ids):
        model_cells = cells.loc[cells["model_id"].eq(model_id)]
        for date_index, (target_date, date_cells) in enumerate(
            model_cells.groupby("target_date", sort=True, observed=True)
        ):
            row_seed = int(
                np.random.SeedSequence(
                    [base_seed, model_index, date_index]
                ).generate_state(1)[0]
            )
            values = date_cells.set_index("spatial_block")["mean_residual_c"]
            result = morans_i_rook(
                values,
                permutations=permutations,
                seed=row_seed,
                block_id_pattern=block_id_pattern,
            )
            records.append(
                {
                    "family": str(date_cells["family"].iloc[0]),
                    "model_id": model_id,
                    "target_date": pd.Timestamp(target_date),
                    "tract_date_row_count_on_date": int(
                        date_cells["tract_date_row_count"].sum()
                    ),
                    "observed_spatial_block_count": result.observation_count,
                    "rook_edge_count": result.rook_edge_count,
                    "mean_block_residual_c": float(
                        date_cells["mean_residual_c"].mean()
                    ),
                    "morans_i": result.morans_i,
                    "randomization_expectation": result.randomization_expectation,
                    "permutation_count": result.permutation_count,
                    "permutation_seed": row_seed,
                    "permutation_p_value_two_sided": (
                        result.permutation_p_value_two_sided
                    ),
                }
            )
    result = pd.DataFrame.from_records(records)
    if result.empty or result.duplicated(["model_id", "target_date"]).any():
        raise ResidualSpatialDiagnosticError("Date-level Moran results are incomplete.")
    return result.sort_values(
        ["model_id", "target_date"], kind="stable"
    ).reset_index(drop=True)


def summarize_date_morans(
    date_morans: pd.DataFrame,
    *,
    exploratory_alpha: float,
) -> pd.DataFrame:
    """Summarize complete-date Moran results without row-level pseudoreplication."""

    records: list[dict[str, Any]] = []
    for (family, model_id), group in date_morans.groupby(
        ["family", "model_id"], sort=True, observed=True
    ):
        significant = group["permutation_p_value_two_sided"].le(exploratory_alpha)
        positive = group["morans_i"].gt(0.0)
        records.append(
            {
                "family": family,
                "model_id": model_id,
                "date_level_observation_count": len(group),
                "mean_morans_i_across_dates": float(group["morans_i"].mean()),
                "median_morans_i_across_dates": float(group["morans_i"].median()),
                "minimum_morans_i": float(group["morans_i"].min()),
                "maximum_morans_i": float(group["morans_i"].max()),
                "positive_morans_i_date_count": int(positive.sum()),
                "exploratory_p_le_alpha_date_count": int(significant.sum()),
                "positive_and_exploratory_p_le_alpha_date_count": int(
                    (positive & significant).sum()
                ),
                "exploratory_alpha": exploratory_alpha,
                "multiple_testing_adjustment": "none_descriptive_diagnostic",
            }
        )
    return pd.DataFrame.from_records(records).sort_values(
        "model_id", kind="stable"
    ).reset_index(drop=True)


def summarize_tract_residuals(predictions: pd.DataFrame) -> pd.DataFrame:
    """Aggregate residual and absolute error across dates for diagnostic maps."""

    result = (
        predictions.groupby(
            ["family", "model_id", "tract_geoid", "spatial_block"],
            observed=True,
            sort=True,
        )
        .agg(
            observed_date_count=("target_date", "nunique"),
            mean_residual_c=("residual_c", "mean"),
            mean_absolute_error_c=("absolute_error_c", "mean"),
            root_mean_squared_error_c=(
                "residual_c",
                lambda values: float(np.sqrt(np.mean(np.square(values)))),
            ),
            mean_observed_lst_c=("y_true", "mean"),
            mean_predicted_lst_c=("y_pred", "mean"),
        )
        .reset_index()
    )
    if result.duplicated(["model_id", "tract_geoid"]).any():
        raise ResidualSpatialDiagnosticError("Tract residual summaries are duplicated.")
    return result


def _audit_columns(
    config: ResidualSpatialConfig,
    inputs: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "authenticated_tract_date_row_count": (
            config.expected_tract_date_rows_per_model
        ),
        "authenticated_independent_date_count": config.expected_independent_dates,
        "authenticated_spatial_block_count": config.expected_spatial_blocks,
        "input_oof_predictions_sha256": inputs["oof_predictions_sha256"],
        "input_compile_provenance_file_sha256": inputs[
            "compile_provenance_file_sha256"
        ],
        "input_compile_provenance_commit_sha256": inputs[
            "compile_provenance_commit_sha256"
        ],
        "input_target_manifest_sha256": inputs["target_manifest_sha256"],
        "input_tract_manifest_sha256": inputs["tract_manifest_sha256"],
        "input_tract_manifest_semantic_sha256": inputs[
            "tract_manifest_semantic_sha256"
        ],
        "input_split_provenance_file_sha256": inputs[
            "split_provenance_file_sha256"
        ],
        "input_split_provenance_commit_sha256": inputs[
            "split_provenance_commit_sha256"
        ],
        "input_split_promotion_file_sha256": inputs[
            "split_promotion_file_sha256"
        ],
        "input_split_promotion_commit_sha256": inputs[
            "split_promotion_commit_sha256"
        ],
        "diagnostic_config_semantic_sha256": config.semantic_sha256,
    }


def _attach_audit_columns(
    frame: pd.DataFrame,
    config: ResidualSpatialConfig,
    inputs: Mapping[str, Any],
) -> pd.DataFrame:
    result = frame.copy()
    for column, value in _audit_columns(config, inputs).items():
        result[column] = value
    return result


def _save_residual_map(
    tracts: gpd.GeoDataFrame,
    tract_summary: pd.DataFrame,
    *,
    model_ids: Sequence[str],
    destination: Path,
) -> None:
    if tuple(model_ids) != ("M2", "B1"):
        raise ResidualSpatialDiagnosticError("Map panels must remain ordered M2 then B1.")
    panels: dict[str, gpd.GeoDataFrame] = {}
    for model_id in model_ids:
        values = tract_summary.loc[
            tract_summary["model_id"].eq(model_id),
            ["tract_geoid", "mean_residual_c", "mean_absolute_error_c"],
        ]
        panel = tracts.loc[:, ["GEOID", "geometry"]].merge(
            values,
            left_on="GEOID",
            right_on="tract_geoid",
            how="left",
            validate="one_to_one",
        )
        if panel[["mean_residual_c", "mean_absolute_error_c"]].isna().any().any():
            raise ResidualSpatialDiagnosticError(
                f"Map join is incomplete for {model_id}."
            )
        panels[model_id] = gpd.GeoDataFrame(
            panel, geometry="geometry", crs=tracts.crs
        )
    all_residuals = np.concatenate(
        [panels[model]["mean_residual_c"].to_numpy(dtype=float) for model in model_ids]
    )
    all_mae = np.concatenate(
        [
            panels[model]["mean_absolute_error_c"].to_numpy(dtype=float)
            for model in model_ids
        ]
    )
    residual_limit = float(np.quantile(np.abs(all_residuals), 0.99))
    mae_limit = float(np.quantile(all_mae, 0.99))
    if residual_limit <= 0 or mae_limit <= 0:
        raise ResidualSpatialDiagnosticError("Map color ranges are degenerate.")
    residual_norm = TwoSlopeNorm(
        vmin=-residual_limit, vcenter=0.0, vmax=residual_limit
    )
    mae_norm = Normalize(vmin=0.0, vmax=mae_limit)

    figure = Figure(figsize=(12, 9), constrained_layout=True)
    FigureCanvasAgg(figure)
    axes = figure.subplots(2, 2, squeeze=False)
    for column, model_id in enumerate(model_ids):
        panels[model_id].plot(
            column="mean_residual_c",
            ax=axes[0, column],
            cmap="RdBu_r",
            norm=residual_norm,
            edgecolor="#6f6f6f",
            linewidth=0.08,
        )
        axes[0, column].set_title(f"{model_id}: mean residual")
        panels[model_id].plot(
            column="mean_absolute_error_c",
            ax=axes[1, column],
            cmap="viridis",
            norm=mae_norm,
            edgecolor="#6f6f6f",
            linewidth=0.08,
        )
        axes[1, column].set_title(f"{model_id}: mean absolute error")
        for row in range(2):
            axes[row, column].set_axis_off()
            axes[row, column].set_aspect("equal")
    residual_mappable = ScalarMappable(norm=residual_norm, cmap="RdBu_r")
    residual_mappable.set_array([])
    mae_mappable = ScalarMappable(norm=mae_norm, cmap="viridis")
    mae_mappable.set_array([])
    figure.colorbar(
        residual_mappable,
        ax=list(axes[0, :]),
        shrink=0.78,
        label="Mean prediction residual (°C; y_pred − y_true)",
    )
    figure.colorbar(
        mae_mappable,
        ax=list(axes[1, :]),
        shrink=0.78,
        label="Mean absolute error (°C)",
    )
    figure.suptitle("Joint OOF residual diagnostics, Los Angeles (2020–2024)")
    figure.text(
        0.5,
        0.01,
        "Tract geometry is used only for diagnostics; color limits use the shared 99th percentile.",
        ha="center",
        fontsize=9,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    figure.savefig(
        temporary,
        format="png",
        dpi=200,
        facecolor="white",
    )
    temporary.replace(destination)
    figure.clear()


def _csv_record(path: Path, frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "path": path.as_posix(),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "rows": len(frame),
        "columns": frame.columns.tolist(),
        "schema_sha256": canonical_sha256(
            [(str(column), str(dtype)) for column, dtype in frame.dtypes.items()]
        ),
    }


def analyze_residual_spatial(
    config_path: str | Path = DEFAULT_DIAGNOSTIC_CONFIG,
    *,
    table_output_directory: str | Path | None = None,
    figure_output_directory: str | Path | None = None,
) -> dict[str, Any]:
    """Run the frozen diagnostic, write tables/map, and commit provenance last."""

    config = load_residual_spatial_config(config_path)
    authenticated = authenticate_spatial_inputs(config)
    table_output = (
        config.table_output_directory
        if table_output_directory is None
        else Path(table_output_directory).resolve()
    )
    figure_output = (
        config.figure_output_directory
        if figure_output_directory is None
        else Path(figure_output_directory).resolve()
    )
    table_output.mkdir(parents=True, exist_ok=True)
    figure_output.mkdir(parents=True, exist_ok=True)
    provenance_path = table_output / PROVENANCE_FILENAME
    provenance_path.unlink(missing_ok=True)

    cells = aggregate_date_block_residuals(authenticated.predictions)
    cell_counts = cells.groupby("model_id", observed=True).size().to_dict()
    if set(cell_counts) != {config.target_model_id, config.baseline_model_id} or any(
        int(value) != config.expected_date_block_cells_per_model
        for value in cell_counts.values()
    ):
        raise ResidualSpatialDiagnosticError("Date-block cell counts drifted.")
    date_morans = compute_date_morans(
        cells,
        model_ids=(config.target_model_id, config.baseline_model_id),
        permutations=config.permutations,
        base_seed=config.permutation_seed,
        block_id_pattern=config.block_id_pattern,
    )
    if (
        len(date_morans) != config.expected_independent_dates * 2
        or not date_morans.groupby("model_id", observed=True).size().eq(
            config.expected_independent_dates
        ).all()
    ):
        raise ResidualSpatialDiagnosticError("Date-level Moran coverage drifted.")
    moran_summary = summarize_date_morans(
        date_morans, exploratory_alpha=config.exploratory_alpha
    )
    tract_summary = summarize_tract_residuals(authenticated.predictions)
    if (
        len(tract_summary) != config.expected_tracts * 2
        or not tract_summary.groupby("model_id", observed=True).size().eq(
            config.expected_tracts
        ).all()
    ):
        raise ResidualSpatialDiagnosticError("Tract residual coverage drifted.")

    inputs = authenticated.input_authentication
    cells_output = _attach_audit_columns(cells, config, inputs)
    date_output = _attach_audit_columns(date_morans, config, inputs)
    summary_output = _attach_audit_columns(moran_summary, config, inputs)
    tract_output = _attach_audit_columns(tract_summary, config, inputs)
    for frame in (cells_output, date_output):
        frame["target_date"] = pd.to_datetime(frame["target_date"]).dt.strftime(
            "%Y-%m-%d"
        )

    table_paths = {
        DATE_BLOCK_FILENAME: table_output / DATE_BLOCK_FILENAME,
        DATE_MORAN_FILENAME: table_output / DATE_MORAN_FILENAME,
        MORAN_SUMMARY_FILENAME: table_output / MORAN_SUMMARY_FILENAME,
        TRACT_SUMMARY_FILENAME: table_output / TRACT_SUMMARY_FILENAME,
    }
    table_frames = {
        DATE_BLOCK_FILENAME: cells_output,
        DATE_MORAN_FILENAME: date_output,
        MORAN_SUMMARY_FILENAME: summary_output,
        TRACT_SUMMARY_FILENAME: tract_output,
    }
    for name, destination in table_paths.items():
        atomic_csv(table_frames[name], destination)
    map_path = figure_output / MAP_FILENAME
    _save_residual_map(
        authenticated.tracts,
        tract_summary,
        model_ids=(config.target_model_id, config.baseline_model_id),
        destination=map_path,
    )

    pipeline_sha256, pipeline_fingerprint = code_runtime_fingerprint(
        project_root=_project_root(),
        relative_paths=_PIPELINE_PATHS,
        algorithm_version=DIAGNOSTIC_ALGORITHM_VERSION,
    )
    pipeline_fingerprint["packages"]["matplotlib"] = matplotlib.__version__
    pipeline_sha256 = canonical_sha256(pipeline_fingerprint)
    output_files = {
        "tables": {
            name: _csv_record(table_paths[name], table_frames[name])
            for name in table_paths
        },
        "figures": {
            MAP_FILENAME: {
                "path": map_path.as_posix(),
                "sha256": sha256_file(map_path),
                "bytes": map_path.stat().st_size,
                "format": "PNG",
                "panel_count": 4,
            }
        },
    }
    provenance: dict[str, Any] = {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "algorithm_version": DIAGNOSTIC_ALGORITHM_VERSION,
        "state": "complete",
        "ready_for_residual_spatial_interpretation": True,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "analysis_scope": "locked_2020_2024_development_joint_oof_only",
        "family": config.family,
        "target_model_id": config.target_model_id,
        "baseline_model_id": config.baseline_model_id,
        "residual_definition": config.residual_definition,
        "final_test_year": config.final_test_year,
        "final_test_locked": True,
        "contains_final_test_year": False,
        "tract_date_row_count_per_model": config.expected_tract_date_rows_per_model,
        "independent_date_count": config.expected_independent_dates,
        "independent_spatial_block_count": config.expected_spatial_blocks,
        "tract_count": config.expected_tracts,
        "date_block_cell_count_per_model": config.expected_date_block_cells_per_model,
        "input_authentication": inputs,
        "diagnostic_config": {
            "path": config.path.as_posix(),
            "file_sha256": sha256_file(config.path),
            "semantic_sha256": config.semantic_sha256,
        },
        "spatial_statistic_contract": {
            "statistic": "global_morans_i",
            "input_unit": "one_unweighted_mean_residual_per_observed_date_block_cell",
            "date_block_aggregation_precedes_statistic": True,
            "adjacency": config.adjacency,
            "weights": config.weights,
            "s0_definition": "sum_of_directed_binary_rook_weights",
            "one_statistic_per_model_date": True,
            "permutation_unit": "complete_block_aggregate_values_within_date",
            "permutation_seed": config.permutation_seed,
            "permutations_per_model_date": config.permutations,
            "permutation_p_value": config.permutation_p_value,
            "exploratory_alpha": config.exploratory_alpha,
            "multiple_testing_adjustment": "none_descriptive_diagnostic",
            "random_or_independent_tract_date_rows_used": False,
        },
        "map_contract": {
            "geometry_source": config.tract_manifest.as_posix(),
            "geometry_crs": config.analysis_crs,
            "panels": [
                "M2 mean residual",
                "B1 mean residual",
                "M2 mean absolute error",
                "B1 mean absolute error",
            ],
            "coordinates_used_as_predictors": False,
            "geometry_used_for_diagnostics_only": True,
            "models_fitted": False,
        },
        "pipeline_sha256": pipeline_sha256,
        "pipeline_fingerprint": pipeline_fingerprint,
        "output_files": output_files,
    }
    provenance["commit_sha256"] = canonical_sha256(provenance)
    atomic_json(provenance, provenance_path)
    return provenance
