"""Presentation only: frozen Geometry, assignments and blind comparison IDs."""
from .config import load_visualization_config
from .geometry_adapter import build_geometry_catalog
from .plan_view_model import build_plan_view_model, operation_status, operation_focus
from .diff_view_model import build_diff_view_model
from .svg_renderer import render_svg

__all__ = ["load_visualization_config", "build_geometry_catalog", "build_plan_view_model",
           "operation_status", "operation_focus", "build_diff_view_model", "render_svg"]
