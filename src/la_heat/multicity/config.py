"""Typed, fail-closed configuration for the cross-city continuation study."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from la_heat.provenance import canonical_sha256, sha256_file

CITY_ID_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]*$")
STATE_FIPS_PATTERN: Final = re.compile(r"^\d{2}$")
PLACE_GEOID_PATTERN: Final = re.compile(r"^\d{7}$")
EPSG_PATTERN: Final = re.compile(r"^EPSG:\d{4,6}$")


class MulticityConfigError(ValueError):
    """Raised when the continuation configuration violates its planning contract."""


@dataclass(frozen=True)
class CitySpec:
    """Target-independent identity and geometry settings for one city."""

    id: str
    name: str
    state_fips: str
    census_place_geoid: str
    timezone: str
    target_grid_crs: str
    role: str
    target_values_status: str
    config_path: Path


@dataclass(frozen=True)
class MulticityPlan:
    """Validated experiment configuration and its four city specifications."""

    raw: dict[str, Any]
    path: Path
    cities: tuple[CitySpec, ...]
    source_files: tuple[Path, ...]

    @property
    def experiment_id(self) -> str:
        return str(self.raw["experiment"]["id"])

    @property
    def source_city(self) -> CitySpec:
        return next(city for city in self.cities if city.role == "source_anchor")

    @property
    def external_cities(self) -> tuple[CitySpec, ...]:
        return tuple(
            city for city in self.cities if city.role == "external_confirmation"
        )

    @property
    def semantic_sha256(self) -> str:
        return canonical_sha256(
            {
                "experiment": self.raw,
                "cities": [
                    {
                        "id": city.id,
                        "name": city.name,
                        "state_fips": city.state_fips,
                        "census_place_geoid": city.census_place_geoid,
                        "timezone": city.timezone,
                        "target_grid_crs": city.target_grid_crs,
                        "role": city.role,
                        "target_values_status": city.target_values_status,
                    }
                    for city in self.cities
                ],
            }
        )

    @property
    def file_records(self) -> dict[str, dict[str, Any]]:
        project_root = self.path.parents[2]
        return {
            path.relative_to(project_root).as_posix(): {
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
            for path in self.source_files
        }


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    if not isinstance(payload, dict):
        raise MulticityConfigError(f"TOML root must be a table: {path}")
    return payload


def _city_spec(path: Path) -> CitySpec:
    payload = _read_toml(path)
    try:
        city = payload["city"]
        spec = CitySpec(
            id=str(city["id"]),
            name=str(city["name"]),
            state_fips=str(city["state_fips"]),
            census_place_geoid=str(city["census_place_geoid"]),
            timezone=str(city["timezone"]),
            target_grid_crs=str(city["target_grid_crs"]),
            role=str(city["role"]),
            target_values_status=str(city["target_values_status"]),
            config_path=path.resolve(),
        )
    except (KeyError, TypeError) as exc:
        raise MulticityConfigError(f"Incomplete city configuration: {path}") from exc

    if not CITY_ID_PATTERN.fullmatch(spec.id):
        raise MulticityConfigError(f"Invalid city id: {spec.id}")
    if not spec.name.strip():
        raise MulticityConfigError(f"City name is empty: {spec.id}")
    if not STATE_FIPS_PATTERN.fullmatch(spec.state_fips):
        raise MulticityConfigError(f"Invalid state FIPS for {spec.id}")
    if not PLACE_GEOID_PATTERN.fullmatch(spec.census_place_geoid):
        raise MulticityConfigError(f"Invalid Census place GEOID for {spec.id}")
    if not spec.census_place_geoid.startswith(spec.state_fips):
        raise MulticityConfigError(f"Place GEOID does not match state for {spec.id}")
    if "/" not in spec.timezone:
        raise MulticityConfigError(f"Timezone must be an IANA name for {spec.id}")
    if not EPSG_PATTERN.fullmatch(spec.target_grid_crs):
        raise MulticityConfigError(f"Invalid target-grid CRS for {spec.id}")
    if spec.role not in {"source_anchor", "external_confirmation"}:
        raise MulticityConfigError(f"Invalid role for {spec.id}: {spec.role}")
    expected_status = (
        "known_phase1_anchor"
        if spec.role == "source_anchor"
        else "sealed"
    )
    if spec.target_values_status != expected_status:
        raise MulticityConfigError(
            f"Target status for {spec.id} must be {expected_status}."
        )
    return spec


def _require_exact_model_contract(raw: dict[str, Any]) -> None:
    models = raw["models"]
    b1 = models["b1_transfer"]
    m2 = models["m2_transfer"]
    if b1.get("estimator") != "ridge" or float(b1.get("ridge_alpha", -1)) != 10.0:
        raise MulticityConfigError("B1-Transfer must retain the frozen Ridge alpha 10.")
    expected_m2 = {
        "estimator": "histogram_gradient_boosting_regressor",
        "loss": "absolute_error",
        "learning_rate": 0.05,
        "max_iter": 300,
        "max_leaf_nodes": 31,
        "min_samples_leaf": 50,
        "l2_regularization": 1.0,
        "early_stopping": False,
        "random_state": 20260719,
    }
    observed = {key: m2.get(key) for key in expected_m2}
    if observed != expected_m2:
        raise MulticityConfigError(
            "M2-Transfer settings must match the Phase I locked model class."
        )


def _validate_plan(raw: dict[str, Any], cities: tuple[CitySpec, ...]) -> None:
    required_sections = {
        "experiment",
        "locks",
        "phase1_anchor",
        "design",
        "target",
        "predictors",
        "models",
        "uncertainty",
        "evaluation",
        "prospective",
        "sources",
        "paths",
    }
    missing = sorted(required_sections - set(raw))
    if missing:
        raise MulticityConfigError(f"Experiment configuration lacks sections: {missing}")

    experiment = raw["experiment"]
    locks = raw["locks"]
    design = raw["design"]
    anchor = raw["phase1_anchor"]

    if experiment.get("status") != "draft_pre_registration":
        raise MulticityConfigError("The initial continuation must remain a draft.")
    if locks.get("protocol_locked") is not False:
        raise MulticityConfigError("Protocol lock requires a separate freeze action.")
    locked_false_fields = (
        "external_targets_unlocked",
        "external_target_values_read",
        "external_prediction_commit_exists",
        "allow_predictor_construction",
        "allow_model_fitting",
        "allow_external_target_access",
    )
    if any(locks.get(field) is not False for field in locked_false_fields):
        raise MulticityConfigError("Draft planning may not unlock computation or targets.")
    if locks.get("allow_boundary_metadata_staging") is not True:
        raise MulticityConfigError("Boundary metadata staging should be the only open stage.")
    if locks.get("authorized_metadata_city_ids") != ["phoenix_az"]:
        raise MulticityConfigError(
            "The draft metadata pilot must remain limited to Phoenix."
        )

    if len(cities) != 4 or len({city.id for city in cities}) != 4:
        raise MulticityConfigError("Exactly four unique city specifications are required.")
    if len({city.census_place_geoid for city in cities}) != 4:
        raise MulticityConfigError("Census place GEOIDs must be unique.")
    source = [city for city in cities if city.role == "source_anchor"]
    external = [city for city in cities if city.role == "external_confirmation"]
    if len(source) != 1 or len(external) != 3:
        raise MulticityConfigError("The design requires one source and three external cities.")
    if source[0].id != experiment.get("source_city_id"):
        raise MulticityConfigError("Source city identity disagrees across configuration.")
    if [city.id for city in external] != list(experiment.get("external_city_ids", [])):
        raise MulticityConfigError("External city order or identity changed.")

    training_years = [int(year) for year in design.get("training_years", [])]
    calibration_year = int(design.get("uncertainty_calibration_year", -1))
    confirmation_year = int(design.get("external_confirmation_year", -1))
    if training_years != [2020, 2021, 2022, 2023]:
        raise MulticityConfigError("Training years must remain 2020-2023.")
    if calibration_year != 2024 or confirmation_year != 2025:
        raise MulticityConfigError("Calibration and confirmation years changed.")
    if calibration_year in training_years or confirmation_year in training_years:
        raise MulticityConfigError("Training, calibration, and confirmation years overlap.")
    if design.get("zero_shot_external_labels") is not True:
        raise MulticityConfigError("External target labels must remain zero-shot.")
    if design.get("external_city_preprocessing_allowed") is not False:
        raise MulticityConfigError("External-city preprocessing fit is prohibited.")
    if design.get("external_city_climatology_allowed") is not False:
        raise MulticityConfigError("External-city labeled climatology is prohibited.")

    if anchor.get("la_2025_is_known") is not True:
        raise MulticityConfigError("The known Phase I LA 2025 result must be declared.")
    if anchor.get("la_2025_allowed_in_new_confirmation") is not False:
        raise MulticityConfigError("LA 2025 cannot be new confirmatory evidence.")
    if raw["predictors"].get("portable_water_distance_source_frozen") is not False:
        raise MulticityConfigError("Portable water-distance source needs a separate freeze.")
    if raw["sources"].get("portable_water_distance_source") != "NOT_YET_FROZEN":
        raise MulticityConfigError("Unfrozen portable water-distance source was populated.")
    expected_census_sources = {
        "census_tract_layer": (
            "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
            "tigerWMS_Census2020/MapServer/6"
        ),
        "census_place_layer": (
            "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
            "tigerWMS_Census2020/MapServer/26"
        ),
        "census_tract_pilot_mirror_layer": (
            "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/"
            "USA_Census_2020_Redistricting_Tracts/FeatureServer/0"
        ),
        "census_tract_pilot_mirror_item": "e3a7d2d3e5834b7eb6b1c2943141ced6",
        "census_place_pilot_mirror_layer": (
            "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/"
            "USA_Census_2020_Redistricting_Incorporated_Places/FeatureServer/0"
        ),
        "census_place_pilot_mirror_item": "13ea1fb24ca14842bb265e6ec6ac1d46",
        "census_pilot_mirror_vertex_contract": (
            "2020_TIGER_boundaries_no_vertex_alteration_declared"
        ),
    }
    observed_census_sources = {
        key: raw["sources"].get(key) for key in expected_census_sources
    }
    if observed_census_sources != expected_census_sources:
        raise MulticityConfigError(
            "The Census primary and pilot-mirror source identities changed."
        )
    if raw["prospective"].get("operational_forecast_claim_allowed") is not False:
        raise MulticityConfigError("The Daymet pipeline cannot support a forecast claim.")
    _require_exact_model_contract(raw)


def load_multicity_plan(path: str | Path) -> MulticityPlan:
    """Load and validate the experiment plus all referenced city files."""

    config_path = Path(path).resolve()
    raw = _read_toml(config_path)
    city_files = raw.pop("city_files", None)
    if not isinstance(city_files, list) or not city_files:
        raise MulticityConfigError("city_files must be a non-empty list.")
    resolved_city_files = tuple(
        (config_path.parent / str(relative)).resolve() for relative in city_files
    )
    cities = tuple(_city_spec(city_path) for city_path in resolved_city_files)
    _validate_plan(raw, cities)
    return MulticityPlan(
        raw=raw,
        path=config_path,
        cities=cities,
        source_files=(config_path, *resolved_city_files),
    )
