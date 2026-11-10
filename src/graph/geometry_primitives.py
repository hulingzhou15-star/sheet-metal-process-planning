"""Exact curve primitives derived only from Milestone 1 Geometry objects.

Circles are full circular arcs; BRIDGE is its four footprint boundary segments.
These are calculation objects, never graph nodes. No sampling or CAD inference.
"""
from dataclasses import dataclass
import math

from src.data.schema import Geometry, SegmentType

Point = tuple[float, float]
TAU = 2 * math.pi
ANGLE_EPS = 1e-12


@dataclass(frozen=True)
class Line:
    start: Point
    end: Point


@dataclass(frozen=True)
class Arc:
    center: Point
    radius: float
    start_angle: float = 0.0
    sweep: float = TAU

    @property
    def full_circle(self):
        return abs(self.sweep) >= TAU - ANGLE_EPS

    def has_angle(self, angle):
        if self.full_circle:
            return True
        delta = ((angle - self.start_angle) * (1 if self.sweep >= 0 else -1)) % TAU
        return delta <= abs(self.sweep) + ANGLE_EPS or TAU - delta <= ANGLE_EPS

    def point(self, angle):
        return (self.center[0] + self.radius * math.cos(angle),
                self.center[1] + self.radius * math.sin(angle))

    @property
    def endpoints(self):
        return (self.point(self.start_angle), self.point(self.start_angle + self.sweep))


Primitive = Line | Arc


def geometry_primitives(geometry: Geometry) -> tuple[Primitive, ...]:
    result = []
    for s in sorted(geometry.segments, key=lambda s: s.seq):
        if s.segment_type in (SegmentType.LINE, SegmentType.BEND_LINE):
            if s.start is None or s.end is None:
                raise ValueError("Line endpoints are required")
            result.append(Line(s.start, s.end))
        elif s.segment_type in (SegmentType.CIRCLE, SegmentType.ARC):
            if s.center is None or s.radius is None or s.radius < 0:
                raise ValueError("Circular primitive center and nonnegative radius are required")
            if s.segment_type == SegmentType.CIRCLE:
                result.append(Arc(s.center, s.radius))
            else:
                if s.start is None or s.sweep_angle is None:
                    raise ValueError("ARC start and signed sweep are required")
                angle = math.atan2(s.start_y-s.center_y, s.start_x-s.center_x)
                result.append(Arc(s.center, s.radius, angle, math.radians(s.sweep_angle)))
        elif s.segment_type == SegmentType.BRIDGE:
            corners = s.bridge_footprint()
            result.extend(Line(a, b) for a, b in zip(corners, corners[1:] + corners[:1]))
        else:
            raise ValueError(f"Unsupported geometry primitive: {s.segment_type}")
    if not result:
        raise ValueError("A provided Geometry must contain primitives")
    for p in result:
        values = (*p.start, *p.end) if isinstance(p, Line) else (*p.center, p.radius, p.start_angle, p.sweep)
        if not all(math.isfinite(x) for x in values):
            raise ValueError("Geometry primitives must be finite")
    return tuple(result)


def primitive_bbox(p: Primitive) -> tuple[float, float, float, float]:
    points = (p.start, p.end) if isinstance(p, Line) else p.endpoints + tuple(
        p.point(angle) for angle in (0, math.pi/2, math.pi, 3*math.pi/2) if p.has_angle(angle))
    return (min(x for x, y in points), min(y for x, y in points),
            max(x for x, y in points), max(y for x, y in points))


def primitives_bbox(primitives) -> tuple[float, float, float, float]:
    boxes = [primitive_bbox(p) for p in primitives]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))
