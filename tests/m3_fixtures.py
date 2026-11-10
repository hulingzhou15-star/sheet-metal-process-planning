"""Artificial object-only Plans. Never alter a source workbook."""
from pathlib import Path

from src.constraints.context import BendInfluence, ConstraintContext
from src.data.schema import ExpertAssignment, ExpertReferencePlan, Operation, PartData, ProcessGroup, Station

ROOT = Path(__file__).resolve().parents[1]
P001 = ROOT / "data/parts/P001/钣金加工数据表_P001.xlsx"


def region(op, region_id, *, area=100, depth=10, parent=None, start=(0, 0), end=(10, 0), far=None,
           axis="HORIZONTAL", side="POSITIVE_Y"):
    boundary = start[1]+depth if far is None else far
    return BendInfluence(op, *start, *end, axis, boundary, depth, side, 10, area, region_id, parent)


def context(operations, regions=(), *, assignments=None, stations=None, fixed=(), geometries=()):
    operations = tuple(operations)
    if stations is None:
        # Names deliberately disagree with order; nonconsecutive orders catch numerical-gap bugs.
        stations = tuple(Station("P", name, order, frozenset({op.operation_type for op in operations}))
                         for name, order in (("OP90", 10), ("OP10", 30), ("OP80", 60), ("OP05", 100)))
    if assignments is None:
        assignments = {op.operation_id: stations[0].station_id for op in operations}
    expert = ExpertReferencePlan(tuple(ExpertAssignment(op, station) for op, station in assignments.items()))
    group_ids = sorted({op.process_group_id for op in operations if op.process_group_id})
    groups = tuple(ProcessGroup("P", group, tuple(op.operation_id for op in operations if op.process_group_id == group))
                   for group in group_ids)
    data = PartData(operations, tuple(geometries), tuple(stations), groups, expert)
    return ConstraintContext(data, tuple(fixed), tuple(regions))


def bends(areas=(200, 100), *, parents=(None, None), assignments=None):
    ops = tuple(Operation("P", chr(65+i), "BEND") for i in range(len(areas)))
    regions = tuple(region(op.operation_id, f"R{i+1}", area=area, parent=parents[i]) for i, (op, area) in enumerate(zip(ops, areas)))
    return context(ops, regions, assignments=assignments)
