"""H1-H6 only: structured failures, no exceptions or expert-derived capability."""
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import asdict, dataclass

from src.data.schema import ExpertReferencePlan
from .context import ConstraintContext


@dataclass(frozen=True)
class ConstraintViolation:
    constraint_type: str
    operation_ids: tuple[str, ...]
    station_ids: tuple[str | None, ...]
    message: str
    context: dict


@dataclass(frozen=True)
class HardConstraintResult:
    feasible: bool
    violations: tuple[ConstraintViolation, ...]

    def to_dict(self):
        counts = Counter(v.constraint_type for v in self.violations)
        return {"feasible": self.feasible,
                "violation_breakdown": {f"H{i}": counts[f"H{i}"] for i in range(1, 7)},
                "violations": [asdict(v) for v in self.violations]}


def plan_pairs(plan):
    if isinstance(plan, ExpertReferencePlan):
        return [(a.operation_id, a.station_id) for a in plan.assignments]
    if isinstance(plan, Mapping):
        return list(plan.items())
    return list(plan)


class HardConstraintEngine:
    def __init__(self, context: ConstraintContext):
        self.context = context

    def evaluate(self, plan):
        context = self.context
        ops, stations = context.operations, context.stations
        violations, assigned = [], defaultdict(list)

        def fail(kind, op_ids, station_ids, message, **details):
            violations.append(ConstraintViolation(kind, tuple(op_ids), tuple(station_ids), message, details))

        for entry in plan_pairs(plan):
            if not isinstance(entry, (tuple, list)) or len(entry) != 2 or not isinstance(entry[0], str):
                fail("H1", (), (), "Malformed assignment; expected (operation_id, station_id)", assignment=repr(entry))
                continue
            op_id, station_id = entry
            assigned[op_id].append(station_id)
        for op_id in sorted(set(ops) | set(assigned)):
            values = assigned.get(op_id, [])
            printable = [v if isinstance(v, str) or v is None else repr(v) for v in values]
            if op_id not in ops:
                fail("H1", (op_id,), printable, "Plan references an unknown Operation", reason="unknown_operation")
            if op_id in ops and len(values) != 1:
                fail("H1", (op_id,), printable, "Each Operation must be assigned exactly once", assignment_count=len(values))
            for station_id in values:
                if not isinstance(station_id, str) or station_id not in stations:
                    fail("H1", (op_id,), (station_id if isinstance(station_id, str) or station_id is None else repr(station_id),),
                         "Assigned Station does not exist", reason="invalid_station")
                elif op_id in ops and ops[op_id].operation_type not in stations[station_id].allowed_operation_types:
                    fail("H2", (op_id,), (station_id,), "Station capability does not allow this OperationType",
                         operation_type=ops[op_id].operation_type,
                         allowed_operation_types=sorted(stations[station_id].allowed_operation_types))
        usable = {op_id: values[0] for op_id, values in assigned.items()
                  if op_id in ops and len(values) == 1 and isinstance(values[0], str) and values[0] in stations}
        for relation in context.precedence_relations:
            a, b = relation.predecessor_id, relation.operation_id
            if a in usable and b in usable:
                sa, sb = stations[usable[a]], stations[usable[b]]
                if sa.order >= sb.order:
                    fail(relation.constraint_type, (a, b), (sa.station_id, sb.station_id),
                         "Predecessor must occupy a strictly earlier Station", relation_source=relation.relation_source,
                         origin_ref=relation.origin_ref, predecessor_order=sa.order, operation_order=sb.order)
        for fixed in context.fixed_assignments:
            if fixed.operation_id in usable and usable[fixed.operation_id] != fixed.station_id:
                fail("H5", (fixed.operation_id,), (usable[fixed.operation_id], fixed.station_id),
                     "TRIM assignment must match Sheet4", fixed_station=fixed.station_id)
        return HardConstraintResult(not violations, tuple(violations))

    def require_feasible(self, plan):
        pairs = plan_pairs(plan)
        result = self.evaluate(pairs)
        if not result.feasible:
            raise ValueError(f"Only hard-feasible Plans can be analyzed here: {result.to_dict()}")
        return dict(pairs)
