"""Central visual parameters. YAML uses the project's valid JSON subset."""
from copy import deepcopy
import json
import math
from pathlib import Path
import re

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs/m3_visualization.yaml"
MODES = ("CURRENT_ONLY", "CUMULATIVE", "FULL_PLAN_BY_STATION")
COLOR_MODES = ("BY_CURRENT_STATUS", "BY_OPERATION_TYPE")


def load_visualization_config(path=DEFAULT_CONFIG_PATH):
    return validate_visualization_config(json.loads(Path(path).read_text(encoding="utf-8")))


def validate_visualization_config(config):
    c = deepcopy(config)
    if c["display"]["default_mode"] not in MODES or c["display"]["default_color_mode"] not in COLOR_MODES:
        raise ValueError("Unknown display or color mode")
    if c["display"]["default_rotation_degrees"] not in (0,270) or type(c["display"]["default_rotation_degrees"]) is not int:
        raise ValueError("Display rotation must be 0 or 270 degrees")
    if type(c["display"]["enable_orientation_toggle"]) is not bool:
        raise ValueError("Orientation toggle must be boolean")
    for name in ("show_influence_region_default", "difference_only_default"):
        if type(c["display"][name]) is not bool:
            raise ValueError("Display switches must be boolean")
    if c["svg"]["coordinate_direction"] != "CARTESIAN_Y_UP":
        raise ValueError("Frozen material coordinates must stay x-right/y-up")
    for name, value in c["svg"].items():
        if name in ("coordinate_direction", "numeric_decimals"):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid SVG parameter {name}")
        if "opacity" in name and value > 1:
            raise ValueError("Opacity must be in [0,1]")
    if type(c["svg"]["numeric_decimals"]) is not int or not 3 <= c["svg"]["numeric_decimals"] <= 15:
        raise ValueError("Invalid coordinate precision")
    if c["svg"]["minimum_extent"] <= 0 or c["svg"]["normal_stroke_width"] <= 0:
        raise ValueError("Extent and stroke width must be positive")
    for name in ("sync_station", "sync_zoom_pan"):
        if c["interaction"][name] is not True:
            raise ValueError("A/B Station and viewport synchronization are required")
    if type(c["interaction"]["enable_region_filter"]) is not bool:
        raise ValueError("Region switch must be boolean")
    for key in ("minimum_zoom", "maximum_zoom", "zoom_step", "wheel_zoom_step", "pan_threshold_px"):
        value = c["interaction"][key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("Invalid zoom parameters")
    if c["interaction"]["zoom_step"] <= 1 or c["interaction"]["wheel_zoom_step"] <= 1:
        raise ValueError("Zoom steps must exceed one")
    if not c["interaction"]["minimum_zoom"] <= 1 <= c["interaction"]["maximum_zoom"]:
        raise ValueError("Zoom range must contain the initial view")
    for section in ("layout", "previews"):
        for key, value in c[section].items():
            if section == "previews" and key in ("comparison_id", "plan_id", "stations"):
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError("Layout/preview dimensions must be positive")
    colors = [*(c["colors"][key] for key in ("current", "previous", "background", "selected", "influence", "paper")),
              *(c["colors"][key] for key in ("hover","outline_stroke","outline_fill")),
              *c["colors"]["ui"].values(), *c["colors"]["operation_types"].values(), *c["colors"]["station_palette"]]
    if not c["colors"]["station_palette"] or any(not re.fullmatch(r"#[0-9a-fA-F]{6}", color) for color in colors):
        raise ValueError("Colors must be local six-digit hex values")
    o=c["outline"]
    if type(o["enabled"]) is not bool or not isinstance(o["source_path"],str) or not isinstance(o["part_id"],str):
        raise ValueError("Invalid display outline settings")
    for name in ("roundtrip_tolerance","containment_flatten_tolerance","stroke_width","stroke_opacity","fill_opacity"):
        value=o[name]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0 or ("opacity" in name and value>1):
            raise ValueError("Invalid outline visual parameter")
    return c
