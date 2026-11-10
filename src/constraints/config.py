"""Load the JSON subset of YAML without adding dependencies to frozen M1/M2."""
from copy import deepcopy
import json
import math
from pathlib import Path

from src.data.schema import OperationType

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs/m3_constraints.yaml"
SOFT_COMPONENTS = ("bend_area_ordering", "precedence_extra_gap", "type_fragmentation")


def validate_config(config):
    config = deepcopy(config)

    def number(value, label, *, minimum=0, strict=False):
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                or (value <= minimum if strict else value < minimum)):
            raise ValueError(f"Invalid numeric configuration: {label}")

    weights = config["soft_weights"]
    if set(weights) != set(SOFT_COMPONENTS):
        raise ValueError("Exactly P1-P3 soft_weights are required")
    for name, value in weights.items():
        number(value, f"soft_weights.{name}")
    # fsum avoids changing the weights; equality is to the specified unit total.
    if math.fsum(weights.values()) != 1.0:
        raise ValueError("soft_weights must sum to 1.0; no automatic normalization")
    for name in ("bend_area_ordering", "depth_diagnostic"):
        number(config[name]["significance_ratio"], f"{name}.significance_ratio", minimum=1, strict=True)
        if config[name]["exclude_ancestor_descendant_pairs"] is not True:
            raise ValueError(f"{name} must exclude ancestor/descendant pairs under M3 v0.1")
    types = config["type_fragmentation"]["enabled_types"]
    if not isinstance(types, list) or len(set(types)) != len(types) or any(t not in set(OperationType) for t in types):
        raise ValueError("enabled_types must be a list of distinct known OperationTypes")
    guard = config["station_overload_guardrail"]
    if type(guard["enabled"]) is not bool:
        raise ValueError("Guardrail enabled must be boolean")
    number(guard["relative_limit"], "station_overload_guardrail.relative_limit", strict=True)
    if type(guard["absolute_slack"]) is not int or guard["absolute_slack"] < 0:
        raise ValueError("absolute_slack must be a nonnegative integer")
    return config


def load_config(path=DEFAULT_CONFIG_PATH):
    """The .yaml file uses YAML-compatible JSON syntax, including JSON booleans."""
    return validate_config(json.loads(Path(path).read_text(encoding="utf-8")))
