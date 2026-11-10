"""Analytic closest-point evidence checked against frozen M2 distances.

These witnesses enrich an audit only. M2 remains the authoritative distance API.
One minimizer is returned; closest points need not be unique.
"""
import math

from src.graph.geometry_distance import PARAM_EPS, point_arc_distance, point_line_distance
from src.graph.geometry_primitives import Arc, Line


def sub(a, b):
    return a[0] - b[0], a[1] - b[1]


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def project_point(point, line):
    delta = sub(line.end, line.start)
    squared = dot(delta, delta)
    raw = dot(sub(point, line.start), delta) / squared if squared else 0.0
    t = max(0.0, min(1.0, raw))
    return {"point": (line.start[0] + t * delta[0], line.start[1] + t * delta[1]),
            "unclamped_parameter": raw, "parameter": t,
            "location": "START" if t == 0 else "END" if t == 1 else "INTERIOR"}


def point_on_arc(point, arc):
    radius = math.dist(point, arc.center)
    if arc.radius == 0:
        return arc.center
    angle = math.atan2(point[1] - arc.center[1], point[0] - arc.center[0]) if radius else arc.start_angle
    if arc.has_angle(angle):
        return arc.point(angle)
    return min(arc.endpoints, key=lambda p: math.dist(point, p))


def primitive_candidates(a, b):
    if isinstance(a, Arc) and isinstance(b, Line):
        return [(q, p) for p, q in primitive_candidates(b, a)]
    if isinstance(a, Line) and isinstance(b, Line):
        candidates = [(a.start, project_point(a.start, b)["point"]),
                      (a.end, project_point(a.end, b)["point"]),
                      (project_point(b.start, a)["point"], b.start),
                      (project_point(b.end, a)["point"], b.end)]
        u, v = sub(a.end, a.start), sub(b.end, b.start)
        denominator = cross(u, v)
        if denominator:
            offset = sub(b.start, a.start)
            t, s = cross(offset, v) / denominator, cross(offset, u) / denominator
            if 0 <= t <= 1 and 0 <= s <= 1:
                point = (a.start[0] + t * u[0], a.start[1] + t * u[1])
                candidates.append((point, point))
        return candidates
    if isinstance(a, Line) and isinstance(b, Arc):
        if b.radius == 0:
            return [(project_point(b.center, a)["point"], b.center)]
        candidates = [(p, point_on_arc(p, b)) for p in (a.start, a.end)]
        candidates.extend((project_point(p, a)["point"], p) for p in b.endpoints)
        delta = sub(a.end, a.start)
        length = math.hypot(*delta)
        if not length:
            return candidates
        unit = delta[0] / length, delta[1] / length
        projection = dot(sub(b.center, a.start), unit)
        foot = a.start[0] + projection * unit[0], a.start[1] + projection * unit[1]
        perpendicular = math.dist(b.center, foot)
        h_squared = b.radius ** 2 - perpendicular ** 2
        tolerance = PARAM_EPS * max(1.0, b.radius ** 2, perpendicular ** 2)
        if h_squared >= -tolerance:
            half = math.sqrt(max(0.0, h_squared))
            for signed in (-half, half):
                point = foot[0] + signed * unit[0], foot[1] + signed * unit[1]
                if 0 <= projection + signed <= length and b.has_angle(math.atan2(point[1]-b.center[1], point[0]-b.center[0])):
                    candidates.append((point, point))
        normal = math.atan2(unit[0], -unit[1])
        for angle in (normal, normal + math.pi):
            if b.has_angle(angle):
                point = b.point(angle)
                candidates.append((project_point(point, a)["point"], point))
        return candidates
    if not isinstance(a, Arc) or not isinstance(b, Arc):
        raise TypeError("Only frozen Line/Arc primitives are supported")
    if a.radius == 0:
        return [(a.center, point_on_arc(a.center, b))]
    if b.radius == 0:
        return [(point_on_arc(b.center, a), b.center)]
    candidates = [(p, point_on_arc(p, b)) for p in a.endpoints]
    candidates.extend((point_on_arc(p, a), p) for p in b.endpoints)
    distance = math.dist(a.center, b.center)
    if not distance:
        for angle in (a.start_angle, a.start_angle + a.sweep, b.start_angle, b.start_angle + b.sweep):
            if a.has_angle(angle) and b.has_angle(angle):
                candidates.append((a.point(angle), b.point(angle)))
        return candidates
    unit = (b.center[0] - a.center[0]) / distance, (b.center[1] - a.center[1]) / distance
    along = (a.radius ** 2 - b.radius ** 2 + distance ** 2) / (2 * distance)
    h_squared = a.radius ** 2 - along ** 2
    tolerance = PARAM_EPS * max(1.0, a.radius ** 2, along ** 2)
    if h_squared >= -tolerance:
        height = math.sqrt(max(0.0, h_squared))
        for signed in (-height, height):
            p = a.center[0] + along * unit[0] - signed * unit[1], a.center[1] + along * unit[1] + signed * unit[0]
            if a.has_angle(math.atan2(p[1]-a.center[1], p[0]-a.center[0])) and b.has_angle(math.atan2(p[1]-b.center[1], p[0]-b.center[0])):
                candidates.append((p, p))
    angle = math.atan2(unit[1], unit[0])
    for alpha in (angle, angle + math.pi):
        if a.has_angle(alpha):
            for beta in (angle, angle + math.pi):
                if b.has_angle(beta):
                    candidates.append((a.point(alpha), b.point(beta)))
    return candidates


