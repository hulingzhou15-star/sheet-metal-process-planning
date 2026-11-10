"""geometry_features_v1, analytic bounding boxes/areas and explicit masks."""
import math

from src.data.schema import OperationType, PartData
from .geometry_primitives import Arc, Line, geometry_primitives, primitives_bbox
from .schema import GeometryFeaturesV1, NormalizationContext


def contour_area(primitives):
    origin = primitives[0].start if isinstance(primitives[0], Line) else primitives[0].endpoints[0]
    terms = []
    for p in primitives:
        start, end = (p.start, p.end) if isinstance(p, Line) else p.endpoints
        sx, sy, ex, ey = start[0]-origin[0], start[1]-origin[1], end[0]-origin[0], end[1]-origin[1]
        term = (sx*ey-ex*sy)/2
        if isinstance(p, Arc):
            term += p.radius**2 * (p.sweep-math.sin(p.sweep))/2
        terms.append(term)
    return abs(math.fsum(terms))


class GeometryFeatureExtractorV1:
    def __init__(self, data: PartData):
        geometries = {(g.part_id, g.geometry_id): g for g in data.geometries}
        if len(geometries) != len(data.geometries):
            raise ValueError("Geometry IDs must be unique within a part")
        self.geometries = geometries
        self.primitives = {key: geometry_primitives(g) for key, g in geometries.items()}
        self.boxes = {key: primitives_bbox(p) for key, p in self.primitives.items()}
        boxes = list(self.boxes.values())
        bbox = ((min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
                if boxes else (0.0, 0.0, 0.0, 0.0))
        diagonal = math.hypot(bbox[2]-bbox[0], bbox[3]-bbox[1])
        if not math.isfinite(diagonal):
            raise ValueError("Normalization extent must be finite")
        self.normalization = NormalizationContext(bbox, ((bbox[0]+bbox[2])/2, (bbox[1]+bbox[3])/2),
                                                  diagonal, diagonal if diagonal > 0 else 1.0, diagonal == 0)

    def extract(self, operation):
        if operation.geometry_id is None:
            return GeometryFeaturesV1()
        key = (operation.part_id, operation.geometry_id)
        if key not in self.geometries:
            raise ValueError(f"Unknown Geometry reference: {key}")
        g, ps, box = self.geometries[key], self.primitives[key], self.boxes[key]
        x0, y0, x1, y1 = box
        width, height = x1-x0, y1-y0
        center = ((x0+x1)/2, (y0+y1)/2)
        length, area, perimeter = math.hypot(width, height), 0.0, 0.0
        has_area = has_perimeter = has_orientation = 0
        cos2 = sin2 = 0.0
        kind = OperationType(operation.operation_type)
        if kind in (OperationType.CIRCLE_PUNCH, OperationType.PRE_PUNCH, OperationType.DIMPLE, OperationType.FLANGING):
            if not all(isinstance(p, Arc) and p.full_circle for p in ps):
                raise ValueError("Circular Operation requires recorded circles")
            outer = max(ps, key=lambda p: p.radius)
            center, length = outer.center, 2*outer.radius
            if kind != OperationType.FLANGING:
                area, perimeter = math.pi*outer.radius**2, 2*math.pi*outer.radius
                has_area = has_perimeter = 1
            # SPEC 12/13: flanging area/perimeter intentionally remain masked.
        elif kind == OperationType.BEND:
            if len(ps) != 1 or not isinstance(ps[0], Line):
                raise ValueError("BEND requires one line")
            p = ps[0]
            center = ((p.start[0]+p.end[0])/2, (p.start[1]+p.end[1])/2)
            dx, dy = p.end[0]-p.start[0], p.end[1]-p.start[1]
            length = math.hypot(dx, dy)
            if length > 0:
                cos2, sin2 = (dx*dx-dy*dy)/length**2, 2*dx*dy/length**2
                has_orientation = 1
        elif kind == OperationType.BRIDGE:
            center = g.segments[0].center
            if center is None:
                raise ValueError("BRIDGE requires its recorded center")
            area, perimeter = width*height, 2*(width+height)
            has_area = has_perimeter = 1
            # Long rectangle axis is its undirected main orientation. A square
            # has no unique long axis; keep orientation masked in that case.
            if width != height:
                cos2, has_orientation = (1.0 if width > height else -1.0), 1
        elif kind == OperationType.IRREGULAR_PUNCH:
            area = contour_area(ps)
            perimeter = math.fsum(math.dist(p.start, p.end) if isinstance(p, Line) else p.radius*abs(p.sweep) for p in ps)
            has_area = has_perimeter = 1
        else:
            raise ValueError(f"No v1 summary contract for a provided {kind.value} Geometry")
        norm, scale = self.normalization, self.normalization.length_scale
        result = GeometryFeaturesV1(1, (center[0]-norm.center[0])/scale, (center[1]-norm.center[1])/scale,
                                   width/scale, height/scale, length/scale, area/scale**2, perimeter/scale,
                                   cos2, sin2, 1, has_area, has_perimeter, has_orientation)
        if not all(math.isfinite(x) for x in result.vector()):
            raise ValueError("Derived geometry features must be finite")
        return result
