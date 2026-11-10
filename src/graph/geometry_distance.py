"""Analytic shortest distances between the recorded planar curve sets.

No polygon filling, centroid proxy or contour sampling. A circle is its
circumference, a contour is the union of its LINE/ARC pieces, FLANGING is the
union of its recorded circles, and BRIDGE uses four footprint boundary lines.
Interior minima are either intersections or stationary normal/radial pairs;
endpoint-to-primitive candidates cover constrained minima on finite pieces.
"""
import math

from .geometry_primitives import Arc, Line, geometry_primitives

PARAM_EPS = 1e-12


def _subtract(a, b):
    return (a[0]-b[0], a[1]-b[1])


def _dot(a, b):
    return a[0]*b[0]+a[1]*b[1]


def _cross(a, b):
    return a[0]*b[1]-a[1]*b[0]


def _angle(point, arc):
    return math.atan2(point[1]-arc.center[1], point[0]-arc.center[0])


def point_line_distance(point, line):
    delta = _subtract(line.end, line.start)
    squared = _dot(delta, delta)
    if squared == 0:
        return math.dist(point, line.start)
    t = max(0.0, min(1.0, _dot(_subtract(point, line.start), delta)/squared))
    return math.dist(point, (line.start[0]+t*delta[0], line.start[1]+t*delta[1]))


def point_arc_distance(point, arc):
    radius_from_center = math.dist(point, arc.center)
    if radius_from_center == 0 or arc.radius == 0:
        return abs(radius_from_center-arc.radius)
    if arc.has_angle(_angle(point, arc)):
        return abs(radius_from_center-arc.radius)
    return min(math.dist(point, endpoint) for endpoint in arc.endpoints)


def line_line_distance(a, b):
    u, v = _subtract(a.end, a.start), _subtract(b.end, b.start)
    denominator = _cross(u, v)
    if denominator != 0:
        offset = _subtract(b.start, a.start)
        t, s = _cross(offset, v)/denominator, _cross(offset, u)/denominator
        if 0 <= t <= 1 and 0 <= s <= 1:
            return 0.0
    # Also handles parallel, collinear and zero-length segments.
    return min(point_line_distance(a.start, b), point_line_distance(a.end, b),
               point_line_distance(b.start, a), point_line_distance(b.end, a))


def line_arc_distance(line, arc):
    if arc.radius == 0:
        return point_line_distance(arc.center, line)
    delta = _subtract(line.end, line.start)
    length = math.hypot(*delta)
    if length == 0:
        return point_arc_distance(line.start, arc)
    unit = (delta[0]/length, delta[1]/length)
    projection = _dot(_subtract(arc.center, line.start), unit)
    foot = (line.start[0]+projection*unit[0], line.start[1]+projection*unit[1])
    perpendicular = math.dist(arc.center, foot)
    h_squared = arc.radius**2-perpendicular**2
    tolerance = PARAM_EPS*max(1.0, arc.radius**2, perpendicular**2)
    if h_squared >= -tolerance:
        half_chord = math.sqrt(max(0.0, h_squared))
        for signed in (-half_chord, half_chord):
            along = projection+signed
            point = (foot[0]+signed*unit[0], foot[1]+signed*unit[1])
            if 0 <= along <= length and arc.has_angle(_angle(point, arc)):
                return 0.0
    candidates = [point_arc_distance(line.start, arc), point_arc_distance(line.end, arc)]
    candidates.extend(point_line_distance(point, line) for point in arc.endpoints)
    # Stationary circle points have radii normal to the supporting line.
    normal_angle = math.atan2(unit[0], -unit[1])
    for angle in (normal_angle, normal_angle+math.pi):
        if arc.has_angle(angle):
            candidates.append(point_line_distance(arc.point(angle), line))
    return min(candidates)


def arc_arc_distance(a, b):
    if a.radius == 0:
        return point_arc_distance(a.center, b)
    if b.radius == 0:
        return point_arc_distance(b.center, a)
    center_distance = math.dist(a.center, b.center)
    if a.full_circle and b.full_circle:
        return max(0.0, center_distance-a.radius-b.radius, abs(a.radius-b.radius)-center_distance)
    candidates = [point_arc_distance(p, b) for p in a.endpoints]
    candidates.extend(point_arc_distance(p, a) for p in b.endpoints)
    if center_distance == 0:
        # Closed angular intervals overlap iff one contains an endpoint of the
        # other (full circles included). On overlap the radial gap is minimal.
        angles = (a.start_angle, a.start_angle+a.sweep, b.start_angle, b.start_angle+b.sweep)
        if any(a.has_angle(t) and b.has_angle(t) for t in angles):
            candidates.append(abs(a.radius-b.radius))
        return min(candidates)
    unit = ((b.center[0]-a.center[0])/center_distance, (b.center[1]-a.center[1])/center_distance)
    along = (a.radius**2-b.radius**2+center_distance**2)/(2*center_distance)
    h_squared = a.radius**2-along**2
    tolerance = PARAM_EPS*max(1.0, a.radius**2, along**2)
    if h_squared >= -tolerance:
        height = math.sqrt(max(0.0, h_squared))
        for signed in (-height, height):
            point = (a.center[0]+along*unit[0]-signed*unit[1],
                     a.center[1]+along*unit[1]+signed*unit[0])
            if a.has_angle(_angle(point, a)) and b.has_angle(_angle(point, b)):
                return 0.0
    angle = math.atan2(unit[1], unit[0])
    # At an interior stationary pair both radii lie on the centers' line.
    for alpha in (angle, angle+math.pi):
        if a.has_angle(alpha):
            for beta in (angle, angle+math.pi):
                if b.has_angle(beta):
                    candidates.append(math.dist(a.point(alpha), b.point(beta)))
    return min(candidates)


def primitive_distance(a, b):
    if isinstance(a, Line) and isinstance(b, Line):
        result = line_line_distance(a, b)
    elif isinstance(a, Line) and isinstance(b, Arc):
        result = line_arc_distance(a, b)
    elif isinstance(a, Arc) and isinstance(b, Line):
        result = line_arc_distance(b, a)
    elif isinstance(a, Arc) and isinstance(b, Arc):
        result = arc_arc_distance(a, b)
    else:
        raise TypeError("Distance supports Line and Arc (including full circles)")
    if not math.isfinite(result) or result < 0:
        raise ValueError("Geometry distance must be finite and nonnegative")
    return result


def primitives_distance(a, b):
    if not a or not b:
        raise ValueError("Geometry distance requires nonempty primitive sets")
    best = math.inf
    for p in a:
        for q in b:
            best = min(best, primitive_distance(p, q))
            if best == 0:
                return 0.0
    return best


def geometry_distance(a, b):
    """Public convenience API for two frozen Milestone 1 Geometry objects."""
    return primitives_distance(geometry_primitives(a), geometry_primitives(b))
