"""Annotation metadata only; these fields do not define manufacturing rules."""
from copy import deepcopy
import json
import math
from pathlib import Path


FIELD_DEFAULTS = json.loads((Path(__file__).resolve().parents[1] / "configs/annotation_fields.json").read_text(encoding="utf-8"))
DEFAULT_BEND_ANGLE_DEG = FIELD_DEFAULTS["bend_angle_deg"]


def validate_bend_angle(value):
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
        raise ValueError("折弯角度必须为有限数值，单位为度；未知时可留空。")


def parse_bend_angle(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        validate_bend_angle(value)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("折弯角度必须为数值，单位为度。") from exc
    validate_bend_angle(number)
    return number


def preserve_bend_angles(project, previous):
    """Older clients may omit this optional field; never erase a saved angle."""
    old = {op["uid"]: op for op in previous["operations"]}
    for op in project["operations"]:
        if op.get("operation_type") != "BEND" or "bend_angle_deg" in op:
            continue
        prior = old.get(op["uid"])
        if prior is None:
            op["bend_angle_deg"] = DEFAULT_BEND_ANGLE_DEG
        elif prior.get("operation_type") == "BEND" and "bend_angle_deg" in prior:
            op["bend_angle_deg"] = prior["bend_angle_deg"]


def fill_missing_bend_angles(project):
    result, changed = deepcopy(project), []
    for op in result["operations"]:
        if op.get("operation_type") == "BEND" and op.get("bend_angle_deg") is None:
            op["bend_angle_deg"] = DEFAULT_BEND_ANGLE_DEG
            changed.append(op["operation_id"])
    return result, changed
