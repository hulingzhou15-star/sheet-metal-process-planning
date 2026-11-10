"""M3 v0.2 controls; JSON is the YAML subset used by the existing project."""
from copy import deepcopy
import json
import math
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs/m3_candidate_generator.yaml"
BUCKET_ORDER = ("NEAR", "MEDIUM", "FAR")


def validate_generator_config(config):
    c = deepcopy(config)

    def integer(section, field, minimum=1):
        value = c[section][field]
        if type(value) is not int or value < minimum:
            raise ValueError(f"{section}.{field} must be an integer >= {minimum}")

    def finite(value):
        return type(value) in (int, float) and math.isfinite(value)

    if c["solver"]["engine"] != "cp_sat" or c["selection"]["method"] != "farthest_first":
        raise ValueError("Only cp_sat and farthest_first are implemented")
    integer("solver", "random_seed", 0)
    if c["solver"]["num_search_workers"] != 1:
        raise ValueError("v0.2 uses one worker for reproducible solver search")
    for field in ("max_distance_timeout_seconds", "per_candidate_timeout_seconds"):
        if not finite(c["solver"][field]) or c["solver"][field] <= 0:
            raise ValueError(f"solver.{field} must be finite and positive")
    for field in ("pool_target_per_bucket", "final_target_per_bucket", "max_attempts_per_bucket", "random_objective_coefficient_abs_max"):
        integer("generation", field)
    integer("generation", "exact_distance_fallback", 0)
    integer("diagnostics", "top_movement_count")
    integer("reporting", "progress_every_attempts")
    ratios = c["distance_buckets"]
    if not (finite(ratios["near_max_ratio"]) and finite(ratios["medium_max_ratio"])
            and 0 < ratios["near_max_ratio"] < ratios["medium_max_ratio"] < 1):
        raise ValueError("Bucket ratios must satisfy 0 < near < medium < 1")
    fraction = c["selection"]["soft_prefilter_fraction"]
    if not finite(fraction) or not 0 < fraction <= 1:
        raise ValueError("soft_prefilter_fraction must be in (0,1]")
    for section, field in (("generation", "use_no_good_constraints"), ("guardrails", "apply_station_overload_guardrail"),
                           ("diagnostics", "include_d3")):
        if type(c[section][field]) is not bool:
            raise ValueError(f"{section}.{field} must be boolean")
    return c


def load_generator_config(path=DEFAULT_CONFIG_PATH):
    return validate_generator_config(json.loads(Path(path).read_text(encoding="utf-8")))
