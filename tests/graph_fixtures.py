"""Small structured inputs; no workbook creation or mutation."""
from src.data.schema import (ExpertReferencePlan, Geometry, GeometrySegment, Operation,
                             PartData, Station)


def part(operations=None, geometries=(), groups=(), stations=None):
    return PartData(tuple(operations or (Operation("P", "A", "TRIM"), Operation("P", "B", "TRIM"))),
                    tuple(geometries), tuple(stations or (
                        Station("P", "OP90", 1, frozenset({"TRIM"})),
                        Station("P", "OP10", 2, frozenset({"TRIM"})))),
                    tuple(groups), ExpertReferencePlan(()))


def circle(name="G", center=(0, 0), radius=2):
    return Geometry("P", name, (GeometrySegment(1, "CIRCLE", center_x=center[0], center_y=center[1], radius=radius),))


def bend(name="G", start=(0, 0), end=(3, 4)):
    return Geometry("P", name, (GeometrySegment(1, "BEND_LINE", *start, *end),))


def rectangle(name="G", start=(0, 0), end=(4, 2), bridge=False):
    if bridge:
        return Geometry("P", name, (GeometrySegment(1, "BRIDGE", *start, *end,
                                                    (start[0]+end[0])/2, (start[1]+end[1])/2),))
    points = (start, (end[0], start[1]), end, (start[0], end[1]))
    return Geometry("P", name, tuple(GeometrySegment(i+1, "LINE", *a, *b)
                                    for i, (a, b) in enumerate(zip(points, points[1:]+points[:1]))))
