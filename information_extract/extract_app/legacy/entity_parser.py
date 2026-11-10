"""Read primitives and bulges in native order; transform points explicitly."""
from __future__ import annotations

import math
from ezdxf.math import Vec3, Vec2, bulge_center, Matrix44
from .transform import (
    GeometryError, finite_point, ocs_point, flat_points, circle_basis,
    check_arc_plane, angle_xy,
)


def point_fields(prefix: str, point: Vec3) -> dict:
    return {f"{prefix}_{axis}": value for axis, value in zip("xyz", point)}


def make_line(start: Vec3, end: Vec3, tolerance: float) -> dict:
    flat_points([start, end], tolerance)
    if (end - start).magnitude <= tolerance:
        raise GeometryError("DEGENERATE_LINE", "直线长度不大于 tolerance，保留在异常清单。")
    return {"entity_type": "LINE", **point_fields("start", start), **point_fields("end", end)}


def make_arc(start, end, center, radius, sweep, tolerance) -> dict:
    if not math.isfinite(radius) or radius <= 0 or not math.isfinite(sweep) or sweep == 0:
        raise GeometryError("INVALID_ARC", "圆弧半径或扫掠角无效。")
    flat_points([start, end, center], tolerance)
    return {
        "entity_type": "ARC", **point_fields("start", start), **point_fields("end", end),
        **point_fields("center", center), "radius": radius,
        "start_angle": angle_xy(start, center), "end_angle": angle_xy(end, center),
        "sweep_angle": sweep,
    }


def parse_primitive(entity, matrix: Matrix44, tolerance: float, relative_tolerance: float) -> list[dict]:
    kind = entity.dxftype()
    if kind == "LINE":
        # LINE endpoints are in block-local WCS, not OCS, even with extrusion.
        start = finite_point(matrix.transform(finite_point(entity.dxf.start)))
        end = finite_point(matrix.transform(finite_point(entity.dxf.end)))
        return [make_line(start, end, tolerance)]
    center_local = finite_point(entity.dxf.center)
    radius = float(entity.dxf.radius)
    if not math.isfinite(radius) or radius <= 0:
        raise GeometryError("INVALID_RADIUS", "圆/圆弧半径必须为有限正数。")
    scale, orientation, u, v = circle_basis(entity, matrix, tolerance, relative_tolerance)
    check_arc_plane(radius, u, v, tolerance)
    center = ocs_point(entity, center_local, matrix)
    if kind == "CIRCLE":
        return [{"entity_type": "CIRCLE", **point_fields("center", center), "radius": radius * scale}]
    start_angle, end_angle = float(entity.dxf.start_angle), float(entity.dxf.end_angle)
    if not all(math.isfinite(a) for a in (start_angle, end_angle)):
        raise GeometryError("INVALID_ARC", "圆弧角度不是有限数。")
    sweep = (end_angle - start_angle) % 360.0
    if sweep == 0:
        raise GeometryError("AMBIGUOUS_ARC_SWEEP", "ARC 起终角相同，无法确定零角或整圆；请人工核验。")
    points = [center_local + Vec3(radius * math.cos(math.radians(a)), radius * math.sin(math.radians(a)), 0)
              for a in (start_angle, end_angle)]
    start, end = (ocs_point(entity, point, matrix) for point in points)
    return [make_arc(start, end, center, radius * scale, sweep * orientation, tolerance)]


def parse_polyline(entity, matrix: Matrix44, tolerance: float, relative_tolerance: float) -> list[dict]:
    vertices = list(entity.get_points("xyseb"))
    if len(vertices) < 2:
        raise GeometryError("TOO_FEW_VERTICES", "LWPOLYLINE 少于两个顶点。")
    if not all(math.isfinite(float(value)) for vertex in vertices for value in vertex):
        raise GeometryError("NON_FINITE_VERTEX", "多段线顶点、宽度或 bulge 不是有限数。")
    elevation = float(entity.dxf.elevation)
    local_points = [Vec3(x, y, elevation) for x, y, *_ in vertices]
    world_points = [ocs_point(entity, p, matrix) for p in local_points]
    flat_points(world_points, tolerance)
    count = len(vertices) if entity.closed else len(vertices) - 1
    result = []
    basis = None
    for i in range(count):
        j = (i + 1) % len(vertices)
        start, end = world_points[i], world_points[j]
        x1, y1, start_width, end_width, bulge = vertices[i]
        x2, y2 = vertices[j][:2]
        if bulge == 0.0:
            row = make_line(start, end, tolerance)
        else:
            if Vec2(x1, y1).distance(Vec2(x2, y2)) == 0:
                raise GeometryError("DEGENERATE_BULGE", f"第 {i + 1} 段 bulge 的两端重合。")
            if basis is None:
                basis = circle_basis(entity, matrix, tolerance, relative_tolerance)
            scale, orientation, u, v = basis
            # bulge_center keeps vertex order, unlike a CCW-only arc converter.
            c = bulge_center(Vec2(x1, y1), Vec2(x2, y2), bulge)
            radius = c.distance(Vec2(x1, y1))
            check_arc_plane(radius, u, v, tolerance)
            center = ocs_point(entity, Vec3(c.x, c.y, elevation), matrix)
            sweep = math.degrees(4.0 * math.atan(bulge)) * orientation
            row = make_arc(start, end, center, radius * scale, sweep, tolerance)
        row.update(polyline_seq=i + 1, bulge=float(bulge),
                   source_start_width=float(start_width), source_end_width=float(end_width))
        result.append(row)
    return result
