"""Review controls only; no changes to generation or constraint parameters."""
from copy import deepcopy
import json
import math
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs/m3_human_review.yaml"
BUCKET_ORDER = ("NEAR", "MEDIUM", "FAR")
ROLES = ("LOW_SOFT", "TYPICAL", "HIGH_OP60_LOAD", "DIVERSITY")
QUESTION = "仅从当前给出的工艺方案看，你认为哪一种安排更合理？"
ANSWER_OPTIONS = (('A', '方案 A 更合理'), ('B', '方案 B 更合理'), ('C', '两者基本等价'),
                  ('D', '两者都存在明显问题'), ('E', '当前信息不足，无法判断'))


def validate_review_config(config):
    c = deepcopy(config)
    if (c["inputs"]["expected_selected_count"], c["inputs"]["expected_per_bucket"]) != (60, 20):
        raise ValueError("v0.3 consumes the frozen 60-candidate batch, 20 per bucket")
    r, p, b = c["representatives"], c["pairs"], c["blinding"]
    if r["per_bucket"] != len(ROLES) or r["typical_statistics_scope"] != "remaining":
        raise ValueError("v0.3 requires four roles per bucket and confirmed remaining-set TYPICAL statistics")
    epsilon = r["typical_epsilon"]
    if type(epsilon) not in (int, float) or not math.isfinite(epsilon) or epsilon <= 0:
        raise ValueError("typical_epsilon must be finite and positive")
    if not isinstance(r["high_load_station"], str) or not r["high_load_station"]:
        raise ValueError("high_load_station must be a Station ID")
    if (p["reference_per_bucket"], p["within_per_bucket"], p["adjacent_per_boundary"], p["challenge_per_kind"]) != (2, 2, 3, 3):
        raise ValueError("v0.3 fixes Group A/B/C/D counts to 6/6/6/6")
    for key in ("challenge_distance_initial", "challenge_distance_step"):
        if type(p[key]) is not int or p[key] < (0 if key.endswith("initial") else 1):
            raise ValueError(f"Invalid {key}")
    for key in ("challenge_soft_initial", "challenge_soft_step"):
        if type(p[key]) not in (int, float) or not math.isfinite(p[key]) or p[key] <= 0:
            raise ValueError(f"Invalid {key}")
    for key in ("plan_blinding_seed", "comparison_blinding_seed"):
        if type(b[key]) is not int or b[key] < 0:
            raise ValueError(f"Invalid {key}")
    if type(b["shuffle_comparison_order"]) is not bool:
        raise ValueError("shuffle_comparison_order must be boolean")
    for key in ("numeric_decimal_places", "initial_open_comparisons"):
        if type(c["presentation"][key]) is not int or c["presentation"][key] < 0:
            raise ValueError(f"Invalid {key}")
    return c


def load_review_config(path=DEFAULT_CONFIG_PATH):
    return validate_review_config(json.loads(Path(path).read_text(encoding="utf-8")))
