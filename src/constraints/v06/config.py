"""Versioned parameters; new direction component remains unweighted."""
from copy import deepcopy
import json
import math
from pathlib import Path

from .context import RULE_VERSION

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs/m3_constraints_v06.yaml"


def validate_config(config):
    config = deepcopy(config)
    if config["rule_version"] != RULE_VERSION:
        raise ValueError("Unexpected rule_version")
    if config["soft_weights"] != {"bend_area_ordering": .45, "precedence_extra_gap": .20, "type_fragmentation": .35}:
        raise ValueError("v0.6 does not authorize new soft weights")
    if math.fsum(config["soft_weights"].values()) != 1.0:
        raise ValueError("soft weights must sum to 1")
    area = config["bend_area_ordering"]
    ratio = area["significance_ratio"]
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not math.isfinite(ratio) or ratio <= 1:
        raise ValueError("significance_ratio must be finite and greater than 1")
    if area["exclude_all_hard_precedence_pairs"] is not True:
        raise ValueError("v0.6 must exclude hard precedence pairs")
    if config["type_fragmentation"]["enabled_types"] != ["FLANGING"]:
        raise ValueError("Only FLANGING type fragmentation is confirmed")
    direction = config["bend_direction_fragmentation"]
    if direction["weight"] is not None:
        raise ValueError("Direction weight is not confirmed; it must be null")
    if set(direction["enabled_directions"]) != {"SMOOTH", "ROUGH"} or len(direction["enabled_directions"]) != 2:
        raise ValueError("Direction categories must be SMOOTH and ROUGH")
    k = config["proximity_diagnostic"]["k"]
    if type(k) is not int or k < 1:
        raise ValueError("k must be a positive integer")
    solver = config["solver"]
    t = solver["max_time_seconds"]
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or t <= 0:
        raise ValueError("max_time_seconds must be positive and finite")
    for name, minimum in (("random_seed", 0), ("num_search_workers", 1)):
        if type(solver[name]) is not int or solver[name] < minimum:
            raise ValueError(f"Invalid solver {name}")
    return config


def load_config(path=DEFAULT_CONFIG_PATH):
    return validate_config(json.loads(Path(path).read_text(encoding="utf-8")))