def location(point, primitive):
    if isinstance(primitive, Line):
        return project_point(point, primitive)["location"]
    if primitive.full_circle:
        return "FULL_CIRCLE"
    if any(math.dist(point, endpoint) <= PARAM_EPS for endpoint in primitive.endpoints):
        return "ARC_ENDPOINT"
    return "ARC_INTERIOR"


def closest_witness(a, b):
    best = None
    for i, p in enumerate(a):
        for j, q in enumerate(b):
            x, y = min(primitive_candidates(p, q), key=lambda pair: math.dist(*pair))
            distance = math.dist(x, y)
            if best is None or distance < best["distance"]:
                best = {"distance": distance, "point_a": x, "point_b": y,
                        "primitive_index_a": i, "primitive_index_b": j,
                        "primitive_kind_a": type(p).__name__, "primitive_kind_b": type(q).__name__,
                        "location_a": location(x, p), "location_b": location(y, q),
                        "uniqueness": "one_minimizer_not_a_uniqueness_claim"}
            if distance == 0:
                return best
    if best is None:
        raise ValueError("Witness needs two nonempty primitive sets")
    return best


def bend_pair_features(a, b):
    def interval(source, target):
        length = math.dist(source.start, source.end)
        if not length:
            return None
        u = ((source.end[0]-source.start[0])/length, (source.end[1]-source.start[1])/length)
        ends = sorted(dot(sub(p, source.start), u) for p in (target.start, target.end))
        overlap = max(0.0, min(length, ends[1]) - max(0.0, ends[0]))
        return {"projected_interval": ends, "overlap_length": overlap, "overlap_fraction_of_source": overlap/length}
    u, v = sub(a.end, a.start), sub(b.end, b.start)
    lengths = math.hypot(*u), math.hypot(*v)
    angle = math.degrees(math.acos(min(1.0, abs(dot(u, v)) / (lengths[0] * lengths[1])))) if all(lengths) else None
    return {"acute_angle_degrees": angle, "projection_on_a": interval(a, b), "projection_on_b": interval(b, a),
            "length_a": lengths[0], "length_b": lengths[1]}


def curve_length(p):
    return math.dist(p.start, p.end) if isinstance(p, Line) else p.radius * abs(p.sweep)


def distance_profile(source, target, count):
    """Arclength sample diagnostics, never used as the exact distance or gate."""
    lengths = [curve_length(p) for p in source]
    total = sum(lengths)
    values = []
    for n in range(count):
        remaining = total * (n + .5) / count
        index = 0
        while index < len(source)-1 and remaining > lengths[index]:
            remaining -= lengths[index]
            index += 1
        primitive = source[index]
        t = remaining / lengths[index] if lengths[index] else 0
        point = ((primitive.start[0]+t*(primitive.end[0]-primitive.start[0]), primitive.start[1]+t*(primitive.end[1]-primitive.start[1]))
                 if isinstance(primitive, Line) else primitive.point(primitive.start_angle+t*primitive.sweep))
        values.append(min(point_line_distance(point, p) if isinstance(p, Line) else point_arc_distance(point, p) for p in target))
    ordered = sorted(values)
    def quantile(q):
        position = q * (len(ordered)-1)
        i, j = math.floor(position), math.ceil(position)
        return ordered[i] + (ordered[j]-ordered[i])*(position-i)
    return {"method": "arclength_midpoint_samples_observation_only", "sample_count": count,
            "source_curve_length": total, "sampled_min": min(values),
            "q10": quantile(.1), "q50": quantile(.5), "q90": quantile(.9), "sampled_max": max(values)}
