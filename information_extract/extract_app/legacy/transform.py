"""OCS -> block coordinates -> model-space WCS, without changing DXF entities."""
from __future__ import annotations

import math
from ezdxf.math import Matrix44, Vec3


class GeometryError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def finite_point(point) -> Vec3:
    p = Vec3(point)
    if not all(math.isfinite(v) for v in p):
        raise GeometryError("NON_FINITE_COORDINATE", "坐标包含 NaN / Infinity。")
    return p


def ocs_point(entity, point, matrix: Matrix44) -> Vec3:
    normal = finite_point(entity.dxf.get("extrusion", (0, 0, 1)))
    if normal.magnitude == 0:
        raise GeometryError("INVALID_EXTRUSION", "extrusion 为零向量。")
    return finite_point(matrix.transform(entity.ocs().to_wcs(finite_point(point))))


def flat_points(points: list[Vec3], tolerance: float) -> None:
    if points and max(p.z for p in points) - min(p.z for p in points) > tolerance:
        raise GeometryError("NON_PLANAR_XY", "几何不平行于 WCS XY 平面，二维字段无法可靠表示。")


def circle_basis(entity, matrix: Matrix44, tolerance: float, relative_tolerance: float):
    """Check the final circular plane, including composed scale/shear/reflection."""
    normal = finite_point(entity.dxf.get("extrusion", (0, 0, 1)))
    if normal.magnitude == 0:
        raise GeometryError("INVALID_EXTRUSION", "extrusion 为零向量。")
    ocs = entity.ocs()
    u = finite_point(matrix.transform_direction(ocs.to_wcs((1, 0, 0))))
    v = finite_point(matrix.transform_direction(ocs.to_wcs((0, 1, 0))))
    lu, lv = u.magnitude, v.magnitude
    if lu == 0 or lv == 0:
        raise GeometryError("DEGENERATE_TRANSFORM", "变换使圆的基向量退化。")
    if abs(lu - lv) > relative_tolerance * max(lu, lv) or abs(u.dot(v)) > relative_tolerance * lu * lv:
        raise GeometryError("NON_CIRCULAR_TRANSFORM", "非均匀缩放或剪切使圆/圆弧变为椭圆，未近似输出。")
    radius = float(entity.dxf.radius) if entity.dxftype() in {"ARC", "CIRCLE"} else 1.0
    # For polylines radius is supplied later, so their basis check is angular.
    extent = max(abs(radius), 1.0) * max(lu, lv)
    if max(abs(u.z) / lu, abs(v.z) / lv) * extent > tolerance:
        raise GeometryError("TILTED_CIRCULAR_PLANE", "圆/圆弧平面倾斜，投影后不能作为二维圆输出。")
    determinant = u.x * v.y - u.y * v.x
    if determinant == 0:
        raise GeometryError("DEGENERATE_TRANSFORM", "圆平面在 XY 中退化。")
    return (lu + lv) / 2.0, 1.0 if determinant > 0 else -1.0, u, v


def check_arc_plane(radius: float, u: Vec3, v: Vec3, tolerance: float) -> None:
    if radius * max(abs(u.z), abs(v.z)) > tolerance:
        raise GeometryError("TILTED_CIRCULAR_PLANE", "圆弧平面倾斜，二维圆弧字段无法可靠表示。")


def angle_xy(point: Vec3, center: Vec3) -> float:
    return math.degrees(math.atan2(point.y - center.y, point.x - center.x)) % 360.0


def normalize_line(start: Vec3, end: Vec3, tolerance: float):
    """Only for independent candidate LINEs; raw endpoints retain source order."""
    if abs(start.y - end.y) < tolerance:
        reverse, kind = start.x > end.x, "horizontal"
    elif abs(start.x - end.x) < tolerance:
        reverse, kind = start.y > end.y, "vertical"
    else:
        reverse, kind = (start.x, start.y) > (end.x, end.y), "diagonal"
    return (end, start, kind, True) if reverse else (start, end, kind, False)
